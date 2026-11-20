"""Select and render an offline cover from the composed, verified MP4."""

import math
from pathlib import Path
import subprocess

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QTextLayout, QTextOption

from ai_shorts_studio.media.probe import binary, probe


HOOK_MAX_LENGTH = 96


def last_frame_us(metadata):
    duration_us = metadata.get('video_duration_us') or metadata['duration_us']
    return max(0, duration_us - math.ceil(1_000_000 / metadata['fps']))


def read_frame(video_path, frame_us, *, preview_size=None):
    seconds, micros = divmod(frame_us, 1_000_000)
    args = [binary('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error',
            '-ss', f'{seconds}.{micros:06d}', '-i', str(video_path),
            '-map', '0:v:0', '-frames:v', '1']
    if preview_size is not None:
        args += ['-vf', f'scale={preview_size}:{preview_size}:force_original_aspect_ratio=decrease']
    args += ['-c:v', 'png', '-threads', '1', '-f', 'image2pipe', 'pipe:1']
    try:
        result = subprocess.run(args, capture_output=True, timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    except subprocess.TimeoutExpired as error:
        raise RuntimeError('Не удалось извлечь кадр обложки за 15 секунд') from error
    image = QImage.fromData(result.stdout, 'PNG')
    if result.returncode or image.isNull():
        detail = result.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError('Не удалось извлечь кадр обложки' + (f'\n{detail[-1000:]}' if detail else ''))
    return image


def _frame_score(image):
    gray = image.scaled(100, 100, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_Grayscale8)
    pixels = gray.constBits().cast('B')
    stride = gray.bytesPerLine()
    total = contrast = sharpness = clipped = 0
    for y in range(1, gray.height() - 1):
        for x in range(1, gray.width() - 1):
            i = y * stride + x
            value = pixels[i]
            total += value
            contrast += value * value
            sharpness += abs(4 * value - pixels[i - 1] - pixels[i + 1] - pixels[i - stride] - pixels[i + stride])
            clipped += value < 12 or value > 243
    count = max(1, (gray.width() - 2) * (gray.height() - 2))
    mean = total / count
    return (sharpness / count + .25 * math.sqrt(max(0, contrast / count - mean * mean))
            - 18 * clipped / count - .12 * abs(mean - 125))


def _choose_frame(video_path, last_us, check_cancelled):
    positions = dict.fromkeys(last_us * percent // 100 for percent in (50, 20, 80, 35, 65))
    best_us, best_score = 0, -math.inf
    for position in positions:
        check_cancelled()
        try:
            image = read_frame(video_path, position, preview_size=240)
        except RuntimeError:
            continue
        score = _frame_score(image)
        if score > best_score:
            best_us, best_score = position, score
    return best_us


def _layout_hook(hook, font, width):
    layout = QTextLayout(hook, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    height = 0
    while (line := layout.createLine()).isValid():
        line.setLineWidth(width)
        line.setPosition(QPointF(0, height))
        height += line.height()
    layout.endLayout()
    return layout


def compose_cover(image, hook):
    hook = ' '.join(hook.split())[:HOOK_MAX_LENGTH]
    if not hook:
        return image

    result = image.convertToFormat(QImage.Format.Format_RGB32)
    short_side = min(result.width(), result.height())
    margin = max(1, round(short_side * .045))
    padding = max(1, round(short_side * .037))
    font = QFont('Sans Serif')
    font.setBold(True)
    font.setPixelSize(max(1, round(short_side * .066)))
    layout = _layout_hook(hook, font, result.width() - 2 * (margin + padding))
    text_height = layout.boundingRect().height()
    band_y = result.height() - margin - text_height - 2 * padding
    band = QRectF(margin, band_y, result.width() - 2 * margin, text_height + 2 * padding)

    painter = QPainter(result)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(14, 18, 27, 218))
        painter.drawRoundedRect(band, padding / 2, padding / 2)
        painter.setPen(QColor('white'))
        layout.draw(painter, QPointF(margin + padding, band_y + padding))
    finally:
        painter.end()
    return result


def render_cover(video_path, destination, *, hook='', frame_us=None, metadata=None, check_cancelled=lambda: None):
    """Write a JPEG to a caller-owned staging path and return the clip-relative frame position."""
    metadata = probe(video_path) if metadata is None else metadata
    last_us = last_frame_us(metadata)
    if frame_us is None:
        frame_us = _choose_frame(video_path, last_us, check_cancelled)
    else:
        frame_us = min(last_us, max(0, frame_us))
    check_cancelled()
    image = compose_cover(read_frame(video_path, frame_us), hook)
    check_cancelled()
    if not image.save(str(Path(destination)), 'JPEG', 92):
        raise RuntimeError('Не удалось сохранить JPG-обложку')
    return frame_us
