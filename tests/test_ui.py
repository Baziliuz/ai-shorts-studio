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
