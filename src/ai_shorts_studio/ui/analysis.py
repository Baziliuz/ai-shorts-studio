from copy import deepcopy
from PySide6.QtCore import Qt,QUrl
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLabel,QComboBox,QSpinBox,QListWidget,
    QListWidgetItem,QDialogButtonBox,QPushButton,QHBoxLayout)
from ai_shorts_studio.ui.timecode_edit import format_timecode

class AnalysisDialog(QDialog):
    def __init__(self,metadata,parent=None):
        super().__init__(parent); self.setWindowTitle('Найти моменты · экспериментальный режим'); self.resize(570,400)
        layout=QVBoxLayout(self)
        note=QLabel('Локально: речь + изменения громкости + разница кадров. Это эвристические предложения для просмотра, а не распознавание побед, эмоций или вирусности. Распознавание — CPU int8; скорость на вашем ПК пока не измерена.')
        note.setWordWrap(True); layout.addWidget(note)
        form=QFormLayout(); layout.addLayout(form)
        self.track=QComboBox()
        for track in metadata['audio']: self.track.addItem(f"#{track['index']} · {track['title']}",track['index'])
        if not metadata['audio']: self.track.addItem('Без звука: только разница кадров',None)
        form.addRow('Дорожка речи (лучше микрофон)',self.track)
        self.language=QComboBox()
        for text,key in [('Русский','ru'),('English','en'),('Автоопределение',None)]: self.language.addItem(text,key)
        form.addRow('Язык',self.language)
        self.target=QComboBox()
        for seconds in (30,60,180): self.target.addItem(f'{seconds} сек.',seconds)
        self.minimum=QSpinBox(); self.minimum.setRange(1,180); self.minimum.setValue(10)
        self.maximum=QSpinBox(); self.maximum.setRange(1,600); self.maximum.setValue(30)
        self.target.currentIndexChanged.connect(lambda _:self.maximum.setValue(self.target.currentData()))
        self.limit=QSpinBox(); self.limit.setRange(1,30); self.limit.setValue(10)
        form.addRow('Целевая длина',self.target); form.addRow('Минимум, сек.',self.minimum); form.addRow('Строгий максимум, сек.',self.maximum); form.addRow('Не более клипов',self.limit)
        self.error=QLabel(); self.error.setWordWrap(True); layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Начать анализ')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def accept(self):
        if not self.minimum.value()<=self.target.currentData()<=self.maximum.value():
            self.error.setText('Минимум ≤ целевая длина ≤ максимум'); return
        super().accept()
    def parameters(self):
        return dict(audio_index=self.track.currentData(),language=self.language.currentData(),
            target_us=self.target.currentData()*1_000_000,min_us=self.minimum.value()*1_000_000,
            max_us=self.maximum.value()*1_000_000,limit=self.limit.value())

class ReviewDialog(QDialog):
    def __init__(self,proposals,source,parent=None):
        super().__init__(parent); self.setWindowTitle('Предложения моментов · выберите после просмотра'); self.resize(950,850)
        self.proposals=deepcopy(proposals); layout=QVBoxLayout(self)
        note=QLabel('Оценка 0–100 — эвристическая. Просмотр ниже показывает исходник и первую аудиодорожку. Итоговую компоновку и выбранный микс проверяйте через «Предпросмотр» после создания клипа.')
        note.setWordWrap(True); layout.addWidget(note)
        self.items=QListWidget(); layout.addWidget(self.items)
        for candidate in self.proposals:
            first=format_timecode(candidate['start_us'])[:-3]; last=format_timecode(candidate['end_us'])[:-3]
            item=QListWidgetItem(f"{candidate['score']}/100 · {first} — {last}\n{candidate['name']}\n"+'; '.join(candidate['evidence']))
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Unchecked)
            if candidate['status']=='accepted':
                item.setText('[Уже создан] '+item.text()); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            elif candidate['status']=='rejected': item.setText('[Отклонён] '+item.text())
            self.items.addItem(item)
        self.details=QLabel(); self.details.setTextFormat(Qt.TextFormat.PlainText); self.details.setWordWrap(True); self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); layout.addWidget(self.details)
        video=QVideoWidget(); video.setMinimumHeight(200); layout.addWidget(video)
        self.player=QMediaPlayer(self); audio=QAudioOutput(self); audio.setVolume(.7)
        self.player.setAudioOutput(audio); self.player.setVideoOutput(video); self.audio=audio
        self.player.setSource(QUrl.fromLocalFile(source)); self.pending_seek=None
        self.player.mediaStatusChanged.connect(self.media_status)
        self.player.positionChanged.connect(self.position_changed)
        self.player.errorOccurred.connect(lambda *_:self.details.setText('Плеер не открыл исходник. Можно создать выбранный клип и использовать прокси в основном окне.'))
        row=QHBoxLayout(); layout.addLayout(row)
        play=QPushButton('Смотреть исходный фрагмент'); play.clicked.connect(self.play); row.addWidget(play)
        reject=QPushButton('Отклонить выделенное предложение'); reject.clicked.connect(self.reject_current); row.addWidget(reject)
        self.items.currentRowChanged.connect(self.select)
        self.items.setCurrentRow(0)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Создать отмеченные клипы')
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def select(self,index):
        self.player.pause()
        if index>=0: self.details.setText(self.proposals[index].get('text','Без распознанной речи'))
    def play(self):
        row=self.items.currentRow()
        if row<0: return
        self.pending_seek=self.proposals[row]['start_us']//1000
        self.media_status(self.player.mediaStatus())
    def media_status(self,status):
        if self.pending_seek is not None and status in (QMediaPlayer.MediaStatus.LoadedMedia,QMediaPlayer.MediaStatus.BufferedMedia):
            self.player.setPosition(self.pending_seek); self.pending_seek=None; self.player.play()
    def position_changed(self,ms):
        row=self.items.currentRow()
        if row>=0 and ms*1000>=self.proposals[row]['end_us']: self.player.pause()
    def reject_current(self):
        row=self.items.currentRow()
        if row<0 or self.proposals[row]['status']=='accepted': return
        self.proposals[row]['status']='rejected'; self.items.item(row).setCheckState(Qt.CheckState.Unchecked)
        self.items.item(row).setText('[Отклонён] '+self.items.item(row).text().removeprefix('[Отклонён] '))
    def chosen(self):
        return [c for i,c in enumerate(self.proposals) if c['status']!='accepted' and self.items.item(i).checkState()==Qt.CheckState.Checked]
    def done(self,result):
        self.player.stop(); self.player.setSource(QUrl()); super().done(result)
