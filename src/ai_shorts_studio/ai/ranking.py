"""Deterministic evidence ranking, deliberately separate from speech recognition.
Scores are heuristic signals, not event/emotion/virality recognition.
"""
from bisect import bisect_left, bisect_right
from itertools import accumulate
import re
from uuid import NAMESPACE_URL, uuid4, uuid5

CUES=re.compile(r'ничего себе|неожидан|получилось|смотри|важн|ошибк|секрет|почему|побед|офиг|wow|amazing|finally|look|watch|mistake|important',re.I)

def candidates(segments,signals,duration_us,target_us=30_000_000,min_us=10_000_000,max_us=30_000_000,limit=10):
    if not 0 < min_us <= target_us <= max_us or not 1 <= limit <= 30:
        raise ValueError('Проверьте минимальную, целевую и максимальную длину клипа')
    anchors=[]
    for s in segments:
        if not 0<=s['start_us']<s['end_us']<=duration_us: raise ValueError('Некорректный сегмент речи')
        cue=CUES.search(s['text'])
        anchors.append((s['start_us'],35 if cue else 0, f'Слово/фраза в речи: «{cue.group()}»' if cue else None))
    anchors += [(s['time_us'],0,None) for s in signals if s.get('loudness',0)>=.6 or s.get('motion',0)>=.6]
    proposals=[]
    for anchor,cue_score,cue in anchors:
        if not 0<=anchor<duration_us: continue
        start=max(0,anchor-5_000_000)
        end=min(duration_us,start+target_us)
        if end-start < min_us: start=max(0,end-target_us)
        # Prefer a real utterance ending near the target while keeping strict limits.
        endings=[s['end_us'] for s in segments if start+min_us<=s['end_us']<=min(duration_us,start+max_us)]
        if endings: end=min(endings,key=lambda t:abs(t-(start+target_us)))
        if not min_us<=end-start<=max_us: continue
        speech=[s for s in segments if s['start_us']<end and s['end_us']>start]
        nearby=[s for s in signals if abs(s['time_us']-anchor)<=3_000_000]
        loud=max((s.get('loudness',0) for s in nearby),default=0)
        motion=max((s.get('motion',0) for s in nearby),default=0)
        motion_weight=20 if speech else 35
        score=min(100,round(cue_score+(15 if speech else 0)+30*loud+motion_weight*motion))
        if score<30: continue
        evidence=([cue] if cue else [])
        if speech: evidence.append('Есть распознанная речь')
        if loud>=.6: evidence.append('Повышение громкости относительно этой записи')
        if motion>=.6: evidence.append('Повышенная разница кадров (1 кадр/сек.)')
        text=' '.join(s['text'].strip() for s in speech).strip()
        # A title is a quote from real segments, never a generated event claim.
        title=(text[:72]+'…' if len(text)>72 else text) or f'Фрагмент {anchor//1_000_000} сек.'
        proposals.append(dict(id=uuid4().hex,name=title,start_us=start,end_us=end,score=score,
            segment_ids=[s['id'] for s in speech],evidence=evidence,
            explanation='Эвристическая оценка по измеримым сигналам; проверьте контекст.',
            text=text[:1200],status='pending'))
    selected=[]
    for item in sorted(proposals,key=lambda c:c['score'],reverse=True):
        if any(max(0,min(item['end_us'],other['end_us'])-max(item['start_us'],other['start_us'])) /
               min(item['end_us']-item['start_us'],other['end_us']-other['start_us'])>.5 for other in selected): continue
        selected.append(item)
        if len(selected)==limit: break
    return selected


SECOND = 1_000_000
CONTENT_RANKING_VERSION = 1
WORDS = re.compile(r'[^\W\d_]+', re.UNICODE)
END_OF_SENTENCE = re.compile(r'[.!?…][»”\")\]]*$')
QUESTION_END = re.compile(r'\?[»”\")\]]*$')
SENTENCE_BREAK = re.compile(r'[.!?…][»”\")\]]*(?=\s|$)')
TOPIC_SWITCH = re.compile(
    r'\b(?:as for|moving on|with that out of the way|let(?:\'s| me) (?:talk about|start with|show you)|'
    r'(?:now|next),? (?:let\'s|look at|we\'ll)|what about|so over at|'
    r'перейдём к|переходим к|теперь о|дальше посмотрим|давайте посмотрим на)\b', re.I)
TEASER = re.compile(r'^(?:that\'s what we(?:\'re| are) going to find out|we\'ll find out|'
                    r'узнаем (?:дальше|чуть позже|в этом видео))\b', re.I)
DEPENDENT_HOOK = re.compile(r'^(?:and|or|so|then|и|или|а|так что|тогда)\b', re.I)
CONTINUATION = re.compile(
    r'^(?:а\s+(?:ещё|потом|вот|здесь|там|этот|эта|эти)|но|зато|поэтому|потому что|'
    r'например|то есть|во-вторых|с другой стороны|при этом|однако|в итоге|'
    r'but|because|therefore|for example|on the other hand|however|which means)\b', re.I)
REASONING = re.compile(
    r'\b(?:потому что|поэтому|например|в отличие от|вместо|по сравнению|зато|'
    r'однако|чем у|на практике|в итоге|because|therefore|for example|'
    r'compared with|compared to|whereas|unlike|however|in practice)\b', re.I)
REACTION = re.compile(r'ничего себе|неожидан|получилось|побед|офиг|wow|amazing|finally', re.I)
TOPIC_STOP_WORDS = set('котор что так этот если очень здесь потом тогда смотрите теперь '
                       'потому поэтому значит просто вообще это правда thing really about '
                       'there their going which these those where could should would'.split())


def _words(text):
    return WORDS.findall(text.casefold())


def _topic_words(text):
    return {word[:5] for word in _words(text) if len(word) >= 5 and word not in TOPIC_STOP_WORDS}


def _reading_spans(text, topic_switches=False):
    boundaries = {0, len(text), *(mark.end() for mark in SENTENCE_BREAK.finditer(text))}
    if topic_switches:
        boundaries.update(mark.start() for mark in TOPIC_SWITCH.finditer(text))
    boundaries = sorted(boundaries)
    for start, end in zip(boundaries, boundaries[1:]):
        phrase = text[start:end]
        if phrase.strip():
            yield start + len(phrase) - len(phrase.lstrip()), start + len(phrase.rstrip())


def _speech_pieces(segments):
    """Split on transcript boundaries and preserve links back to the source ASR segment."""
    for segment in segments:
        text = segment['text'].strip()
        spans = list(_reading_spans(text, topic_switches=True))
        if len(spans) <= 1:
            yield segment
            continue
        start, end = segment['start_us'], segment['end_us']
        words = segment.get('words', [])
        word_text = ''.join(word[2] for word in words)
        if word_text.strip() == text:
            leading_space = len(word_text) - len(word_text.lstrip())
            word_ends = list(accumulate(len(word[2]) for word in words))
        else:
            word_ends = []
        for left, right in spans:
            if word_ends:
                first = bisect_right(word_ends, left + leading_space)
                last = bisect_left(word_ends, right + leading_space)
                start_us, end_us = words[first][0], words[last][1]
            else:
                # Projects cached by earlier builds only have whole-segment timestamps.
                start_us = start + (end - start) * left // len(text)
                end_us = start + (end - start) * right // len(text)
            if start_us < end_us:
                yield dict(id=segment['id'], start_us=start_us, end_us=end_us, text=text[left:right])


def _speech_groups(segments):
    group = []
    for segment in segments:
        if group:
            previous = group[-1]
            gap = segment['start_us'] - previous['end_us']
            before, after = previous['text'].strip(), segment['text'].strip()
            same_idea = len(_topic_words(before) & _topic_words(after)) >= 2
            connects = not TOPIC_SWITCH.match(after) and (
                gap < 0 or (gap <= 6 * SECOND and QUESTION_END.search(before))
                or (gap <= 3 * SECOND and CONTINUATION.match(after))
                or (gap <= SECOND and (not END_OF_SENTENCE.search(before) or same_idea)))
            if not connects:
                yield group
                group = []
        group.append(segment)
    if group:
        yield group


def _hook(segments):
    phrases = []
    for segment in segments:
        for left, right in _reading_spans(segment['text']):
            phrase = ' '.join(segment['text'][left:right].split())
            words = _words(phrase)
            topics = _topic_words(phrase)
            if (5 <= len(words) <= 12 and len(phrase) <= 76 and len(topics) >= 2
                    and END_OF_SENTENCE.search(phrase)
                    and not (QUESTION_END.search(phrase) or CONTINUATION.match(phrase)
                             or DEPENDENT_HOOK.match(phrase))):
                phrases.append((int(bool(REASONING.search(phrase))), len(topics), phrase))
    return max(phrases, default=(0, 0, ''), key=lambda item: item[:2])[2]


def _proposal(start, end, anchor, segments, evidence, score, explanation, source_id):
    text = ' '.join(s['text'].strip() for s in segments)
    title = (text[:72] + '…' if len(text) > 72 else text) or f'Фрагмент {anchor // SECOND} сек.'
    segment_ids = list(dict.fromkeys(s['id'] for s in segments))
    identity = f'ai-shorts-studio/content-v{CONTENT_RANKING_VERSION}/{source_id}/{start}:{end}'
    return dict(id=uuid5(NAMESPACE_URL, identity).hex, name=title, start_us=start, end_us=end,
                score=min(100, round(score)), segment_ids=segment_ids, evidence=evidence,
                explanation=explanation, text=text[:1200], hook=_hook(segments),
                anchor_us=min(max(anchor, start), end - 1), status='pending')


def _speech_proposals(segments, source_id):
    for group in _speech_groups(_speech_pieces(segments)):
        text = ' '.join(s['text'].strip() for s in group)
        words = _words(text)
        if QUESTION_END.search(text) or len(words) < 7 or len(_topic_words(text)) < 3:
            continue
        after_question = text.rsplit('?', 1)[1].strip() if '?' in text else ''
        if TEASER.match(after_question or text):
            continue
        answered = len(_words(after_question)) >= 5
        reasoned = bool(REASONING.search(text))
        complete = bool(END_OF_SENTENCE.search(text))
        if not (answered or reasoned or (complete and (len(words) >= 16 or CUES.search(text)))):
            continue
        evidence = ['Есть распознанная речь']
        if answered:
            evidence.append('Вопрос и последующая реплика')
        if reasoned:
            evidence.append('В речи есть объяснение, вывод или сравнение')
        if complete:
            evidence.append('Граница по окончанию реплики')
        anchor_segment = max(group, key=lambda s: (bool(REASONING.search(s['text'])), len(s['text'])))
        anchor = (anchor_segment['start_us'] + anchor_segment['end_us']) // 2
        score = 40 + 14 * answered + 14 * reasoned + min(18, len(words) / 3)
        yield _proposal(group[0]['start_us'], max(s['end_us'] for s in group), anchor, group,
                        evidence, score, 'Границы выбраны по репликам, паузам и связкам речи.', source_id)


def _activity_runs(segments, signals):
    points = [(s['time_us'], s.get('loudness', 0), s.get('motion', 0), '') for s in signals
              if max(s.get('loudness', 0), s.get('motion', 0)) >= .35]
    for segment in segments:
        cue = REACTION.search(segment['text'])
        if cue:
            points.append((segment['start_us'], 0, 0, cue.group()))
    run = []
    for point in sorted(points):
        if run and point[0] - run[-1][0] > 2 * SECOND:
            yield run
            run = []
        run.append(point)
    if run:
        yield run


def _event_proposals(segments, signals, duration_us, profile, source_id):
    starts = [s['start_us'] for s in segments]
    ends = list(accumulate((s['end_us'] for s in segments), max))
    for run in _activity_runs(segments, signals):
        anchor, _, _, _ = max(run, key=lambda p: (max(p[1], p[2]) + .4 * min(p[1], p[2]), bool(p[3])))
        loud = max(point[1] for point in run)
        motion = max(point[2] for point in run)
        cue = next((point[3] for point in run if point[3]), '')
        strong = max(loud, motion)
        combined = min(loud, motion)
        if not (strong >= (.78 if profile == 'game' else .88)
                or (strong >= .65 and (combined >= .52 or cue))
                or (profile == 'game' and cue)):
            continue
        left = max(0, run[0][0] - 4 * SECOND)
        right = min(duration_us, run[-1][0] + 5 * SECOND)
        speech = segments[bisect_left(ends, left):bisect_right(starts, right)]
        start = min(left, speech[0]['start_us']) if speech else left
        end = max(right, max(s['end_us'] for s in speech)) if speech else right
        evidence = []
        if loud >= .6:
            evidence.append('Повышение громкости относительно этой записи')
        if motion >= .6:
            evidence.append('Повышенная разница кадров (1 кадр/сек.)')
        if cue:
            evidence.append(f'Реплика реакции: «{cue}»')
        score = 28 + 36 * strong + 22 * combined + 9 * bool(cue) + 5 * bool(speech)
        yield _proposal(start, end, anchor, speech, evidence, score,
                        'Границы охватывают подводку, изменение сигнала и время для реакции.', source_id)


def content_candidates(segments, signals, duration_us, profile='auto', source_id='', *, automatic=False):
    """Rank variable-length speech and activity intervals with evidence from this source."""
    if profile not in {'auto', 'game', 'conversation'}:
        raise ValueError('Неизвестный профиль анализа')
    segments = sorted(segments, key=lambda s: s['start_us'])
    if any(not 0 <= s['start_us'] < s['end_us'] <= duration_us for s in segments):
        raise ValueError('Некорректный сегмент речи')
    signals = [s for s in signals if 0 <= s['time_us'] < duration_us]
    proposals = []
    if profile != 'game':
        proposals.extend(_speech_proposals(segments, source_id))
    speech_time = 0
    covered_until = 0
    for segment in segments:
        end = segment['end_us']
        speech_time += max(0, end - max(covered_until, segment['start_us']))
        covered_until = max(end, covered_until)
    dense_conversation = profile == 'auto' and len(proposals) >= 2 and speech_time >= .65 * duration_us
    if not proposals or (profile != 'conversation' and not dense_conversation):
        proposals.extend(_event_proposals(segments, signals, duration_us, profile, source_id))
    selected = []
    for item in sorted(proposals, key=lambda c: (-c['score'], c['start_us'], c['end_us'])):
        for other in selected:
            overlap = min(item['end_us'], other['end_us']) - max(item['start_us'], other['start_us'])
            shorter = min(item['end_us'] - item['start_us'], other['end_us'] - other['start_us'])
            if overlap * 2 > shorter:
                break
        else:
            selected.append(item)
    if automatic and selected:
        # A long session can yield many review suggestions. Limit the number of automatic
        # exports by source length and evidence, leaving each selected clip's boundaries intact.
        per_clip = 150 * SECOND
        count = max(3, min(12, (duration_us + per_clip - 1) // per_clip))
        minimum_score = min(65, max(45, selected[0]['score'] - 17))
        return [item for item in selected if item['score'] >= minimum_score][:count]
    return selected
