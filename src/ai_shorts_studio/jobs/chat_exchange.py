"""Offline exchange of source-relative edit decisions."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from ai_shorts_studio.media.render import Cancelled

SECOND = 1_000_000


def stamp(us):
    seconds, fraction = divmod(us, SECOND)
    return f'{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}.{fraction:06}'


def write_package(path, request, analysis, runner, progress):
    from PySide6.QtGui import QImage, QPainter, QColor
    from ai_shorts_studio.media.cover import read_frame

    def check():
        if runner.cancelled.is_set():
            raise Cancelled()

    duration = request['metadata']['duration_us']
    index = {}
    lines = []
    pauses = []
    previous = 0
    for n, segment in enumerate(analysis['segments'], 1):
        key = f'S{n:06}'
        a, b = segment['start_us'], segment['end_us']
        index[key] = [a, b]
        if a - previous >= 3 * SECOND:
            pauses.append([previous, a])
        previous = max(previous, b)
        lines.append(f"{key}  {stamp(a)} → {stamp(b)}  {segment['text']}")
    if duration - previous >= 3 * SECOND:
        pauses.append([previous, duration])
    transcript = '\n'.join(lines)
    package = hashlib.sha256((request['fingerprint'] + transcript).encode()).hexdigest()
    manifest = dict(version=1, source_sha256=request['fingerprint'], package_id=package,
                    duration_us=duration, audio_index=request['audio_index'], language=request['language'],
                    speech_enabled=analysis['speech_enabled'], segment_count=len(index))
    example = dict(version=1, source_sha256=request['fingerprint'], package_id=package,
                   clips=[dict(title='Название момента', start_us=0, end_us=30 * SECOND,
                               reason='Причина выбора', hook='Заголовок', cover_us=10 * SECOND)])
    signals = analysis['signals']
    # Overview plus a short before/during/after sequence for distinct strong events.
    step = max(30 * SECOND, (duration + 59) // 60)
    times = set(range(0, duration, step))
    peaks = []
    for signal in sorted(signals, key=lambda s: max(s['loudness'], s['motion']), reverse=True):
        time = signal['time_us']
        if max(signal['loudness'], signal['motion']) < .7 or len(peaks) == 12:
            break
        if all(abs(time - t) >= 10 * SECOND for t in peaks):
            peaks.append(time)
            times.update(t for t in (time-2*SECOND,time,time+2*SECOND) if 0 <= t < duration)
    times = sorted(times)
    path = Path(path)
    with TemporaryDirectory(dir=path.parent) as temp:
        temporary = Path(temp) / 'package.zip'
        with ZipFile(temporary, 'w', ZIP_DEFLATED) as archive:
            def dump(name, value):
                archive.writestr(name, json.dumps(value, ensure_ascii=False))
            dump('manifest.json', manifest)
            dump('events.json', dict(signals=signals, speech_gaps_us=pauses))
            archive.writestr('transcript.txt', transcript or 'Распознанных реплик нет.')
            dump('cuts.example.json', example)
            archive.writestr('README.txt', 'Проанализируй всю запись по тексту, событиям и листам кадров. '
                'Подбери самостоятельные моменты от 30 секунд, верни таблицу и файл cuts.json по примеру. '
                'Все времена в микросекундах от начала исходника. Вместо start_us и end_us можно указать '
                'start_segment и end_segment (номера из transcript.txt). cover_us внутри клипа. '
                'Неуверенность обозначь в reason. При отсутствии достойных моментов верни пустой clips.')
            for offset in range(0, len(times), 12):
                sheet = QImage(960, 4*210, QImage.Format.Format_RGB32)
                sheet.fill(QColor('black'))
                painter = QPainter(sheet)
                try:
                    painter.setPen(QColor('white'))
                    for i, time in enumerate(times[offset:offset+12]):
                        check()
                        x, y = i % 3 * 320, i // 3 * 210
                        frame = read_frame(request['source'], time, preview_size=320)
                        from PySide6.QtCore import Qt
                        frame = frame.scaled(320, 180, Qt.AspectRatioMode.KeepAspectRatio)
                        painter.drawImage(x+(320-frame.width())//2, y, frame)
                        painter.drawText(x+5, y+199, stamp(time))
                        progress(.8 + .19*(offset+i+1)/len(times))
                finally:
                    painter.end()
                image = Path(temp) / f'frames-{offset//12+1:03}.jpg'
                if not sheet.save(str(image), 'JPEG', 85):
                    raise OSError('Не удалось сохранить лист кадров')
                archive.write(image, image.name)
        check()
        os.replace(temporary, path)
    return package, index


def parse_plan(data, project):
    if not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1:
        raise ValueError('Неизвестная версия плана')
    if data.get('source_sha256') != project.fingerprint:
        raise ValueError('План относится к другому исходному видео')
    package = data.get('package_id')
    if not isinstance(package, str) or package not in project.exchange_indexes:
        raise ValueError('Откройте проект, в котором был подготовлен ZIP')
    index = project.exchange_indexes[package]
    if not isinstance(data.get('clips'), list):
        raise ValueError('Ожидался список clips')
    seen = set()
    result = []
    for n, row in enumerate(data['clips'], 1):
        try:
            if 'start_segment' in row or 'end_segment' in row:
                if 'start_us' in row or 'end_us' in row:
                    raise ValueError('укажите один способ выбора границ')
                start, end = index[row['start_segment']][0], index[row['end_segment']][1]
            else:
                start, end = row['start_us'], row['end_us']
            if (type(start) is not int or type(end) is not int or start < 0 or
                    end > project.metadata['duration_us'] or end-start < 30*SECOND):
                raise ValueError('проверьте границы и минимум 30 секунд')
            title = row['title'].strip()
            hook = row.get('hook', '').strip()
            reason = row.get('reason', '').strip()
            frame = row.get('cover_us')
            if not title or (frame is not None and (type(frame) is not int or not start <= frame < end)):
                raise ValueError('проверьте название и время обложки')
            if (start,end) not in seen:
                result.append(dict(start_us=start,end_us=end,name=title,hook=hook,cover_us=frame,text=reason,
                                   status='pending',evidence=[],score=None))
                seen.add((start,end))
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            raise ValueError(f'Клип {n}: {exc}') from exc
    return result
