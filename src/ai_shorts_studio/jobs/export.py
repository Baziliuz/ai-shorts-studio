"""Publish verified exports without overwriting an earlier result."""

import re
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ai_shorts_studio.media.render import Cancelled, choose_encoder
from ai_shorts_studio.storage.project import ExportResult, Store


def check_cancelled(renderer):
    if renderer.cancelled.is_set():
        raise Cancelled()


def export_clips(db_path, source, metadata, clips, folder, renderer, progress, preference="auto"):
    folder = Path(folder).resolve()
    check_cancelled(renderer)
    encoder = choose_encoder(preference)
    renderer.on_status(f"Экспорт · {'NVIDIA NVENC' if encoder == 'h264_nvenc' else 'libx264'}")
    results = []
    store = Store(db_path, recover=False)
    try:
        for index, clip in enumerate(clips):
            check_cancelled(renderer)
            result = export_clip(
                store, source, metadata, clip, folder, renderer, encoder,
                lambda value: progress((index + value) / len(clips)),
            )
            results.append(result)
    finally:
        store.close()
    return results


def export_clip(store, source, metadata, clip, folder, renderer, encoder, progress):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clip.name).strip(" .")[:48] or "Клип"
    attempt_id = uuid4().hex
    stem = f"{name}_{clip.id[:8]}"
    final = folder / f"{stem}_{attempt_id[:12]}"
    result = ExportResult(attempt_id, clip.id, final / f"{stem}.mp4")

    with TemporaryDirectory(prefix=".shorts-", dir=folder) as temporary:
        staging = Path(temporary) / "export"
        staging.mkdir()
        renderer.render(source, clip, metadata, staging / result.video.name, encoder,
                        lambda value: progress(value * .98))
        check_cancelled(renderer)
        # The directory and destination share a filesystem. Moving the directory
        # exposes the validated files together while the previous result remains intact.
        if final.exists():
            raise FileExistsError(f"Папка экспорта уже существует: {final}")
        staging.rename(final)
        try:
            store.confirm_export(result, lambda: check_cancelled(renderer))
        except Cancelled:
            shutil.rmtree(final, ignore_errors=True)
            raise
    progress(1)
    return result
