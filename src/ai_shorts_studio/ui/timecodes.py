from PySide6.QtWidgets import (QDialog,QVBoxLayout,QLabel,QPlainTextEdit,QDoubleSpinBox,
                              QDialogButtonBox)
from ai_shorts_studio.domain.timecodes import clips_from_starts

class TimecodesDialog(QDialog):
    def __init__(self,duration_us,template,parent=None):
        super().__init__(parent)
        self.duration_us,self.template=duration_us,template
        self.generated=[]
        self.setWindowTitle('Нарезать по таймкодам'); self.resize(560,440)
        layout=QVBoxLayout(self)
        note=QLabel('Вставьте начала фрагментов — по одному на строку.\nФорматы: ММ:СС, ЧЧ:ММ:СС или секунды. После пробела можно написать название.')
        note.setWordWrap(True); layout.addWidget(note)
        self.text=QPlainTextEdit(); self.text.setPlaceholderText('05:20 Первый раунд\n12:10\n01:25:40 Финал'); layout.addWidget(self.text)
        layout.addWidget(QLabel('Длительность каждого клипа, секунд:'))
        self.length=QDoubleSpinBox(); self.length.setRange(.001,86400); self.length.setDecimals(3); self.length.setValue(30); layout.addWidget(self.length)
        description=QLabel('Конец = начало + длительность, в пределах записи. Пересечения допустимы. Компоновка и звук берутся из текущего клипа. После создания клипы можно поправить и экспортировать вместе.')
        description.setWordWrap(True); layout.addWidget(description)
        self.error=QLabel(); self.error.setWordWrap(True); self.error.setStyleSheet('color:#ffb4a8'); layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Создать клипы')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)

    def accept(self):
        try:
            self.generated=clips_from_starts(self.text.toPlainText(),self.duration_us,
                                            round(self.length.value()*1e6),self.template)
        except ValueError as error:
            self.error.setText(str(error)); return
        super().accept()
