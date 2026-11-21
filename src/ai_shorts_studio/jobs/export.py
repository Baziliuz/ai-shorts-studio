"""Publish verified exports without overwriting an earlier result."""

import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from ai_shorts_studio.media.render import Cancelled, choose_encoder
from ai_shorts_studio.storage.project import ExportResult, Store


@dataclass(frozen=True)
class CoverRequest:
    hook: str
    source_start_us: int
    frame_us: int | None = None
    channels: bool = False


def check_cancelled(renderer):
    if renderer.cancelled.is_set():
        raise Cancelled()


def export_clips(db_path, source, metadata, clips, folder, renderer, progress, preference="auto", *, cover_hooks=None, channels=False):
    folder = Path(folder).resolve()
    check_cancelled(renderer)
    folder.mkdir(parents=True, exist_ok=True)
    encoder = choose_encoder(preference)
    renderer.on_status(f"Экспорт · {'NVIDIA NVENC' if encoder == 'h264_nvenc' else 'libx264'}")
    results = []
    store = Store(db_path, recover=False)
    try:
        for index, clip in enumerate(clips):
            check_cancelled(renderer)
            cover = None
            if cover_hooks is not None:
                previous = store.current_export(clip.id)
                hook = clip.plan_hook or cover_hooks.get(clip.id, "")
                frame_us = None
                if clip.plan_cover_us is not None and clip.start_us <= clip.plan_cover_us < clip.end_us:
                    frame_us = clip.plan_cover_us - clip.start_us
                if previous and previous.cover is not None:
                    hook = previous.hook
                    source_frame_us = previous.source_start_us + previous.frame_us
                    if clip.start_us <= source_frame_us < clip.end_us:
                        frame_us = source_frame_us - clip.start_us
                cover = CoverRequest(hook, clip.start_us, frame_us, channels)
            result = export_clip(
                store, source, metadata, clip, folder, renderer, encoder,
                lambda value: progress((index + value) / len(clips)), cover,
            )
            results.append(result)
    finally:
        store.close()
    return results


def export_clip(store, source, metadata, clip, folder, renderer, encoder, progress, cover):
    def write_video(path):
        renderer.render(source, clip, metadata, path, encoder,
                        lambda value: progress(value * (.89 if cover is not None else .98)))

    result = publish_export(store, clip, folder, renderer, write_video, cover)
    progress(1)
    return result


def revise_cover(db_path, clip, previous, frame_us, hook, renderer, progress, channels=False):
    def copy_video(path):
        with previous.video.open("rb") as original, path.open("xb") as target:
            while block := original.read(4 * 1024 * 1024):
                check_cancelled(renderer)
                target.write(block)
        progress(.75)

    store = Store(db_path, recover=False)
    try:
        cover = CoverRequest(hook, previous.source_start_us if previous.source_start_us is not None else clip.start_us,
                             frame_us, channels)
        folder = previous.video.parent
        if not previous.attempt_id.startswith("legacy:"):
            folder = folder.parent
        result = publish_export(store, clip, folder,
                                renderer, copy_video, cover, replace_attempt=previous.attempt_id)
    finally:
        store.close()
    progress(1)
    return result


def publish_export(store, clip, folder, renderer, write_video, cover, replace_attempt=None):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", clip.name).strip(" .")[:48] or "Клип"
    attempt_id = uuid4().hex
    number = store.clip_numbers([clip])[clip.id]
    stem = f"{number:03d}_{name}"
    final = folder / f"{stem}_{attempt_id[:12]}"
    video_name = f"{stem}.mp4"
    with TemporaryDirectory(prefix=".shorts-", dir=folder, ignore_cleanup_errors=True) as temporary:
        staging = Path(temporary) / "export"
        staging.mkdir()
        write_video(staging / video_name)
        check_cancelled(renderer)
        result = ExportResult(attempt_id, clip.id, final / video_name)
        if cover is not None:
            from ai_shorts_studio.media.cover import render_cover

            image_name = f"{stem}.jpg"
            frame_us = render_cover(staging / video_name, staging / image_name, hook=cover.hook, channels=cover.channels,
                                    frame_us=cover.frame_us, check_cancelled=lambda: check_cancelled(renderer))
            result = ExportResult(attempt_id, clip.id, final / video_name, final / image_name,
                                  frame_us, cover.hook, cover.source_start_us)
        check_cancelled(renderer)
        # The directory and destination share a filesystem. Moving the directory
        # exposes the validated files together while the previous result remains intact.
        if final.exists():
            raise FileExistsError(f"Папка экспорта уже существует: {final}")
        staging.rename(final)
        try:
            store.confirm_export(result, lambda: check_cancelled(renderer), replace_attempt)
        except Exception:
            shutil.rmtree(final, ignore_errors=True)
            raise
    return result
