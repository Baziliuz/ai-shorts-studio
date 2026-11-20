import sqlite3

import pytest

from ai_shorts_studio.domain.model import Clip
from ai_shorts_studio.jobs.export import export_clips
from ai_shorts_studio.media.probe import binary, probe, run
from ai_shorts_studio.media.render import Cancelled, Renderer
from ai_shorts_studio.storage.project import Store


def test_repeat_export_preserves_previous_result_on_cancellation_and_restart(tmp_path):
    source = tmp_path / "Запись игры.mkv"
    run([binary("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=1",
         "-c:v", "libx264", str(source)])
    db_path = tmp_path / "Проект.sqlite"
    folder = tmp_path / "Нарезки"
    folder.mkdir()
    metadata = probe(source)
    clip = Clip("Первый момент", 0, 700_000)

    previous, = export_clips(db_path, source, metadata, [clip], folder, Renderer(), lambda _: None, "libx264")
    old_bytes = previous.video.read_bytes()
    renderer = Renderer()
    with pytest.raises(Cancelled):
        export_clips(db_path, source, metadata, [clip], folder, renderer, lambda _: renderer.cancel(), "libx264")

    reopened = Store(db_path)
    try:
        assert reopened.current_export(clip.id) == previous
        assert list(folder.iterdir()) == [previous.video.parent]
    finally:
        reopened.close()

    current, = export_clips(db_path, source, metadata, [clip], folder, Renderer(), lambda _: None, "libx264")
    reopened = Store(db_path)
    try:
        assert reopened.current_export(clip.id) == current
        assert current.video != previous.video
        assert previous.video.read_bytes() == old_bytes
    finally:
        reopened.close()


def test_migrate_completed_export_and_rollback_interrupted_confirmation(tmp_path):
    path = tmp_path / "previous.sqlite"
    with sqlite3.connect(path) as legacy:
        legacy.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, status TEXT, output TEXT)")
        legacy.execute("INSERT INTO jobs VALUES ('clip','completed',?)", (str(tmp_path / "previous.mp4"),))
        legacy.execute("PRAGMA user_version=2")
    legacy.close()

    store = Store(path)
    previous = store.current_export("clip")
    assert previous.video == tmp_path / "previous.mp4"
    assert path.with_name(path.name + ".v2-backup.sqlite").exists()

    def cancel():
        raise Cancelled()

    from ai_shorts_studio.storage.project import ExportResult
    with pytest.raises(Cancelled):
        store.confirm_export(ExportResult("attempt", "clip", tmp_path / "next.mp4"), cancel)
    assert store.current_export("clip") == previous
    assert store.db.execute("SELECT COUNT(*) FROM exports").fetchone()[0] == 1
    store.close()
