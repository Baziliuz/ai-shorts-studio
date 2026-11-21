from copy import deepcopy
from PySide6.QtCore import Qt,QUrl
from PySide6.QtMultimedia import QMediaPlayer,QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLabel,QComboBox,QListWidget,
    QListWidgetItem,QDialogButtonBox,QPushButton,QHBoxLayout,QPlainTextEdit)
from ai_shorts_studio.ui.timecode_edit import format_timecode

class AnalysisDialog(QDialog):
    def __init__(self, metadata, parent=None, *, automatic=False, orientation=None):
        super().__init__(parent)
        self.setWindowTitle('Создать нарезку' if automatic else 'Найти моменты для просмотра')
        self.resize(500, 240)
        layout = QVBoxLayout(self)
        note = QLabel(
            'Моменты выбираются по речи, громкости и движению. '
            'Длительность каждого клипа зависит от содержания.'
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        form = QFormLayout()
        layout.addLayout(form)

        self.format = QComboBox()
        self.format.addItem('9:16 · вертикально', '9:16')
        self.format.addItem('16:9 · горизонтально', '16:9')
        self.format.addItem('Как в исходнике', 'source')
        self.format.setCurrentIndex(max(0, self.format.findData(orientation)))
        if automatic:
            form.addRow('Пропорции кадра', self.format)

        self.profile = QComboBox()
        self.profile.addItem('Определить автоматически', 'auto')
        self.profile.addItem('Игровой стрим', 'game')
        self.profile.addItem('Обзор, интервью или подкаст', 'conversation')
        form.addRow('Тип записи', self.profile)

        self.track = QComboBox()
        for track in metadata['audio']:
            self.track.addItem(f"#{track['index']} · {track['title']}", track['index'])
        if not metadata['audio']:
            self.track.addItem('Без звука')
            self.track.setEnabled(False)
        form.addRow('Дорожка для анализа речи', self.track)
        self.language = QComboBox()
        for label, key in [('Русский', 'ru'), ('English', 'en'), ('Автоопределение', None)]:
            self.language.addItem(label, key)
        self.language.setCurrentIndex(self.language.count() - 1)
        self.language.setEnabled(bool(metadata['audio']))
        form.addRow('Язык речи', self.language)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            'Создать нарезку' if automatic else 'Начать анализ'
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @property
    def orientation(self):
        return self.format.currentData()

    def parameters(self):
        return dict(selection='content', profile=self.profile.currentData(),
                    audio_index=self.track.currentData(), language=self.language.currentData())


class PublicationDialog(QDialog):
    def __init__(self, clip_name, status=None, note='', parent=None):
        super().__init__(parent)
        self.setWindowTitle('Оценить момент')
        self.resize(440, 230)
        form = QFormLayout(self)
        name = QLabel(clip_name)
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        form.addRow('Клип', name)
        self.rating = QComboBox()
        self.rating.addItem('Выберите оценку')
        self.rating.addItem('Годится для публикации', 'publishable')
        self.rating.addItem('Ошибка отбора', 'selection_error')
        self.rating.setCurrentIndex(max(0, self.rating.findData(status)))
        form.addRow('Оценка', self.rating)
        self.comment = QPlainTextEdit(note)
        self.comment.setPlaceholderText('Например, не хватает подводки или ответ обрезан')
        form.addRow('Комментарий', self.comment)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QDialogButtonBox.StandardButton.Save)
        save.setText('Сохранить оценку')
        save.setEnabled(self.status is not None)
        self.rating.currentIndexChanged.connect(lambda: save.setEnabled(self.status is not None))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    @property
    def status(self):
        return self.rating.currentData()

    @property
    def note(self):
        return self.comment.toPlainText().strip()

class ReviewDialog(QDialog):
    def __init__(self,proposals,source,parent=None, *, imported=False):
        super().__init__(parent); self.setWindowTitle('Предложения моментов · выберите после просмотра'); self.resize(950,850)
        self.proposals=deepcopy(proposals); layout=QVBoxLayout(self)
        note=QLabel('Оценка 0–100 — эвристическая. Просмотр ниже показывает исходник и первую аудиодорожку. Итоговую компоновку и выбранный микс проверяйте через «Предпросмотр» после создания клипа.')
        if imported:
            self.setWindowTitle('План нарезки из ChatGPT')
            note.setText('Проверьте фрагменты и снимите отметки с ненужных.')
        note.setWordWrap(True); layout.addWidget(note)
        self.items=QListWidget(); layout.addWidget(self.items)
        for candidate in self.proposals:
            first=format_timecode(candidate['start_us'])[:-3]; last=format_timecode(candidate['end_us'])[:-3]
            item=QListWidgetItem(f"{candidate['score']}/100 · {first} — {last}\n{candidate['name']}\n"+'; '.join(candidate['evidence']))
            if imported:
                item.setText(f"{first} → {last} · {candidate['name']}")
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable); item.setCheckState(Qt.CheckState.Checked if imported else Qt.CheckState.Unchecked)
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
