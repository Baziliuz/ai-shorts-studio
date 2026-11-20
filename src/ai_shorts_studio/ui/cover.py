"""Preview and edit the frame and hook used by the exported JPG."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QSlider, QVBoxLayout

from ai_shorts_studio.media.cover import HOOK_MAX_LENGTH, compose_cover, last_frame_us, read_frame
from ai_shorts_studio.media.probe import probe
from ai_shorts_studio.ui.timecode_edit import TimecodeEdit


class CoverDialog(QDialog):
    def __init__(self, video_path, *, hook='', frame_us=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Обложка клипа')
        self.resize(620, 600)
        self._video_path = video_path
        self._last_us = last_frame_us(probe(video_path))
        self.frame_us = self._last_us // 2 if frame_us is None else min(self._last_us, max(0, frame_us))
        self._frame = None

        layout = QVBoxLayout(self)
        self.preview = QLabel()
        self.preview.setFixedHeight(380)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setAccessibleName('Предпросмотр обложки')
        layout.addWidget(self.preview)

        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, min(self._last_us, 1_000_000))
        self.position.setAccessibleName('Положение кадра в клипе')
        self.position.setEnabled(bool(self._last_us))
        layout.addWidget(self.position)

        fields = QFormLayout()
        self.timecode = TimecodeEdit()
        fields.addRow('Время кадра в клипе', self.timecode)
        self.hook_text = QLineEdit()
        self.hook_text.setMaxLength(HOOK_MAX_LENGTH)
        self.hook_text.setText(hook)
        self.hook_text.setPlaceholderText('Оставьте пустым, чтобы сохранить только кадр')
        fields.addRow('Текст на обложке', self.hook_text)
        layout.addLayout(fields)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet('color: #ffb4a8')
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('Сохранить обложку')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('Отмена')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh = QTimer(self)
        self._refresh.setSingleShot(True)
        self._refresh.setInterval(180)
        self._refresh.timeout.connect(self._load_frame)
        self._sync_position()
        self._load_frame()
        self.position.valueChanged.connect(self._slide)
        self.timecode.editingFinished.connect(self._read_timecode)
        self.hook_text.textChanged.connect(self._show_frame)

    @property
    def hook(self):
        return ' '.join(self.hook_text.text().split())

    def _sync_position(self):
        self.timecode.set_us(self.frame_us)
        if self._last_us:
            self.position.blockSignals(True)
            self.position.setValue(self.frame_us * self.position.maximum() // self._last_us)
            self.position.blockSignals(False)

    def _slide(self, value):
        self.frame_us = self._last_us * value // self.position.maximum()
        self.timecode.set_us(self.frame_us)
        self._refresh.start()

    def _read_timecode(self):
        try:
            frame_us = self.timecode.value_us()
            if frame_us > self._last_us:
                raise ValueError('Укажите время кадра в пределах клипа')
        except ValueError as error:
            self.error.setText(str(error))
            return False
        self.error.clear()
        if frame_us != self.frame_us:
            self.frame_us = frame_us
            self._sync_position()
            self._refresh.start()
        return True

    def _load_frame(self):
        try:
            self._frame = read_frame(self._video_path, self.frame_us, preview_size=490)
        except RuntimeError as error:
            self._frame = None
            self.preview.clear()
            self.error.setText(str(error))
        else:
            self.error.clear()
            self._show_frame()
        self.save_button.setEnabled(self._frame is not None)

    def _show_frame(self):
        if self._frame is not None:
            frame = compose_cover(self._frame, self.hook)
            pixmap = QPixmap.fromImage(frame).scaled(490, self.preview.height(), Qt.AspectRatioMode.KeepAspectRatio,
                                                      Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(pixmap)

    def accept(self):
        if not self._read_timecode():
            return
        if self._refresh.isActive():
            self._refresh.stop()
            self._load_frame()
        if self._frame is not None:
            super().accept()
