"""Create ordinary editable clips from start markers, without touching media."""
from copy import deepcopy
import re
from uuid import uuid4
from ai_shorts_studio.domain.model import Clip

_PATTERN = re.compile(r'^(\d+(?::\d{1,2}){0,2}(?:[.,]\d{1,6})?)(?:\s+(.+))?$')

def clips_from_starts(text, duration_us, length_us, template=None):
    if not isinstance(length_us, int) or length_us <= 0:
        raise ValueError('Длительность клипа должна быть больше нуля')
    result, seen = [], set()
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        match = _PATTERN.fullmatch(line)
        if not match:
            raise ValueError(f'Строка {line_number}: укажите начало в формате ММ:СС или ЧЧ:ММ:СС')
        marker, name = match.groups()
        if name and re.match(r"^(?:->|[-–—])\s*\d", name):
            raise ValueError(f'Строка {line_number}: вставьте только начало; длина задаётся отдельно')
        # Integer parsing retains all six decimal places without floating point rounding.
        main, _, fraction = marker.replace(',', '.').partition('.')
        fields = [int(x) for x in main.split(':')]
        if len(fields) > 1 and (fields[-1] >= 60 or len(fields) == 3 and fields[-2] >= 60):
            raise ValueError(f'Строка {line_number}: секунды и минуты в ЧЧ:ММ:СС должны быть меньше 60')
        seconds = 0
        for field in fields:
            seconds = seconds * 60 + field
        start = seconds * 1_000_000 + int(fraction.ljust(6, '0') or '0')
        if start >= duration_us:
            raise ValueError(f'Строка {line_number}: начало находится за пределами записи')
        if start in seen:
            raise ValueError(f'Строка {line_number}: повторный таймкод {marker}')
        seen.add(start)
        clip = deepcopy(template) if template else Clip('', 0, 1)
        clip.id = uuid4().hex
        clip.name = name or f'Клип {marker}'
        clip.start_us, clip.end_us = start, min(start + length_us, duration_us)
        clip.validate(duration_us)
        result.append(clip)
    if not result:
        raise ValueError('Вставьте хотя бы один таймкод начала')
    return result
