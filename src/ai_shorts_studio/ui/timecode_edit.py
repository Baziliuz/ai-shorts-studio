"""Timecode input without QTime's 24-hour wraparound or float rounding."""
import re
from PySide6.QtWidgets import QLineEdit

def format_timecode(value):
    if not isinstance(value,int) or value < 0:
        raise ValueError('Время должно быть неотрицательным числом микросекунд')
    seconds,micros=divmod(value,1_000_000)
    minutes,seconds=divmod(seconds,60)
    hours,minutes=divmod(minutes,60)
    return f'{hours:02}:{minutes:02}:{seconds:02}.{micros:06}'

class TimecodeEdit(QLineEdit):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setPlaceholderText('00:00:00.000000')
        self.setToolTip('Часы:минуты:секунды. Доли секунды необязательны (до 6 знаков). Например: 01:25:30.500')
        self.set_us(0)

    def set_us(self,value):
        self.setText(format_timecode(value))

    def value_us(self):
        match=re.fullmatch(r'([0-9]+):([0-5][0-9]):([0-5][0-9])(?:[.,]([0-9]{1,6}))?',self.text().strip())
        if not match:
            raise ValueError('Введите время как ЧЧ:ММ:СС, например 01:25:30 или 01:25:30.500000')
        hours,minutes,seconds,fraction=match.groups()
        return (int(hours)*3600+int(minutes)*60+int(seconds))*1_000_000+int((fraction or '').ljust(6,'0'))
