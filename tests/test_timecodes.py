import pytest
from ai_shorts_studio.domain.model import Clip,Rect
from ai_shorts_studio.domain.timecodes import clips_from_starts

@pytest.mark.parametrize('text,start', [('05:20',320000000),('01:02:03.123456',3723123456),('90:01,5',5401500000),('12.000001',12000001)])
def test_precision(text,start):
    clip=clips_from_starts(text,10_000_000_000,30_000_000)[0]
    assert clip.start_us==start and clip.end_us==start+30_000_000

@pytest.mark.parametrize('text',['','00:60','1:60:00','-1','wrong','12:20 — 12:40','00:10\n10','100'])
def test_invalid_atomic_input(text):
    with pytest.raises(ValueError): clips_from_starts(text,100_000_000,30_000_000)

def test_template_and_end():
    template=Clip('original',0,100,mode='gaming',camera=Rect(.1,.1,.2,.2),audio={'1':.5})
    result=clips_from_starts('00:01 Название\n\n00:09',10_000_000,3_000_000,template)
    assert [c.end_us for c in result]==[4_000_000,10_000_000]
    assert result[0].name=='Название' and result[1].name=='Клип 00:09'
    assert len({template.id, result[0].id,result[1].id})==3
    result[0].camera.x=.3
    assert template.camera.x==result[1].camera.x==.1
    assert result[0].audio=={'1':.5}
