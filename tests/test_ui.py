import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication
from ai_shorts_studio.ui.window import Window

def test_window(tmp_path):
    app=QApplication.instance() or QApplication([])
    data=tmp_path/'data'; cache=tmp_path/'cache'; data.mkdir(); cache.mkdir()
    window=Window(data,cache)
    assert window.windowTitle().startswith('AI Shorts Studio')
    assert window.clips.count()==0
    window.show(); app.processEvents(); window.close()

def test_project_edit_roundtrip(tmp_path):
    from ai_shorts_studio.domain.model import Project,Clip
    from ai_shorts_studio.storage.project import Store
    app=QApplication.instance() or QApplication([])
    data=tmp_path/'data'; cache=tmp_path/'cache'; data.mkdir(); cache.mkdir()
    window=Window(data,cache)
    metadata=dict(duration_us=10_000_000,width=1920,height=1080,fps=30,size=1024,video_codec='h264',video_index=0,sar='1:1',rotation=0,audio=[])
    project=Project(str(tmp_path/'missing.mp4'),'digest',metadata,[Clip('Один',0,2_000_000),Clip('Два',2_000_000,4_000_000),Clip('Три',4_000_000,8_000_000)])
    window.set_project(Store(tmp_path/'project.sqlite'),project)
    assert window.resolution.currentData()=='720x1280'
    window.name.setText('Изменён'); window.mode.setCurrentIndex(3)
    assert window.apply()
    assert window.project.clips[0].mode=='gaming'
    window.undo(False); assert window.project.clips[0].name=='Один'
    window.undo(True); assert window.project.clips[0].name=='Изменён'
    from ai_shorts_studio.domain.timecodes import clips_from_starts
    window.append_timecode_clips(clips_from_starts('00:01\n00:09',10_000_000,3_000_000,window.selected()))
    assert len(window.project.clips)==5 and len(window.clips.selectedItems())==2
    assert window.store.load().clips[-1].end_us==10_000_000
    window.undo(False); assert len(window.project.clips)==3
    window.undo(True); assert len(window.project.clips)==5
    window.close()
    store=Store(tmp_path/'project.sqlite'); assert store.load().clips[0].mode=='gaming'; store.close()

def test_region_selection_coordinates(tmp_path):
    from PySide6.QtGui import QPixmap,QColor
    from PySide6.QtCore import QPointF
    from ai_shorts_studio.ui.regions import Canvas
    from ai_shorts_studio.domain.model import Rect
    app=QApplication.instance() or QApplication([])
    image=QPixmap(640,360); image.fill(QColor('blue')); path=tmp_path/'frame.png'; image.save(str(path))
    canvas=Canvas(path,{'crop':Rect(),'camera':Rect(),'game':Rect()}); canvas.resize(800,600)
    rect=canvas.image_rect()
    point=canvas.normalized(QPointF(rect.x()+rect.width()*.25,rect.y()+rect.height()*.75))
    assert abs(point.x()-.25)<.001 and abs(point.y()-.75)<.001
    outside=canvas.normalized(QPointF(-100,-100)); assert outside.x()==outside.y()==0

def test_timecodes_dialog_validation(tmp_path):
    from ai_shorts_studio.ui.timecodes import TimecodesDialog
    from ai_shorts_studio.domain.model import Clip
    app=QApplication.instance() or QApplication([])
    dialog=TimecodesDialog(100_000_000,Clip('template',0,1))
    dialog.text.setPlainText('00:10\nошибка'); dialog.accept()
    assert dialog.result()!=dialog.DialogCode.Accepted and dialog.error.text()
    dialog.text.setPlainText('00:10\n00:50'); dialog.accept()
    assert dialog.result()==dialog.DialogCode.Accepted
    assert [c.start_us for c in dialog.generated]==[10_000_000,50_000_000]

def test_analysis_dialog_and_review(tmp_path):
    from ai_shorts_studio.ui.analysis import AnalysisDialog,PublicationDialog,ReviewDialog
    from PySide6.QtCore import Qt
    app=QApplication.instance() or QApplication([])
    setup=AnalysisDialog({'audio':[dict(index=2,title='Микрофон')]}, automatic=True, orientation='16:9')
    assert setup.orientation=='16:9'
    setup.profile.setCurrentIndex(setup.profile.findData('game'))
    assert setup.parameters()==dict(selection='content', profile='game', audio_index=2, language=None)
    setup.language.setCurrentIndex(setup.language.findData('ru'))
    assert setup.parameters()['language'] == 'ru'
    setup.accept()
    assert setup.result()==setup.DialogCode.Accepted
    silent=AnalysisDialog({'audio':[]})
    assert silent.parameters()['audio_index'] is None and not silent.language.isEnabled()
    feedback=PublicationDialog('Момент', 'selection_error', ' Нужна реакция ')
    assert (feedback.status, feedback.note)==('selection_error', 'Нужна реакция')
    proposals=[dict(id='c',name='Ничего себе',start_us=0,end_us=10_000_000,score=70,
                    evidence=['Есть речь'],text='<b>text is data</b>',status='pending')]
    review=ReviewDialog(proposals,str(tmp_path/'missing.mp4'))
    assert review.chosen()==[]
    review.items.item(0).setCheckState(Qt.CheckState.Checked)
    assert len(review.chosen())==1
    review.reject_current()
    assert review.chosen()==[] and review.proposals[0]['status']=='rejected'
    assert proposals[0]['status']=='pending'
    assert review.details.textFormat()==Qt.TextFormat.PlainText
    review.reject()
