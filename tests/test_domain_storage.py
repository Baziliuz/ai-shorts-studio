import pytest
from ai_shorts_studio.domain.model import Clip,Rect,Project,History
from ai_shorts_studio.storage.project import Store

@pytest.mark.parametrize('start,end',[(-1,2),(2,2),(3,2),(0,101)])
def test_boundaries(start,end):
    with pytest.raises(ValueError): Clip('x',start,end).validate(100)

def test_rect():
    with pytest.raises(ValueError): Rect(.8,0,.3,1).validate()

def test_roundtrip_and_recovery(tmp_path):
    path = tmp_path/'проект с пробелами.sqlite'
    p = Project('исходник.mkv','digest',{'duration_us':10_000_000},[
        Clip('первый',0,1_000_000),Clip('второй',2_000_000,3_000_000),Clip('третий',4_000_000,8_000_000)])
    store = Store(path); store.save(p); store.job('one','running','unfinished'); store.job('two','completed','finished'); store.close()
    store = Store(path)
    assert store.load()==p
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',('one',)).fetchone()[0]=='interrupted'
    assert store.db.execute('SELECT status FROM jobs WHERE id=?',('two',)).fetchone()[0]=='completed'
    store.close()

def test_undo_redo():
    p = Project('source','digest',{},[Clip('first',0,100)])
    h = History(); h.record(p); p.clips[0].crop = Rect(.1,.1,.5,.5)
    p = h.move(p); assert p.clips[0].crop==Rect()
    p = h.move(p,True); assert p.clips[0].crop.x==.1
