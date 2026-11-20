from PySide6.QtCore import QThread, Signal
from ai_shorts_studio.media.render import Cancelled

class Worker(QThread):
    progress = Signal(int)
    note = Signal(str)

    def __init__(self, task):
        super().__init__()
        self.task = task
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.task(lambda value:self.progress.emit(round(value*100)))
        except Cancelled:
            self.error = 'Задача отменена. Можно запустить её повторно.'
        except Exception as error:
            self.error = str(error) or type(error).__name__
