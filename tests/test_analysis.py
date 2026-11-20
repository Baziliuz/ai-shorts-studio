import hashlib
import json
from pathlib import Path
import pytest
from ai_shorts_studio.ai.ranking import candidates
from ai_shorts_studio.ai.models import ModelDownloader,verify_model
from ai_shorts_studio.ai.pipeline import cache_key,normalize_signals
from ai_shorts_studio.domain.model import Clip,Project
from ai_shorts_studio.media.render import Cancelled
from ai_shorts_studio.storage.project import Store


def test_evidence_and_strict_bounds():
    segments=[dict(id='s1',start_us=20_000_000,end_us=28_000_000,text='Ничего себе, получилось!'),
              dict(id='s2',start_us=30_000_000,end_us=40_000_000,text='Продолжим дальше.')]
    signals=[dict(time_us=21_000_000,loudness=1,motion=.8)]
    result=candidates(segments,signals,90_000_000,30_000_000,10_000_000,30_000_000,10)
    assert result and len(result)==1
    item=result[0]
    assert 10_000_000<=item['end_us']-item['start_us']<=30_000_000
    assert set(item['segment_ids'])<={'s1','s2'}
    assert item['name'].startswith('Ничего себе') and 0<=item['score']<=100
    assert any('громкости' in text for text in item['evidence'])


def test_no_filler_and_no_invented_speech():
    assert candidates([],[],90_000_000)==[]
    result=candidates([],[dict(time_us=30_000_000,loudness=1,motion=1)],90_000_000)
    assert result[0]['segment_ids']==[] and result[0]['text']==''
    assert 'Фрагмент' in result[0]['name']
    with pytest.raises(ValueError): candidates([dict(id='x',start_us=-1,end_us=4,text='wow')],[],100)


def test_cache_independent_of_crop_and_clip_length():
    request=dict(fingerprint='hash',audio_index=1,language='ru',target_us=30,crop='old')
    key=cache_key(request)
    assert cache_key(dict(request,crop='new',target_us=60))==key
    assert cache_key(dict(request,audio_index=2))!=key
    assert cache_key(dict(request,language='en'))!=key
    assert normalize_signals([dict(time_us=0,rms=0,delta=0)])[0]['loudness']==0


def test_suggestions_feedback_and_legacy(tmp_path):
    path=tmp_path/'project.sqlite'; store=Store(path)
    project=Project('file','hash',dict(duration_us=100),[Clip('old',0,100)],suggestions=[dict(id='candidate',status='rejected')])
    store.save(project); store.feedback('hash',0,100,'rejected'); store.close()
    store=Store(path); assert store.load()==project
    assert store.db.execute('SELECT status FROM feedback').fetchone()[0]=='rejected'
    store.close()
    data=project.serialize(); del data['suggestions']
    for key in ('normalize_audio','duck_game','voice_index'): del data['clips'][0][key]
    loaded=Project.load(data)
    assert loaded.suggestions==[] and not loaded.clips[0].normalize_audio


def test_model_download_integrity_and_resume(tmp_path,monkeypatch):
    import ai_shorts_studio.ai.models as models
    data=b'example model bytes'
    manifest=dict(repo='test/model',revision='fixed',files=[dict(name='model.bin',size=len(data),sha256=hashlib.sha256(data).hexdigest())])
    monkeypatch.setattr(models,'REGISTRY',manifest)
    (tmp_path/'model.bin.part').write_bytes(data[:5])
    class Response:
        status=206
        headers={'Content-Range':f'bytes 5-{len(data)-1}/{len(data)}'}
        def __init__(self): self.block=data[5:]
        def read(self,n): result=self.block; self.block=b''; return result
        def __enter__(self): return self
        def __exit__(self,*args): pass
    def open_url(request,**kwargs):
        assert request.get_header('Range')=='bytes=5-'
        return Response()
    monkeypatch.setattr(models.urllib.request,'urlopen',open_url)
    values=[]; ModelDownloader().download(tmp_path,values.append)
    assert (tmp_path/'model.bin').read_bytes()==data and values[-1]==1
    verify_model(tmp_path)
    (tmp_path/'model.bin').write_bytes(b'corrupted')
    with pytest.raises(ValueError): verify_model(tmp_path)
    downloader=ModelDownloader(); downloader.cancel()
    with pytest.raises(Cancelled): downloader.download(tmp_path)


def test_silent_worker_and_recovery(tmp_path):
    from ai_shorts_studio.media.probe import run,binary,probe,fingerprint
    from ai_shorts_studio.ai.runner import AnalysisRunner
    source=tmp_path/'silent.mkv'
    run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=15:d=3','-c:v','libx264',str(source)])
    request=dict(source=str(source),fingerprint=fingerprint(source),metadata=probe(source),cache_dir=str(tmp_path/'cache'),
        model_dir=str(tmp_path/'missing-model'),audio_index=None,language=None,target_us=2_000_000,min_us=1_000_000,max_us=3_000_000,limit=3)
    result=AnalysisRunner().analyze(request)
    assert result['segment_count']==0
    saved=list((tmp_path/'cache'/'analysis').rglob('block-*.json'))
    assert len(saved)==1
    original=saved[0].stat().st_mtime_ns
    assert AnalysisRunner().analyze(request)['segment_count']==0
    assert saved[0].stat().st_mtime_ns==original
    runner=AnalysisRunner(); runner.cancel()
    with pytest.raises(Cancelled): runner.analyze(request)


def test_running_analysis_cancels_descendants(tmp_path,monkeypatch):
    import subprocess,sys,threading,time
    import ai_shorts_studio.ai.runner as runner_module
    pid_path=tmp_path/'child.pid'; real_popen=subprocess.Popen
    code=("import subprocess,sys,time;from pathlib import Path;"
          "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
          f"Path({str(pid_path)!r}).write_text(str(p.pid));time.sleep(30)")
    def launch(command,**kwargs):
        if '--analysis-worker' in command: command=[sys.executable,'-c',code]
        return real_popen(command,**kwargs)
    monkeypatch.setattr(runner_module.subprocess,'Popen',launch)
    runner=runner_module.AnalysisRunner()
    def cancel_when_child_exists():
        deadline = time.monotonic() + 10
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        runner.cancel()

    cancellation = threading.Thread(target=cancel_when_child_exists, daemon=True)
    cancellation.start()
    try:
        with pytest.raises(Cancelled): runner.analyze(dict(cache_dir=str(tmp_path/'cache')))
    finally:
        cancellation.join(timeout=10)
    assert runner.process is None and pid_path.exists()
    assert not list((tmp_path/'cache'/'analysis-jobs').iterdir())
    pid=int(pid_path.read_text())
    if sys.platform=='win32':
        result=subprocess.check_output(['tasklist','/FI',f'PID eq {pid}','/NH'],text=True)
        assert f' {pid} ' not in result
    else:
        status=Path(f'/proc/{pid}/stat')
        deadline = time.monotonic() + 5
        while True:
            try:
                state = status.read_text().split()[2]
            except FileNotFoundError:
                break
            if state == 'Z':
                break
            assert time.monotonic() < deadline, f"Analysis child {pid} is still running ({state})"
            time.sleep(.02)


def test_schema_one_migrates_with_old_clip(tmp_path):
    import sqlite3
    path=tmp_path/'legacy.sqlite'
    data=Project('source','hash',dict(duration_us=100),[Clip('old',0,100)]).serialize()
    del data['suggestions']
    for key in ('normalize_audio','duck_game','voice_index'): del data['clips'][0][key]
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE project (id INTEGER PRIMARY KEY, data TEXT)')
    db.execute('INSERT INTO project VALUES (1,?)',(json.dumps(data),)); db.execute('PRAGMA user_version=1'); db.commit(); db.close()
    store=Store(path)
    assert store.db.execute('PRAGMA user_version').fetchone()[0]==4
    assert store.load().clips[0].normalize_audio is False
    assert store.load().suggestions==[]
    store.close()
    backup=sqlite3.connect(str(path)+'.v1-backup.sqlite')
    assert backup.execute('PRAGMA user_version').fetchone()[0]==1
    backup.close()
