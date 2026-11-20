import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import pytest
from PySide6.QtWidgets import QApplication
from ai_shorts_studio.ui.timecode_edit import TimecodeEdit

@pytest.fixture
def editor():
    app=QApplication.instance() or QApplication([])
    widget=TimecodeEdit()
    yield widget
    widget.close()

@pytest.mark.parametrize('text,value',[
    ('01:25:30',5_130_000_000),('00:00:00.000001',1),
    ('25:00:00.123456',90_000_123456),('123:59:59,5',446_399_500000)])
def test_parse_and_roundtrip(editor,text,value):
    editor.setText(text)
    assert editor.value_us()==value
    editor.set_us(value)
    assert editor.value_us()==value
    assert editor.text().split(':')[0]==text.split(':')[0]

@pytest.mark.parametrize('text',['','01:60:00','01:00:60','-1:00:00','1.5','01:01','01:00:00.1234567'])
def test_reject_invalid_time(editor,text):
    editor.setText(text)
    with pytest.raises(ValueError): editor.value_us()

def test_project_keeps_hour_boundaries(tmp_path):
    from ai_shorts_studio.domain.model import Project,Clip
    from ai_shorts_studio.storage.project import Store
    from ai_shorts_studio.ui.window import Window
    app=QApplication.instance() or QApplication([])
    data=tmp_path/'data'; cache=tmp_path/'cache'; data.mkdir(); cache.mkdir()
    window=Window(data,cache)
    metadata=dict(duration_us=10_000_000_000,width=1920,height=1080,fps=30,size=1024,video_codec='h264',video_index=0,sar='1:1',rotation=0,audio=[])
    window.set_project(Store(tmp_path/'hours.sqlite'),Project(str(tmp_path/'missing.mp4'),'digest',metadata,[Clip('hours',0,1_000_000)]))
    window.start.setText('01:25:30.000001'); window.end.setText('01:26:00.123456')
    assert window.apply()
    saved=window.store.load().clips[0]
    assert saved.start_us==5_130_000_001 and saved.end_us==5_160_123_456
    assert window.start.text()=='01:25:30.000001'
    window.undo(False); assert window.selected().start_us==0
    window.undo(True); assert window.selected().end_us==5_160_123_456
    window.close()
