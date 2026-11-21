import json
import os
from threading import Event
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from PySide6.QtWidgets import QApplication

from ai_shorts_studio.domain.model import Project
from ai_shorts_studio.jobs.chat_exchange import parse_plan, write_package
from ai_shorts_studio.media.probe import binary, probe, run


def test_exchange_roundtrip(tmp_path):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    app = QApplication.instance() or QApplication([])
    video = tmp_path / 'source.mp4'
    run([binary('ffmpeg'), '-v', 'error', '-f', 'lavfi', '-i', 'color=s=80x48:r=1:d=40', str(video)])
    metadata = probe(video)
    project = Project(str(video), 'source', metadata)
    path = tmp_path / 'review.zip'
    package, index = write_package(path, dict(metadata=metadata, source=str(video),
        fingerprint='source', audio_index=None, language='ru'),
        dict(segments=[dict(start_us=0,end_us=31_000_000,text='Тест')], signals=[], speech_enabled=True),
        SimpleNamespace(cancelled=Event()), lambda _: None)
    project.exchange_indexes[package] = index
    with ZipFile(path) as archive:
        assert 'frames-001.jpg' in archive.namelist()
        assert 'S000001' in archive.read('transcript.txt').decode()
        plan = json.loads(archive.read('cuts.example.json'))
    assert parse_plan(plan, project)[0]['end_us'] == 30_000_000
    row = dict(title='Тема', start_segment='S000001', end_segment='S000001')
    plan['clips'] = [row, row]
    assert len(parse_plan(plan, Project.load(project.serialize()))) == 1
    plan['clips'].append(dict(title='Короткий', start_us=0, end_us=1))
    with pytest.raises(ValueError, match='Клип 3'):
        parse_plan(plan, project)
