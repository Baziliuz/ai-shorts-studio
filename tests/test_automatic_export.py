import os
from pathlib import Path
from threading import Event
from time import monotonic

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton

from ai_shorts_studio.ai import runner
from ai_shorts_studio.domain.model import Clip, Project
from ai_shorts_studio.media.probe import binary, fingerprint, probe, run
from ai_shorts_studio.storage.project import Store
from ai_shorts_studio.ui.analysis import AnalysisDialog, PublicationDialog
from ai_shorts_studio.ui.window import Window


MOMENTS = [
    ('Первый момент', 100_000, 500_000, 'Первый хук'),
    ('Второй момент', 1_000_000, 1_700_000, 'Второй хук'),
]


def wait_for_tasks(window):
    deadline = monotonic() + 60
    while window.busy() and monotonic() < deadline:
        QTest.qWait(20)
    assert not window.busy(), 'Автоматическая нарезка не завершилась'


def test_create_shorts_exports_both_formats_and_reuses_editor_clips(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    data = tmp_path / 'Данные'
    cache = tmp_path / 'Кэш'
    data.mkdir()
    cache.mkdir()
    source = tmp_path / 'Игровая запись без звука.mkv'
    run([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=10:d=3',
         '-c:v', 'libx264', str(source)])
    db_path = data / 'Проект.sqlite'
    source_id = fingerprint(source)

    class AnalysisStub:
        def __init__(self):
            self.cancelled = Event()
            self.on_status = lambda message: None

        def cancel(self):
            self.cancelled.set()

        def analyze(self, request, progress):
            assert request['audio_index'] is None
            assert Path(request['source']) == source
            assert request['fingerprint'] == source_id
            self.on_status('Поиск моментов по движению')
            progress(1)
            return dict(segment_count=0, candidates=[
                dict(id=str(index), name=name, start_us=start, end_us=end, hook=hook,
                     score=80, status='pending', evidence=['Движение'])
                for index, (name, start, end, hook) in enumerate(MOMENTS)
            ])

    formats = iter(('9:16', '16:9', '9:16'))

    def select_format(dialog):
        dialog.format.setCurrentIndex(dialog.format.findData(next(formats)))
        return QDialog.DialogCode.Accepted

    def rate(dialog):
        dialog.rating.setCurrentIndex(dialog.rating.findData('publishable'))
        dialog.comment.setPlainText('Хорошие границы')
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(runner, 'AnalysisRunner', AnalysisStub)
    monkeypatch.setattr(AnalysisDialog, 'exec', select_format)
    monkeypatch.setattr(PublicationDialog, 'exec', rate)

    window = Window(data, cache)
    messages = []
    monkeypatch.setattr(window, 'message', lambda text: messages.append(str(text)))
    initial_clip = Clip('Ручной клип', 0, 500_000, fps=10)
    window.set_project(Store(db_path), Project(str(source), source_id, probe(source), [initial_clip]))
    buttons = {button.text(): button for button in window.findChildren(QPushButton)}

    try:
        previous = {}
        for orientation, size, expected_count in [
            ('9:16', (1080, 1920), 3),
            ('16:9', (1920, 1080), 5),
            ('9:16', (1080, 1920), 5),
        ]:
            messages.clear()
            buttons['Создать нарезку'].click()
            assert window.busy()
            wait_for_tasks(window)
            assert len(messages) == 1 and messages[0].startswith('Комплектов MP4 и JPG: 2'), messages

            store = Store(db_path)
            try:
                saved = store.load()
                assert len(saved.clips) == window.clips.count() == expected_count
                assert saved.clips[0] == initial_clip
                for proposal, (name, start, end, hook) in zip(saved.suggestions, MOMENTS, strict=True):
                    assert proposal['status'] == 'accepted'
                    clip_id = proposal['auto_clips'][orientation]
                    clip = next(item for item in saved.clips if item.id == clip_id)
                    assert (clip.start_us, clip.end_us) == (start, end)
                    assert clip.name == name
                    result = store.current_export(clip_id)
                    folder = data / 'Проект — Нарезка'
                    assert result.video.parent.parent == folder
                    assert result.cover.parent == result.video.parent
                    assert (result.video.suffix, result.cover.suffix) == ('.mp4', '.jpg')
                    assert (result.source_start_us, result.hook) == (start, hook)
                    video = probe(result.video)
                    assert (video['width'], video['height']) == size
                    assert video['video_codec'] == 'h264' and not video['audio']
                    assert video['duration_us'] == pytest.approx(end - start, abs=100_000)
                    cover = QImage(str(result.cover))
                    assert (cover.width(), cover.height()) == size
                    key = orientation, start, end
                    if key in previous:
                        before = previous[key]
                        assert result.clip_id == before.clip_id
                        assert result.attempt_id != before.attempt_id
                        assert before.video.is_file() and before.cover.is_file()
                    previous[key] = result
            finally:
                store.close()

            if expected_count == 3:
                first_id = previous[('9:16', MOMENTS[0][1], MOMENTS[0][2])].clip_id
                row = next(i for i, clip in enumerate(window.project.clips) if clip.id == first_id)
                window.clips.setCurrentRow(row)
                buttons['Оценить момент'].click()

        store = Store(db_path)
        try:
            assert store.publication_rating(first_id) == ('publishable', 'Хорошие границы')
        finally:
            store.close()
    finally:
        if window.busy():
            window.cancel()
            window.worker.wait(10_000)
            app.processEvents()
        window.close()
