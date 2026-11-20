import os
from contextlib import closing

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QDialog

from ai_shorts_studio.domain.model import Clip
from ai_shorts_studio.jobs.export import export_clips, revise_cover
from ai_shorts_studio.media.cover import render_cover
from ai_shorts_studio.media.probe import binary, probe, run
from ai_shorts_studio.media.render import Renderer
from ai_shorts_studio.storage.project import Store
from ai_shorts_studio.ui.cover import CoverDialog


def make_video(path, width, height):
    size = f'{width}x{height}'
    run([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', f'color=black:s={size}:r=24:d=0.6',
         '-f', 'lavfi', '-i', f'testsrc2=s={size}:r=24:d=1.8',
         '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-c:v', 'libx264', str(path)])


@pytest.mark.parametrize('width,height', [(180, 320), (320, 180)])
def test_cover_chooses_visible_frame_and_keeps_aspect_ratio(tmp_path, width, height):
    app = QApplication.instance() or QApplication([])
    video = tmp_path / 'Готовый клип.mp4'
    make_video(video, width, height)
    cover = tmp_path / 'Пустая обложка.jpg'
    with_hook = tmp_path / 'Обложка с заголовком.jpg'

    frame_us = render_cover(video, cover)
    assert 600_000 < frame_us < 2_400_000
    assert render_cover(video, with_hook, frame_us=frame_us, hook='Как меняется динамика?') == frame_us
    image = QImage(str(cover))
    titled = QImage(str(with_hook))
    assert (image.width(), image.height()) == (width, height)
    assert titled.size() == image.size()
    assert image != titled


def test_cover_editor_changes_frame_and_allows_removing_hook(tmp_path):
    app = QApplication.instance() or QApplication([])
    video = tmp_path / 'Готовый клип.mp4'
    make_video(video, 160, 90)

    dialog = CoverDialog(video, frame_us=880_000, hook='Первый вариант')
    dialog.timecode.set_us(0)
    dialog.hook_text.clear()
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.frame_us == 0
    assert dialog.hook == ''

    cover = tmp_path / 'Выбранный кадр.jpg'
    assert render_cover(video, cover, frame_us=dialog.frame_us, hook=dialog.hook) == 0
    assert QImage(str(cover)).pixelColor(40, 25).value() < 10


def test_revise_cover_publishes_new_bundle_and_keeps_old_files(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    source = tmp_path / 'Исходник.mp4'
    make_video(source, 160, 90)
    project_path = tmp_path / 'Проект.sqlite'
    export_folder = tmp_path / 'Нарезка'
    clip = Clip('Клип', 250_000, 1_250_000, fps=24)
    previous, = export_clips(project_path, source, probe(source), [clip], export_folder, Renderer(),
                             lambda _: None, 'libx264', cover_hooks={clip.id: 'Первый вариант'})
    original_video = previous.video.read_bytes()
    original_cover = previous.cover.read_bytes()

    clip.start_us += 100_000
    current = revise_cover(project_path, clip, previous, 0, 'Новый текст', Renderer(), lambda _: None)
    with closing(Store(project_path)) as store:
        assert store.current_export(clip.id) == current
    assert current.attempt_id != previous.attempt_id
    assert current.video.parent != previous.video.parent
    assert (current.frame_us, current.hook, current.source_start_us) == (0, 'Новый текст', 250_000)
    assert current.video.read_bytes() == previous.video.read_bytes() == original_video
    assert previous.cover.read_bytes() == original_cover
    assert current.cover.read_bytes() != original_cover

    def fail_cover(*args, **kwargs):
        raise RuntimeError('Ошибка записи JPG')

    confirmed_folders = set(export_folder.iterdir())
    current_cover = current.cover.read_bytes()
    monkeypatch.setattr('ai_shorts_studio.media.cover.render_cover', fail_cover)
    with pytest.raises(RuntimeError, match='Ошибка записи JPG'):
        revise_cover(project_path, clip, current, 500_000, 'Не записано', Renderer(), lambda _: None)
    with closing(Store(project_path)) as store:
        assert store.current_export(clip.id) == current
    assert set(export_folder.iterdir()) == confirmed_folders
    assert current.cover.read_bytes() == current_cover
