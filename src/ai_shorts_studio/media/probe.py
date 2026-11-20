import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
from fractions import Fraction

def binary(name):
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
    bundled = root / 'bin' / (name + ('.exe' if os.name == 'nt' else ''))
    found = str(bundled) if bundled.exists() else shutil.which(name)
    if not found:
        raise RuntimeError(f"Не найден {name}. Установите полную сборку приложения.")
    return found

def run(args, timeout=30):
    p = subprocess.run(args, capture_output=True, text=True, encoding='utf-8', errors='replace',
                       timeout=timeout, creationflags=0x08000000 if os.name == 'nt' else 0)
    if p.returncode:
        raise RuntimeError(p.stderr[-4000:])
    return p.stdout

def fingerprint(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def probe(path, *, strict_fps=False):
    data = json.loads(run([binary('ffprobe'), '-v','error','-show_format','-show_streams','-of','json',str(path)]))
    videos = [s for s in data['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')]
    if not videos:
        raise ValueError('В файле нет видеопотока')
    v = videos[0]
    duration = float(data['format'].get('duration', v.get('duration',0)))
    if duration <= 0:
        raise ValueError('Не удалось определить длительность')
    try:
        video_duration = float(v['duration'])
        if not math.isfinite(video_duration) or video_duration <= 0:
            video_duration = duration
    except (KeyError, TypeError, ValueError):
        video_duration = duration
    rotation = int(float(v.get('tags',{}).get('rotate',0)))
    for side in v.get('side_data_list',[]):
        rotation = int(side.get('rotation',rotation))
    w,h = v['width'],v['height']
    sar = v.get('sample_aspect_ratio','1:1')
    try:
        w = round(w * float(Fraction(sar.replace(':','/'))))
    except (ValueError, ZeroDivisionError):
        pass
    if rotation % 180:
        w,h = h,w
    try:
        fps = float(Fraction(v['avg_frame_rate']))
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError('Недопустимая частота кадров')
    except (KeyError, ValueError, ZeroDivisionError):
        if strict_fps:
            raise ValueError('Не удалось проверить частоту кадров результата') from None
        fps = 30
    return dict(duration_us=round(duration*1e6), video_duration_us=round(video_duration*1e6),
                width=w, height=h, fps=fps,
                size=Path(path).stat().st_size, video_codec=v['codec_name'], video_index=v['index'],
                rotation=rotation, sar=sar, audio=[dict(index=s['index'], codec=s['codec_name'],
                title=s.get('tags',{}).get('title',f"Дорожка {s['index']}"))
                for s in data['streams'] if s['codec_type']=='audio'])
