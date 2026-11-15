from dataclasses import dataclass, field, asdict
from copy import deepcopy
from uuid import uuid4

@dataclass
class Rect:
    x: float = 0
    y: float = 0
    w: float = 1
    h: float = 1

    def validate(self):
        if not (0 <= self.x < 1 and 0 <= self.y < 1 and self.w > 0 and self.h > 0
                and self.x + self.w <= 1.000001 and self.y + self.h <= 1.000001):
            raise ValueError("Область должна находиться внутри кадра")

@dataclass
class Clip:
    name: str
    start_us: int
    end_us: int
    id: str = field(default_factory=lambda: uuid4().hex)
    mode: str = "center"
    width: int = 720
    height: int = 1280
    fps: float = 30
    crop: Rect = field(default_factory=Rect)
    camera: Rect = field(default_factory=lambda: Rect(0, 0, .25, .25))
    game: Rect = field(default_factory=Rect)
    camera_share: float = .3
    audio: dict = field(default_factory=dict)

    def validate(self, duration_us):
        if not (isinstance(self.start_us, int) and isinstance(self.end_us, int)
                and 0 <= self.start_us < self.end_us <= duration_us):
            raise ValueError("Границы: 0 ≤ начало < конец ≤ длительность")
        if self.mode not in {"center", "manual", "blur", "gaming"}:
            raise ValueError("Неизвестная компоновка")
        if (self.width, self.height) not in {(720,1280),(1080,1920),(1280,720),(1920,1080)}:
            raise ValueError("Недопустимое разрешение")
        if not 0 < self.fps <= 120 or not .1 <= self.camera_share <= .8:
            raise ValueError("Недопустимые параметры")
        for r in (self.crop, self.camera, self.game):
            r.validate()
        if any(not 0 <= float(v) <= 4 for v in self.audio.values()):
            raise ValueError("Громкость должна быть от 0 до 400%")

    @classmethod
    def load(cls, data):
        data = deepcopy(data)
        for key in ("crop", "camera", "game"):
            data[key] = Rect(**data[key])
        return cls(**data)

@dataclass
class Project:
    source: str
    fingerprint: str
    metadata: dict
    clips: list[Clip] = field(default_factory=list)

    def serialize(self):
        return asdict(self)

    @classmethod
    def load(cls, data):
        return cls(data["source"], data["fingerprint"], data["metadata"],
                   [Clip.load(c) for c in data["clips"]])

class History:
    def __init__(self):
        self.undo_stack, self.redo_stack = [], []

    def record(self, project):
        self.undo_stack.append(deepcopy(project))
        self.redo_stack.clear()

    def move(self, current, redo=False):
        source, target = (self.redo_stack, self.undo_stack) if redo else (self.undo_stack, self.redo_stack)
        if not source:
            return current
        target.append(deepcopy(current))
        return source.pop()
