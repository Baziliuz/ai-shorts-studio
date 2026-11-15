from PySide6.QtCore import QThread, Signal
from ai_shorts_studio.media.render import Cancelled

class Worker(QThread):
    done = Signal(object)
    failed = Signal(str)
    progress = Signal(int)

    def __init__(self, task):
        super().__init__()
        self.task = task

    def run(self):
        try:
            self.done.emit(self.task(lambda value:self.progress.emit(round(value*100))))
        except Cancelled:
            self.failed.emit('Задача отменена. Незавершённый результат удалён.')
        except Exception as error:
            self.failed.emit(str(error))
