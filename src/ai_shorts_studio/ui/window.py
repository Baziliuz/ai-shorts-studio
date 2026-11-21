from copy import deepcopy
from pathlib import Path
import json
import shutil
from uuid import uuid4
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QSplitter,
    QPushButton,QLabel,QListWidget,QFileDialog,QMessageBox,QSlider,QDoubleSpinBox,QComboBox,
    QLineEdit,QGroupBox,QProgressBar,QScrollArea,QCheckBox,QDialog,QDialogButtonBox)
from ai_shorts_studio.domain.model import Project,Clip,Rect,History
from ai_shorts_studio.storage.project import Store
from ai_shorts_studio.media.probe import probe,fingerprint
from ai_shorts_studio.media.render import Renderer
from ai_shorts_studio.jobs.export import export_clips, revise_cover
from ai_shorts_studio.jobs.worker import Worker
from ai_shorts_studio.ui.timecode_edit import TimecodeEdit,format_timecode

STYLE = '''
QWidget {background:#151820; color:#e5e7ef; font-size:13px;}
QMainWindow {background:#11141b;} QGroupBox {border:1px solid #343b4d; border-radius:8px; margin-top:12px; padding:12px;}
QGroupBox::title {subcontrol-origin:margin; left:12px; color:#a8b5d8;}
QPushButton {background:#303952; padding:9px; border-radius:6px;} QPushButton:hover {background:#435579;}
QPushButton:disabled {color:#737b91;} QLineEdit,QComboBox,QDoubleSpinBox,QListWidget {background:#202633; padding:5px; border:1px solid #3a4256; border-radius:4px;}
QListWidget::item:selected {background:#405589;} QProgressBar {border:1px solid #3a4256; text-align:center;} QProgressBar::chunk {background:#678cff;}
'''

class Window(QMainWindow):
    def __init__(self, data_dir, cache_dir):
        super().__init__()
        self.data_dir,self.cache_dir = Path(data_dir),Path(cache_dir)
        self.project,self.store = None,None
        self.history = History()
        self.worker,self.renderer = None,None
        self.on_complete = self.on_failed = None
        self.loading = False
        self.source_view = True
        self.proxy_view = False
        self.setWindowTitle('AI Shorts Studio · ручной редактор')
        self.resize(1380,900)
        self.setStyleSheet(STYLE)
        self.player = QMediaPlayer(self)
        self.output = QAudioOutput(self)
        self.player.setAudioOutput(self.output)
        self.video = QVideoWidget()
        self.video.setMinimumSize(400,280)
        self.player.setVideoOutput(self.video)
        self.player.positionChanged.connect(self.position_changed)
        self.player.durationChanged.connect(lambda d:self.seek.setMaximum(max(1,d)))
        self.player.errorOccurred.connect(lambda *_:self.statusBar().showMessage('Плеер не открыл видео. Доступно создание прокси.'))
        self.player.tracksChanged.connect(self.sync_audio_preview)
        self.build()
        self.restore_settings()

    def button(self,text,callback,layout):
        b = QPushButton(text)
        b.clicked.connect(callback)
        layout.addWidget(b)
        return b

    def build(self):
        central = QWidget(); root = QVBoxLayout(central); self.setCentralWidget(central)
        bar = QHBoxLayout(); root.addLayout(bar)
        self.button('Импорт видео',self.import_video,bar)
        self.button('Открыть проект',self.open_project,bar)
        self.button('Вернуть исходник',self.show_source,bar)
        self.button('Создать прокси',self.proxy,bar)
        self.button('Кэш',self.cache_info,bar)
        self.button('Модель речи',self.setup_speech_model,bar)
        splitter = QSplitter(); root.addWidget(splitter,1)
        left = QWidget(); ll = QVBoxLayout(left); splitter.addWidget(left)
        ll.addWidget(QLabel('КЛИПЫ'))
        self.clips = QListWidget(); self.clips.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.clips.currentRowChanged.connect(self.load_clip); ll.addWidget(self.clips)
        self.button('+ Новый клип',self.add_clip,ll)
        self.button('Создать нарезку',self.create_shorts,ll)
        self.button('Нарезать по таймкодам',self.add_timecodes,ll)
        self.button('Найти моменты',self.find_moments,ll)
        self.button('Предложения моментов',self.review_moments,ll)
        self.button('Удалить клип',self.delete_clip,ll)
        self.info = QLabel('Импортируйте MP4, MOV, MKV или AVI.\nВсе материалы остаются на компьютере.')
        self.info.setWordWrap(True); ll.addWidget(self.info)
        mid = QWidget(); ml = QVBoxLayout(mid); splitter.addWidget(mid)
        ml.addWidget(self.video,1)
        self.view_label = QLabel('Исходник'); ml.addWidget(self.view_label)
        self.seek = QSlider(Qt.Orientation.Horizontal); self.seek.sliderMoved.connect(self.player.setPosition); ml.addWidget(self.seek)
        self.time = QLabel('00:00:00.000'); ml.addWidget(self.time)
        playback = QHBoxLayout(); ml.addLayout(playback)
        self.button('▶ / Ⅱ',self.toggle_play,playback)
        self.button('Начало здесь',lambda:self.mark(False),playback)
        self.button('Конец здесь',lambda:self.mark(True),playback)
        volume = QSlider(Qt.Orientation.Horizontal); volume.setRange(0,100); volume.setValue(80)
        volume.valueChanged.connect(lambda v:self.output.setVolume(v/100)); playback.addWidget(QLabel('Громкость')); playback.addWidget(volume)
        self.output.setVolume(.8)
        right = QWidget(); form_root = QVBoxLayout(right)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setWidget(right); splitter.addWidget(scroll)
        self.resolution = QComboBox()
        for w,h in [(720,1280),(1080,1920),(1280,720),(1920,1080)]:
            self.resolution.addItem(f'{w} × {h} · {"9:16" if h > w else "16:9"}',f'{w}x{h}')
        self.resolution.addItem('Как в исходнике', 'source')
        form_root.addWidget(QLabel('Размер выбранных клипов'))
        form_root.addWidget(self.resolution)
        self.channels = QCheckBox('Twitch и VK на обложках Milkmen')
        form_root.addWidget(self.channels)
        self.button('Сохранить выбранные клипы (MP4)', self.export, form_root)
        self.advanced = QCheckBox('Дополнительные настройки')
        form_root.addWidget(self.advanced)
        details = QWidget()
        form_root.addWidget(details)
        form_root.addStretch()
        form_root = QVBoxLayout(details)
        details.hide()
        self.advanced.toggled.connect(details.setVisible)
        self.name = QLineEdit()
        self.start,self.end = TimecodeEdit(),TimecodeEdit()
        box = QGroupBox('Фрагмент'); f = QFormLayout(box); form_root.addWidget(box)
        f.addRow('Название',self.name); f.addRow('Начало · ЧЧ:ММ:СС',self.start); f.addRow('Конец · ЧЧ:ММ:СС',self.end)
        self.mode = QComboBox()
        for title,key in [('По центру','center'),('Ручная область','manual'),('Размытый фон','blur'),('Игра + камера','gaming')]:
            self.mode.addItem(title,key)
        self.fps = QComboBox(); self.fps.addItems(['Исходная','30','60'])
        box = QGroupBox('Компоновка'); f = QFormLayout(box); form_root.addWidget(box)
        f.addRow('Режим',self.mode); f.addRow('Кадров/сек.',self.fps)
        self.button('Выделить области на кадре мышью',self.select_regions,form_root)
        self.rects = {}
        for key,title in [('crop','Кадрирование'),('camera','Веб-камера'),('game','Область игры')]:
            b = QGroupBox(title+' · доли кадра 0–1'); grid = QFormLayout(b)
            controls = [self.spin(0,1,4) for _ in range(4)]
            for label,control in zip(['X','Y','Ширина','Высота'],controls):
                grid.addRow(label,control)
            self.rects[key] = controls
            form_root.addWidget(b)
        self.share = self.spin(.1,.8,2); self.share.setValue(.3)
        form_root.addWidget(QLabel('Доля высоты блока камеры')); form_root.addWidget(self.share)
        note = QLabel('Камера сверху, игра снизу. Закрытую камерой часть игры восстановить нельзя. Выберите область игры без встроенной камеры, если это возможно.')
        note.setWordWrap(True); form_root.addWidget(note)
        self.audio_box = QGroupBox('Звук · выбранные дорожки сводятся'); self.audio_layout = QFormLayout(self.audio_box)
        self.audio_controls = {}; form_root.addWidget(self.audio_box)
        self.normalize=QCheckBox('Выровнять громкость · динамическая нормализация')
        self.duck=QCheckBox('Приглушать игру во время речи')
        self.voice=QComboBox()
        form_root.addWidget(self.normalize); form_root.addWidget(self.duck)
        form_root.addWidget(QLabel('Дорожка микрофона для приглушения:')); form_root.addWidget(self.voice)
        help_audio=QLabel('Приглушение требует отдельных выбранных дорожек микрофона и игры. Уже сведённый звук не разделяется. Результат слушайте через рендер предпросмотра.')
        help_audio.setWordWrap(True); form_root.addWidget(help_audio)
        self.button('Применить изменения',self.apply,form_root)
        self.button('Предпросмотр компоновки (до 8 сек.)',self.preview,form_root)
        self.button('Прослушать выбранный микс (до 8 сек.)',self.preview,form_root)
        self.button('Редактировать обложку',self.edit_cover,form_root)
        self.button('Оценить момент',self.rate_clip,form_root)
        self.progress = QProgressBar(); root.addWidget(self.progress)
        self.cancel_button = self.button('Отменить задачу',self.cancel,root); self.cancel_button.setEnabled(False)
        splitter.setSizes([220,730,390])
        for text,shortcut,fn in [('Отменить',QKeySequence.StandardKey.Undo,lambda:self.undo(False)),('Повторить',QKeySequence.StandardKey.Redo,lambda:self.undo(True))]:
            a = QAction(text,self); a.setShortcut(shortcut); a.triggered.connect(fn); self.menuBar().addAction(a)
        self.statusBar().showMessage('Готово · локальная обработка')

    def spin(self,minimum,maximum,decimals):
        w = QDoubleSpinBox(); w.setRange(minimum,maximum); w.setDecimals(decimals); w.setSingleStep(.01 if maximum==1 else 1)
        return w

    def busy(self):
        return self.worker is not None

    def background(self,task,done,renderer=None,failed=None):
        if self.busy():
            self.message('Дождитесь завершения текущей задачи'); return
        self.renderer = renderer
        self.progress.setRange(0,100) if renderer else self.progress.setRange(0,0)
        self.progress.setValue(0); self.cancel_button.setEnabled(renderer is not None)
        worker = Worker(task); self.worker = worker
        self.on_complete, self.on_failed = done, failed or self.message
        worker.progress.connect(self.progress.setValue)
        worker.note.connect(self.statusBar().showMessage)
        if hasattr(renderer,'on_status'): renderer.on_status=worker.note.emit
        worker.finished.connect(self.task_finished)
        worker.start()

    def task_finished(self):
        worker = self.worker
        done, failed = self.on_complete, self.on_failed
        self.on_complete = self.on_failed = None
        worker.deleteLater(); self.worker = None; self.renderer = None
        self.cancel_button.setEnabled(False); self.progress.setRange(0,100)
        self.statusBar().showMessage('Задача завершена')
        if worker.error is None:
            done(worker.result)
        else:
            failed(worker.error)

    def cancel(self):
        if self.renderer:
            self.renderer.cancel()

    def message(self,text):
        QMessageBox.information(self,'AI Shorts Studio',str(text))

    def import_video(self):
        if self.busy(): return
        source,_ = QFileDialog.getOpenFileName(self,'Исходное видео','','Видео (*.mp4 *.mov *.mkv *.avi);;Все файлы (*)')
        if not source: return
        target,_ = QFileDialog.getSaveFileName(self,'Новый проект SQLite',str(self.data_dir/'Новый проект.sqlite'),'Проект (*.sqlite)')
        if not target: return
        if Path(target).exists():
            self.message('Выберите новое имя проекта: существующий проект не будет перезаписан.'); return
        self.background(lambda p:(probe(source),fingerprint(source)),lambda result:self.import_done(source,target,result))

    def import_done(self,source,target,result):
        metadata=result[0]
        audio={str(metadata['audio'][0]['index']):1} if metadata['audio'] else {}
        initial=Clip('Клип 1',0,min(30_000_000,metadata['duration_us']),fps=min(30,metadata['fps']),audio=audio)
        self.set_project(Store(target),Project(source,result[1],metadata,[initial]))

    def set_project(self,store,project):
        if self.store: self.store.close()
        self.store,self.project = store,project
        self.history = History(); self.save(); self.refresh()
        m = project.metadata
        tracks = '\n'.join(f"• {a['title']} ({a['codec']}, #{a['index']})" for a in m['audio']) or 'Без звука'
        self.info.setText(f"{Path(project.source).name}\n{m['duration_us']/1e6:.3f} сек. · {m['width']}×{m['height']}\n{m['fps']:.3f} fps · {m['size']/1024**3:.2f} ГБ\nВидео: {m['video_codec']} · SAR {m['sar']} · поворот {m['rotation']}°\n{tracks}")
        self.setWindowTitle(f'AI Shorts Studio · {store.path.name}')
        self.show_source()
        self.write_settings({'last_project':str(store.path)})

    def open_project(self):
        if self.busy(): return
        path,_ = QFileDialog.getOpenFileName(self,'Открыть проект',str(self.data_dir),'Проект (*.sqlite)')
        if path: self.read_project(path)

    def read_project(self,path):
        store = None
        try:
            store = Store(path); project = store.load()
            if not project: raise ValueError('Пустой проект')
            if not Path(project.source).exists():
                source,_ = QFileDialog.getOpenFileName(self,'Исходник перемещён. Укажите тот же файл')
                if not source: store.close(); return
                self.background(lambda p:fingerprint(source),lambda value:self.relink_done(store,project,source,value))
            else:
                self.background(lambda p:fingerprint(project.source),lambda value:self.verified_open(store,project,value))
        except Exception as e:
            if store: store.close()
            self.message(e)

    def verified_open(self,store,project,value):
        if value != project.fingerprint:
            store.close(); self.message('Содержимое исходника изменилось. Проект не открыт.'); return
        self.set_project(store,project)

    def relink_done(self,store,project,source,value):
        if value != project.fingerprint:
            store.close(); self.message('Отпечаток не совпадает. Выберите исходный файл.'); return
        project.source = source; self.set_project(store,project)

    def save(self):
        if self.store: self.store.save(self.project)

    def refresh(self,row=0):
        self.loading = True; self.clips.clear()
        numbers = self.store.clip_numbers(self.project.clips)
        for c in self.project.clips:
            self.clips.addItem(f'{numbers[c.id]:03d} · {c.name}\n{format_timecode(c.start_us)[:-3]} — {format_timecode(c.end_us)[:-3]}')
        self.loading = False
        self.clips.setCurrentRow(min(row,len(self.project.clips)-1))

    def selected(self):
        row = self.clips.currentRow()
        return self.project.clips[row] if self.project and 0 <= row < len(self.project.clips) else None

    def add_clip(self):
        if not self.project or self.busy(): return
        self.history.record(self.project)
        m = self.project.metadata
        start = min(self.player.position()*1000,max(0,m['duration_us']-1000000)) if self.source_view else 0
        audio = {str(m['audio'][0]['index']):1.0} if m['audio'] else {}
        self.project.clips.append(Clip(f'Клип {len(self.project.clips)+1}',start,min(start+30_000_000,m['duration_us']),fps=min(30,m['fps']),audio=audio))
        self.save(); self.refresh(len(self.project.clips)-1)

    def setup_speech_model(self):
        if self.busy(): return
        from ai_shorts_studio.ai.models import REGISTRY
        size=sum(f['size'] for f in REGISTRY['files'])/1024**2
        text=(f'Для распознавания нужно скачать многоязычную модель small: {size:.0f} МБ. '
              'Источник: Hugging Face / Systran, лицензия MIT. Модель сохраняется локально, '
              'файлы проверяются по SHA-256. После загрузки интернет для обработки не нужен. '
              'Видео, звук и текст не отправляются на сервер. Распознавание в этой версии — CPU int8. Скачать?')
        if QMessageBox.question(self,'Установка модели речи',text)!=QMessageBox.StandardButton.Yes: return
        self.download_speech_model(lambda _:self.message('Модель проверена. Распознавание доступно офлайн.'))

    def download_speech_model(self, done):
        from ai_shorts_studio.ai.models import REGISTRY, ModelDownloader

        downloader = ModelDownloader()
        model_dir = self.data_dir / 'models' / REGISTRY['id']
        self.background(lambda progress: downloader.download(model_dir, progress), done, downloader)

    def find_moments(self):
        self.start_analysis(automatic=False)

    def create_shorts(self):
        self.start_analysis(automatic=True)

    def start_analysis(self, *, automatic):
        if self.busy(): return
        if not self.project:
            self.message('Сначала импортируйте видео или откройте проект.'); return
        if self.selected() and not self.apply(): return
        from ai_shorts_studio.ai.models import REGISTRY, model_ready
        from ai_shorts_studio.ui.analysis import AnalysisDialog

        selected = self.selected()
        orientation = '16:9' if selected and selected.width > selected.height else '9:16'
        if selected and selected.mode == 'source':
            orientation = 'source'
        dialog = AnalysisDialog(self.project.metadata, self, automatic=automatic, orientation=orientation)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        orientation = dialog.orientation if automatic else None
        model_dir = self.data_dir / 'models' / REGISTRY['id']
        request = dict(source=self.project.source, fingerprint=self.project.fingerprint,
                       metadata=deepcopy(self.project.metadata), cache_dir=str(self.cache_dir),
                       model_dir=str(model_dir), automatic=automatic, **dialog.parameters())
        if request['audio_index'] is None or model_ready(model_dir):
            self.run_analysis(request, orientation)
            return

        size = sum(item['size'] for item in REGISTRY['files']) / 1024**2
        prompt = QMessageBox(self)
        prompt.setWindowTitle('Модель речи')
        prompt.setText(f'Для распознавания речи можно скачать Whisper small ({size:.0f} МБ, Hugging Face). '
                       'После установки анализ работает офлайн. Можно сразу начать по громкости и движению.')
        download = prompt.addButton('Скачать и начать', QMessageBox.ButtonRole.AcceptRole)
        signals = prompt.addButton('Громкость и движение', QMessageBox.ButtonRole.ActionRole)
        prompt.addButton(QMessageBox.StandardButton.Cancel)
        prompt.exec()
        if prompt.clickedButton() == download:
            self.download_speech_model(lambda _: self.run_analysis(request, orientation))
        elif prompt.clickedButton() == signals:
            self.run_analysis(dict(request, speech_enabled=False), orientation)

    def run_analysis(self, request, orientation):
        from ai_shorts_studio.ai.runner import AnalysisRunner

        runner = AnalysisRunner()

        def failed(error):
            if runner.cancelled.is_set() or request['audio_index'] is None or not request.get('speech_enabled', True):
                self.message(error)
                return
            reply = QMessageBox.question(self, 'Анализ остановлен',
                                         error + '\n\nПродолжить по громкости и движению без распознавания речи?')
            if reply == QMessageBox.StandardButton.Yes:
                self.run_analysis(dict(request, speech_enabled=False), orientation)

        self.background(lambda progress: runner.analyze(request, progress),
                        lambda result: self.analysis_done(result, orientation), runner, failed)

    def analysis_done(self, result, orientation=None):
        previous = {(c['start_us'], c['end_us']): c for c in self.project.suggestions}
        proposals = result['candidates']
        for candidate in proposals:
            old = previous.pop((candidate['start_us'], candidate['end_us']), {})
            for key in ('status', 'clip_id', 'auto_clips'):
                if key in old:
                    candidate[key] = old[key]
        self.history.record(self.project)
        self.project.suggestions = proposals + [item for item in previous.values() if item.get('status') == 'accepted']
        if orientation is not None:
            self.export_candidates(proposals, orientation)
            return
        self.save()
        self.message(f"Анализ завершён. Сегментов речи: {result['segment_count']}, предложений: {len(proposals)}. "
                     'Откройте «Предложения моментов» для просмотра.')

    def clip_from_candidate(self, candidate, template=None):
        metadata = self.project.metadata
        if template is None:
            audio = {str(metadata['audio'][0]['index']): 1} if metadata['audio'] else {}
            template = Clip('', 0, 1, fps=min(30, metadata['fps']), audio=audio)
        clip = deepcopy(template)
        clip.id = uuid4().hex
        clip.name = candidate['name']
        clip.start_us, clip.end_us = candidate['start_us'], candidate['end_us']
        clip.validate(metadata['duration_us'])
        return clip

    def export_candidates(self, proposals, orientation):
        existing = {clip.id: clip for clip in self.project.clips}
        width, height = (1080, 1920) if orientation == '9:16' else (1920, 1080)
        metadata = self.project.metadata
        if orientation == 'source':
            width, height = metadata['width'], metadata['height']
        same_aspect = width * metadata['height'] == height * metadata['width']
        template = self.selected()
        first_new = len(self.project.clips)
        clips = []
        for candidate in proposals:
            if candidate.get('status') == 'rejected':
                continue
            identities = candidate.setdefault('auto_clips', {})
            clip = existing.get(identities.get(orientation))
            if clip is None or clip.width * height != clip.height * width or (clip.mode == 'source') != (orientation == 'source'):
                clip = self.clip_from_candidate(candidate, template)
                clip.width, clip.height = width, height
                clip.mode = 'center' if same_aspect else 'blur'
                if orientation == 'source':
                    clip.use_source_size(metadata)
                identities[orientation] = clip.id
                self.project.clips.append(clip)
            candidate['status'] = 'accepted'
            clips.append(clip)
        self.save()
        if not clips:
            self.message('Анализ завершён. Моментов с достаточными сигналами для экспорта нет.')
            return
        self.refresh(first_new if first_new < len(self.project.clips) else self.project.clips.index(clips[0]))
        self.clips.clearSelection()
        ids = {clip.id for clip in clips}
        for row, clip in enumerate(self.project.clips):
            if clip.id in ids:
                self.clips.item(row).setSelected(True)
        folder = self.store.path.parent / (self.store.path.stem + ' — Нарезка')
        self.start_export(deepcopy(clips), folder)

    def review_moments(self):
        if self.busy(): return
        if not self.project or not self.project.suggestions:
            self.message('Пока нет предложений. Запустите «Найти моменты». Если сильных сигналов нет, программа может не предложить клипы.'); return
        if self.selected() and not self.apply(): return
        from ai_shorts_studio.ui.analysis import ReviewDialog
        dialog=ReviewDialog(self.project.suggestions,self.project.source,self)
        outcome=dialog.exec()
        chosen=dialog.chosen() if outcome==QDialog.DialogCode.Accepted else []
        clips=[]
        for candidate in chosen:
            clip = self.clip_from_candidate(candidate, self.selected())
            clips.append(clip)
            candidate['status'] = 'accepted'
            candidate['clip_id'] = clip.id
        if clips or dialog.proposals!=self.project.suggestions:
            self.history.record(self.project); row=len(self.project.clips)
            self.project.suggestions=dialog.proposals; self.project.clips.extend(clips); self.save()
            for candidate in dialog.proposals:
                if candidate['status'] in ('accepted','rejected'):
                    self.store.feedback(self.project.fingerprint,candidate['start_us'],candidate['end_us'],candidate['status'])
            if clips:
                self.refresh(row); self.clips.clearSelection()
                for index in range(row,len(self.project.clips)): self.clips.item(index).setSelected(True)
                self.statusBar().showMessage(f'Создано клипов: {len(clips)}. Проверьте компоновку и звук через предпросмотр.')

    def add_timecodes(self):
        if self.busy(): return
        if not self.project:
            self.message('Сначала импортируйте видео или откройте проект.'); return
        if self.selected() and not self.apply(): return
        from ai_shorts_studio.ui.timecodes import TimecodesDialog
        metadata=self.project.metadata
        template=deepcopy(self.selected())
        if template is None:
            audio={str(metadata['audio'][0]['index']):1} if metadata['audio'] else {}
            template=Clip('',0,1,fps=min(30,metadata['fps']),audio=audio)
        dialog=TimecodesDialog(metadata['duration_us'],template,self)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        self.append_timecode_clips(dialog.generated)

    def append_timecode_clips(self,clips):
        if not clips: return
        for clip in clips: clip.validate(self.project.metadata['duration_us'])
        row=len(self.project.clips)
        self.history.record(self.project)
        self.project.clips.extend(clips)
        self.save(); self.refresh(row)
        self.clips.clearSelection()
        for index in range(row,len(self.project.clips)):
            self.clips.item(index).setSelected(True)
        self.statusBar().showMessage(f'Создано клипов: {len(clips)}. Они выделены для экспорта.')

    def delete_clip(self):
        if not self.selected() or self.busy(): return
        self.history.record(self.project); del self.project.clips[self.clips.currentRow()]
        self.save(); self.refresh()

    def load_clip(self,row):
        if self.loading or not self.selected(): return
        c = self.selected(); self.name.setText(c.name); self.start.set_us(c.start_us); self.end.set_us(c.end_us)
        self.mode.setCurrentIndex(max(0, self.mode.findData(c.mode))); self.resolution.setCurrentIndex(self.resolution.findData('source' if c.mode == 'source' else f'{c.width}x{c.height}'))
        self.fps.setCurrentText(str(int(c.fps)) if c.fps in (30,60) else 'Исходная')
        for key,controls in self.rects.items():
            r = getattr(c,key)
            for control,value in zip(controls,[r.x,r.y,r.w,r.h]): control.setValue(value)
        self.share.setValue(c.camera_share)
        self.normalize.setChecked(c.normalize_audio); self.duck.setChecked(c.duck_game)
        self.voice.clear(); self.voice.addItem('Не выбрана',None)
        for track in self.project.metadata['audio']:
            self.voice.addItem(f"#{track['index']} · {track['title']}",track['index'])
        self.voice.setCurrentIndex(max(0,self.voice.findData(c.voice_index)))
        while self.audio_layout.rowCount(): self.audio_layout.removeRow(0)
        self.audio_controls = {}
        for a in self.project.metadata['audio']:
            wrapper = QWidget(); line = QHBoxLayout(wrapper); line.setContentsMargins(0,0,0,0)
            check = QCheckBox(a['title']); check.setChecked(str(a['index']) in c.audio)
            gain = self.spin(0,4,2); gain.setValue(c.audio.get(str(a['index']),1))
            line.addWidget(check); line.addWidget(gain); self.audio_layout.addRow(wrapper)
            self.audio_controls[str(a['index'])] = (check,gain)
        self.sync_audio_preview()

    def apply(self):
        old = self.selected()
        if old is None or self.busy(): return False
        c = deepcopy(old)
        c.name = self.name.text().strip() or 'Клип'
        try:
            c.start_us,c.end_us = self.start.value_us(),self.end.value_us()
        except ValueError as error:
            self.message(error); return False
        size = self.resolution.currentData()
        if size == 'source':
            c.use_source_size(self.project.metadata)
        else:
            c.mode = self.mode.currentData()
            c.width, c.height = map(int, size.split('x'))
            c.fps = self.project.metadata['fps'] if self.fps.currentIndex()==0 else float(self.fps.currentText())
        c.camera_share = self.share.value()
        c.normalize_audio=self.normalize.isChecked(); c.duck_game=self.duck.isChecked(); c.voice_index=self.voice.currentData()
        c.audio = {i:g.value() for i,(check,g) in self.audio_controls.items() if check.isChecked()}
        for key,controls in self.rects.items(): setattr(c,key,Rect(*[s.value() for s in controls]))
        try:
            c.validate(self.project.metadata['duration_us'])
            if c.fps > self.project.metadata['fps']+.01: raise ValueError('Выберите частоту не выше исходной')
        except ValueError as e:
            self.message(e); return False
        row = self.clips.currentRow()
        if c != old:
            self.history.record(self.project); self.project.clips[row] = c; self.save(); self.refresh(row)
        self.sync_audio_preview()
        return True

    def undo(self,redo):
        if not self.project or self.busy(): return
        self.project = self.history.move(self.project,redo); self.save(); self.refresh()

    def show_source(self):
        if not self.project: return
        self.proxy_view = False
        self.source_view = True; self.view_label.setText('Исходник · выбранный многодорожечный микс слушайте через предпросмотр')
        self.player.setSource(QUrl.fromLocalFile(self.project.source))

    def sync_audio_preview(self):
        if not self.project or not self.source_view or not self.selected(): return
        if self.proxy_view:
            self.player.setActiveAudioTrack(0 if self.project.metadata['audio'] else -1)
            return
        indexes = [a['index'] for a in self.project.metadata['audio']]
        selected = list(self.selected().audio)
        self.player.setActiveAudioTrack(indexes.index(int(selected[0])) if selected else -1)

    def toggle_play(self):
        self.player.pause() if self.player.playbackState()==QMediaPlayer.PlaybackState.PlayingState else self.player.play()

    def position_changed(self,ms):
        if not self.seek.isSliderDown(): self.seek.setValue(ms)
        h,r = divmod(ms,3600000); m,r = divmod(r,60000); s,milli = divmod(r,1000)
        self.time.setText(f'{h:02}:{m:02}:{s:02}.{milli:03}')

    def mark(self,end):
        if not self.source_view:
            self.message('Установите границы на исходнике'); return
        (self.end if end else self.start).set_us(self.player.position()*1000)

    def select_regions(self):
        if not self.selected() or self.busy(): return
        from ai_shorts_studio.media.probe import binary,run
        path=self.cache_dir/(uuid4().hex+'.png')
        source=self.project.source
        try:
            position=self.player.position()/1000 if self.source_view else self.start.value_us()/1e6
        except ValueError as error:
            self.message(error); return
        position=min(position,max(0,self.project.metadata['duration_us']/1e6-.1))
        def task(progress):
            run([binary('ffmpeg'),'-v','error','-i',source,'-ss',str(position),
                 '-frames:v','1','-vf','scale=trunc(iw*sar/2)*2:ih,setsar=1,scale=1280:720:force_original_aspect_ratio=decrease',
                 '-update','1','-n',str(path)],timeout=120)
            return path
        self.background(task,self.regions_ready)

    def regions_ready(self,path):
        from ai_shorts_studio.ui.regions import RegionDialog
        regions={key:Rect(*[w.value() for w in controls]) for key,controls in self.rects.items()}
        dialog=RegionDialog(path,regions,self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            for key,controls in self.rects.items():
                rect=dialog.canvas.regions[key]
                for control,value in zip(controls,[rect.x,rect.y,rect.w,rect.h]): control.setValue(value)
        Path(path).unlink(missing_ok=True)

    def preview(self):
        if not self.apply(): return
        c = deepcopy(self.selected()); c.end_us = min(c.end_us,c.start_us+8_000_000)
        target = self.cache_dir/(uuid4().hex+'.mp4'); renderer = Renderer()
        source,metadata = self.project.source,deepcopy(self.project.metadata)
        self.background(lambda p:renderer.render(source,c,metadata,target,progress=p),self.play_render,renderer)

    def play_render(self,path):
        self.proxy_view = False
        self.source_view = False; self.view_label.setText('Рендер · реальная компоновка и выбранный микс · до 8 секунд')
        self.player.setSource(QUrl.fromLocalFile(path)); self.player.play()

    def proxy(self):
        if not self.project or self.busy(): return
        m = self.project.metadata
        # Preserve the source display aspect ratio for accurate original-timeline markers.
        from ai_shorts_studio.media.proxy import ProxyRenderer
        renderer = ProxyRenderer(); target = self.cache_dir/(uuid4().hex+'.mp4')
        self.background(lambda p:renderer.render_proxy(self.project.source,m,target,p),self.play_proxy,renderer)

    def play_proxy(self,path):
        self.proxy_view = True
        self.source_view = True; self.view_label.setText('Прокси · границы относятся к оригиналу; экспорт из оригинала')
        self.player.setSource(QUrl.fromLocalFile(path))

    def export(self):
        rows = sorted({self.clips.row(i) for i in self.clips.selectedItems()})
        if not rows or self.busy(): return
        if self.advanced.isChecked() and not self.apply(): return
        folder = QFileDialog.getExistingDirectory(self,'Папка для экспорта')
        if not folder: return
        clips = [deepcopy(self.project.clips[i]) for i in rows]
        size = self.resolution.currentData()
        for clip in clips:
            if size == 'source':
                clip.use_source_size(self.project.metadata)
                continue
            if clip.mode == 'source':
                clip.mode = 'center'
            clip.width, clip.height = map(int, size.split('x'))
            if not self.advanced.isChecked():
                clip.mode = 'center'
                clip.crop = Rect()
                clip.camera = Rect(0, 0, .25, .25)
                clip.game = Rect()
        if not self.advanced.isChecked():
            self.start_export(clips, folder)
            return
        upscaled = any(c.width>self.project.metadata['width'] or c.height>self.project.metadata['height'] or c.mode in ('gaming','manual') for c in clips)
        if upscaled and QMessageBox.question(self,'Масштабирование','Возможны увеличение исходника или выбранных областей и потеря детализации. Продолжить?') != QMessageBox.StandardButton.Yes: return
        dialog = QDialog(self); dialog.setWindowTitle('Кодировщик'); layout = QVBoxLayout(dialog)
        selector = QComboBox()
        selector.addItem('Автоматически · NVIDIA NVENC или libx264', 'auto')
        selector.addItem('Программный H.264 (libx264)', 'libx264')
        layout.addWidget(selector)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel); layout.addWidget(buttons)
        buttons.accepted.connect(dialog.accept); buttons.rejected.connect(dialog.reject)
        if dialog.exec()!=QDialog.DialogCode.Accepted: return
        self.start_export(clips, folder, selector.currentData())

    def start_export(self, clips, folder, preference="auto"):
        renderer = Renderer()
        source, metadata = self.project.source, deepcopy(self.project.metadata)
        db_path = self.store.path
        channels = self.channels.isChecked()
        hooks = {}
        for candidate in self.project.suggestions:
            clip_ids = [candidate.get('clip_id'), *candidate.get('auto_clips', {}).values()]
            for clip_id in clip_ids:
                if clip_id:
                    hooks[clip_id] = candidate.get('hook', '')
        self.background(
            lambda progress: export_clips(db_path, source, metadata, clips, folder, renderer, progress,
                                          preference, cover_hooks=hooks, channels=channels),
            self.export_done, renderer,
        )

    def export_done(self, results):
        folder = results[0].video.parent.parent
        self.message(f'Комплектов MP4 и JPG: {len(results)}\n\nПапка:\n{folder}')

    def edit_cover(self):
        clip = self.selected()
        if clip is None or self.busy():
            return
        previous = self.store.current_export(clip.id)
        if previous is None or not previous.video.is_file():
            self.message('Сначала экспортируйте этот клип. Обложка берётся из готового MP4.')
            return
        from ai_shorts_studio.ui.cover import CoverDialog

        channels = self.channels.isChecked()
        try:
            dialog = CoverDialog(previous.video, hook=previous.hook, frame_us=previous.frame_us, channels=channels, parent=self)
        except (OSError, ValueError, RuntimeError) as error:
            self.message(error)
            return
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        renderer = Renderer()
        db_path = self.store.path
        frame_us, hook = dialog.frame_us, dialog.hook
        clip = deepcopy(clip)
        self.background(
            lambda progress: revise_cover(db_path, clip, previous, frame_us, hook, renderer, progress, channels),
            lambda result: self.message(f'Обложка сохранена:\n{result.cover}'), renderer,
        )

    def rate_clip(self):
        clip = self.selected()
        if clip is None or self.busy():
            return
        from ai_shorts_studio.ui.analysis import PublicationDialog

        status, note = self.store.publication_rating(clip.id)
        dialog = PublicationDialog(clip.name, status, note, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.store.rate_publication(clip.id, dialog.status, dialog.note)
            self.statusBar().showMessage('Оценка клипа сохранена')

    def cache_info(self):
        if self.busy(): return
        files = [p for p in self.cache_dir.rglob('*') if p.is_file()]
        size = sum(p.stat().st_size for p in files)
        if QMessageBox.question(self,'Кэш',f'{size/1024**2:.1f} МБ · {self.cache_dir}\nОчистить прокси, предпросмотры и кэш анализа (включая текст речи)? Модели и проекты сохранятся.')==QMessageBox.StandardButton.Yes:
            self.player.stop(); self.player.setSource(QUrl())
            for p in files:
                try: p.unlink()
                except OSError: pass
            for name in ('analysis','analysis-jobs'):
                folder=self.cache_dir/name
                if folder.exists(): shutil.rmtree(folder,ignore_errors=True)
            self.show_source()

    def write_settings(self,data):
        target = self.data_dir/'settings.json'; temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8'); temporary.replace(target)

    def restore_settings(self):
        try:
            path = json.loads((self.data_dir/'settings.json').read_text(encoding='utf-8')).get('last_project')
            if path and Path(path).is_file(): QTimer.singleShot(100,lambda:self.read_project(path))
        except (OSError,ValueError): pass

    def closeEvent(self,event):
        if self.busy():
            self.cancel(); self.message('Дождитесь завершения или отмены задачи перед закрытием.'); event.ignore(); return
        self.player.stop()
        if self.store: self.store.close()
        event.accept()
