from ai_shorts_studio.ai.pipeline import cache_key
from ai_shorts_studio.ai.ranking import SECOND, candidates, content_candidates


def speech(identifier, start, end, text):
    return dict(id=identifier, start_us=start * SECOND, end_us=end * SECOND, text=text)


def signal(time, loudness=0, motion=0):
    return dict(time_us=time * SECOND, loudness=loudness, motion=motion)


def test_conversation_keeps_complete_answer_and_comparison_without_duration_presets():
    segments = [
        speech('question', 10, 14, 'Почему эта версия расходует меньше топлива?'),
        speech('answer', 15, 22, 'На трассе дизель потребляет всего шесть литров.'),
        speech('comparison', 23, 42, 'При этом бензиновый двигатель расходовал почти девять литров.'),
        speech('long', 55, 250, 'Поэтому я сравнил результаты трассовых замеров и городских поездок.'),
        speech('greeting', 253, 255, 'Привет всем!'),
        speech('unanswered', 260, 265, 'Насколько надёжен этот двигатель после сотни тысяч километров?'),
    ]
    result = content_candidates(segments, [], 300 * SECOND, 'conversation', 'test-recording')
    assert [(item['start_us'] // SECOND, item['end_us'] // SECOND) for item in result] == [(10, 42), (55, 250)]
    assert result[0]['segment_ids'] == ['question', 'answer', 'comparison']
    assert result[0]['hook'] == segments[1]['text']
    assert content_candidates(segments, [], 300 * SECOND, 'conversation', 'test-recording') == result
    long_only = content_candidates(segments[3:4], [], 300 * SECOND, 'conversation', automatic=True)
    assert long_only[0]['end_us'] - long_only[0]['start_us'] == 195 * SECOND


def test_game_keeps_lead_in_and_reaction_and_deduplicates_nearby_peaks():
    segments = [
        speech('setup', 12, 17, 'Сейчас попробуем открыть последнюю дверь ключом.'),
        speech('reaction', 24, 31, 'Ничего себе, получилось! Дверь наконец открылась.'),
    ]
    signals = [signal(19, .4, .5), signal(20, .82, .62), signal(22, .68, .92), signal(49, 0, 1)]
    moments = content_candidates(segments, signals, 75 * SECOND, 'game')
    assert [(item['start_us'] // SECOND, item['end_us'] // SECOND) for item in moments] == [(12, 31), (45, 54)]
    assert moments[0]['segment_ids'] == ['setup', 'reaction']
    assert moments[0]['anchor_us'] == 22 * SECOND
    assert 'громкости' in moments[0]['evidence'][0]
    assert 'кадров' in moments[0]['evidence'][1]
    assert moments[1]['hook'] == '' and moments[1]['text'] == ''


def test_conversation_uses_real_word_timings_inside_an_asr_segment():
    first = 'The longer wheelbase helps because passengers have more legroom.'
    second = 'As for charging, the battery can recover much faster compared with its predecessor.'
    segment = speech('full', 10, 50, first + ' ' + second)
    first_words = [[i * SECOND, (i + 1) * SECOND, ' ' + word]
                   for i, word in enumerate(first.split(), 10)]
    second_words = [[i * SECOND, (i + 1) * SECOND, ' ' + word]
                    for i, word in enumerate(second.split(), 35)]
    segment['words'] = first_words + second_words
    result = sorted(content_candidates([segment], [], 60 * SECOND, 'conversation'),
                    key=lambda proposal: proposal['start_us'])
    assert [(item['start_us'], item['end_us']) for item in result] == [
        (first_words[0][0], first_words[-1][1]), (second_words[0][0], second_words[-1][1])]
    assert [item['segment_ids'] for item in result] == [['full'], ['full']]


def test_no_proposals_for_flat_signal_and_no_fabricated_hook():
    segments = [speech('react', 5, 6, 'Ничего себе!')]
    points = [signal(second, .1, .15) for second in range(1, 20)]
    assert content_candidates([], points, 20 * SECOND) == []
    result = content_candidates(segments, points, 20 * SECOND, 'game')
    assert len(result) == 1 and result[0]['hook'] == ''


def test_existing_fixed_duration_api_supports_motion_without_audio():
    result = candidates([], [signal(16, motion=1)], 45 * SECOND)
    assert len(result) == 1 and result[0]['end_us'] - result[0]['start_us'] == 30 * SECOND
    assert result[0]['evidence'] == ['Повышенная разница кадров (1 кадр/сек.)']


def test_automatic_output_count_is_limited_without_changing_selected_boundaries():
    points = [signal(t, 1 - i * .03, .85) for i, t in enumerate((14, 30, 46, 62, 78))]
    review = content_candidates([], points, 100 * SECOND, 'game')
    automatic = content_candidates([], points, 100 * SECOND, 'game', automatic=True)
    assert len(review) == 5
    assert automatic == review[:3]


def test_explicit_signal_only_fallback_has_its_own_block_cache():
    request = dict(fingerprint='hash', audio_index=1, language='ru')
    assert cache_key(dict(request, selection='content', profile='game')) == cache_key(request)
    assert cache_key(dict(request, speech_enabled=False)) != cache_key(request)


def test_signal_only_worker_can_use_an_audio_track_without_a_speech_model(tmp_path):
    from ai_shorts_studio.ai.runner import AnalysisRunner
    from ai_shorts_studio.media.probe import binary, fingerprint, probe, run

    source = tmp_path / 'Эпизод.mkv'
    run([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=s=96x64:r=12:d=18',
         '-f', 'lavfi', '-i', 'sine=frequency=500:duration=18',
         '-af', "volume='if(between(t,8,10),1,0)':eval=frame", '-c:v', 'libx264', str(source)])
    metadata = probe(source)
    request = dict(source=str(source), fingerprint=fingerprint(source), metadata=metadata,
                   cache_dir=str(tmp_path / 'cache'), audio_index=metadata['audio'][0]['index'],
                   language=None, speech_enabled=False, selection='content', profile='game')
    result = AnalysisRunner().analyze(request)
    assert result['segment_count'] == 0
    assert result['engine'] == 'Без распознавания речи'
    assert len(result['candidates']) == 1
    assert any('громкости' in reason for reason in result['candidates'][0]['evidence'])
