import json
from pathlib import Path
import subprocess
import threading
import time
import pytest
from ai_shorts_studio.domain.model import Clip,Rect
from ai_shorts_studio.media.probe import binary,run,probe,fingerprint
from ai_shorts_studio.media import render
from ai_shorts_studio.media.render import Renderer,Cancelled,encoder_available,capabilities

@pytest.fixture(scope='module')
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp('медиа с пробелами')/'исходный файл.mkv'
    run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=3',
         '-f','lavfi','-i',"aevalsrc=if(between(t\\,1\\,2)\\,0.5*sin(2*PI*440*t)\\,0):s=48000:d=3",
         '-f','lavfi','-i','sine=frequency=880:duration=3',
         '-map','0:v','-map','1:a','-map','2:a','-c:v','libx264','-preset','ultrafast','-c:a','pcm_s16le',str(path)])
    return path

def test_probe_and_corrupt(source,tmp_path):
    m = probe(source); assert len(m['audio'])==2; assert m['duration_us']==3_000_000
    assert fingerprint(source)==fingerprint(source)
    broken = tmp_path/'broken.mp4'; broken.write_bytes(b'broken')
    with pytest.raises(RuntimeError): probe(broken)
    with pytest.raises(FileNotFoundError): fingerprint(tmp_path/'missing')

@pytest.mark.parametrize('mode',['center','manual','blur','gaming'])
def test_compositions(source,tmp_path,mode):
    m = probe(source)
    c = Clip(mode,733333,1533333,mode=mode,audio={'1':.8,'2':.2},crop=Rect(.1,.1,.8,.8),game=Rect(.25,0,.75,1))
    out = tmp_path/(mode+'.mp4'); values=[]
    Renderer().render(source,c,m,out,progress=values.append)
    result = probe(out)
    assert (result['width'],result['height'])==(720,1280)
    assert abs(result['duration_us']-800000) <= 1_000_000/30
    assert len(result['audio'])==1 and values[-1]==1
    with pytest.raises(FileExistsError): Renderer().render(source,c,m,out)
    assert not list(tmp_path.glob('.ai-shorts-render-*'))

def test_silent_landscape(source,tmp_path):
    out = tmp_path/'silent.mp4'
    Renderer().render(source,Clip('silent',0,500000,width=1280,height=720),probe(source),out)
    assert probe(out)['audio']==[]

def test_cancel_preserves_files(source,tmp_path):
    before = fingerprint(source)
    out = tmp_path/'cancelled.mp4'; renderer = Renderer(); renderer.cancel()
    with pytest.raises(Cancelled): renderer.render(source,Clip('c',0,3_000_000),probe(source),out)
    assert not out.exists() and fingerprint(source)==before
    assert not list(tmp_path.glob('.ai-shorts-render-*'))

def test_nvenc_and_filters():
    caps=capabilities()
    assert caps['software']
    assert isinstance(caps['nvenc'],bool)
    assert not encoder_available('nonexistent_encoder')

@pytest.mark.parametrize(('nvenc', 'preference', 'expected'), [
    (True, 'auto', 'h264_nvenc'),
    (False, 'auto', 'libx264'),
    (False, 'h264_nvenc', 'libx264'),
    (True, 'libx264', 'libx264'),
])
def test_export_encoder_selection(monkeypatch, nvenc, preference, expected):
    monkeypatch.setattr(render, 'capabilities', lambda: {'nvenc': nvenc, 'software': True})
    assert render.choose_encoder(preference) == expected

@pytest.mark.parametrize(('overrides', 'message'), [
    ({'width': 900}, 'Разрешение'),
    ({'duration_us': 200_000}, 'Длительность'),
    ({'fps': 60}, 'Частота кадров'),
    ({'video_codec': 'hevc'}, 'H.264'),
    ({'audio': [{'codec': 'opus'}]}, 'звуковой дорожки'),
])
def test_verify_output_before_publish(monkeypatch, tmp_path, overrides, message):
    clip = Clip('Экспорт', 0, 1_000_000)
    result = dict(width=720, height=1280, duration_us=1_000_000, fps=30,
                  video_codec='h264', audio=[{'codec': 'aac'}])
    result.update(overrides)
    monkeypatch.setattr(render, 'probe', lambda path, *, strict_fps: result)
    with pytest.raises(RuntimeError, match=message):
        render.validate_export(tmp_path / 'staged.mp4', clip, 1, audio=True)

def test_cancel_during_validation_preserves_previous_export(source, tmp_path, monkeypatch):
    previous = tmp_path / 'предыдущий.mp4'
    previous.write_bytes(b'confirmed')
    out = tmp_path / 'повторный.mp4'
    renderer = Renderer()
    validate = render.validate_export

    def cancel_after_validation(path, clip, duration, audio):
        assert path.parent.name.startswith('.ai-shorts-render-')
        validate(path, clip, duration, audio)
        renderer.cancel()

    monkeypatch.setattr(render, 'validate_export', cancel_after_validation)
    with pytest.raises(Cancelled):
        renderer.render(source, Clip('Клип', 0, 500_000), probe(source), out)
    assert previous.read_bytes() == b'confirmed'
    assert not out.exists()
    assert not list(tmp_path.glob('.ai-shorts-render-*'))

def test_video_and_audio_markers(tmp_path):
    """Color transition and an audio onset share t=1; measure both after trim."""
    source = tmp_path/'markers.mkv'
    run([binary('ffmpeg'),'-v','error','-f','lavfi','-i',
         'color=red:s=160x90:r=30:d=1','-f','lavfi','-i','color=blue:s=160x90:r=30:d=2',
         '-f','lavfi','-i',"aevalsrc=if(gte(t\\,1)\\,0.5*sin(2*PI*440*t)\\,0):s=48000:d=3",
         '-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
         '-map','[v]','-map','2:a','-c:v','libx264','-c:a','pcm_s16le',str(source)])
    out = tmp_path/'trim.mp4'; c = Clip('markers',733333,2400000,audio={'1':1})
    Renderer().render(source,c,probe(source),out)
    import array
    video = subprocess.check_output([binary('ffmpeg'),'-v','error','-i',str(out),'-vf','scale=1:1','-f','rawvideo','-pix_fmt','rgb24','-'])
    first_blue = next(i for i in range(len(video)//3) if video[i*3+2]>video[i*3]) / 30
    samples=array.array('f',subprocess.check_output([binary('ffmpeg'),'-v','error','-i',str(out),'-vn','-ac','1','-ar','48000','-f','f32le','-']))
    onset=next(i for i,v in enumerate(samples) if abs(v)>.05)/48000
    expected=1-c.start_us/1e6
    assert abs(first_blue-expected)<=1/30+.001
    assert abs(onset-expected)<.1
    assert abs(onset-first_blue)<.1

@pytest.mark.parametrize(('start_us', 'end_us', 'expected_onset'), [
    (800_000, 2_300_000, .6),
    (0, 800_000, None),
])
def test_delayed_audio_keeps_original_timeline(tmp_path, start_us, end_us, expected_onset):
    import array

    source = tmp_path / 'delayed-track.mkv'
    run([binary('ffmpeg'), '-v', 'error',
         '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=3',
         '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono:d=3',
         '-itsoffset', '1.4', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
         '-map', '0:v', '-map', '1:a', '-map', '2:a', '-c:v', 'libx264', '-c:a', 'pcm_s16le',
         str(source)])
    metadata = probe(source)
    tracks = metadata['audio'] if expected_onset is not None else metadata['audio'][1:]
    audio = {str(track['index']): 1 for track in tracks}
    clip = Clip('Синхронизация', start_us, end_us, audio=audio)
    out = tmp_path / 'sync.mp4'
    Renderer().render(source, clip, metadata, out)
    samples = array.array('f', subprocess.check_output([
        binary('ffmpeg'), '-v', 'error', '-i', str(out), '-vn',
        '-ac', '1', '-ar', '48000', '-f', 'f32le', '-',
    ]))
    assert [track['codec'] for track in probe(out)['audio']] == ['aac']
    onset = next((i / 48000 for i, sample in enumerate(samples) if abs(sample) > .05), None)
    if expected_onset is None:
        assert onset is None
    else:
        assert onset == pytest.approx(expected_onset, abs=.05)

def test_vfr_and_no_audio(tmp_path):
    source=tmp_path/'vfr.mkv'
    run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=2',
         '-vf',"select='if(lt(t,1),1,not(mod(n,3)))'",'-fps_mode','vfr','-c:v','libx264',str(source)])
    m=probe(source); assert not m['audio']
    out=tmp_path/'vfr-output.mp4'
    Renderer().render(source,Clip('vfr',100000,1_500_000,fps=min(30,m['fps'])),m,out)
    assert abs(probe(out)['duration_us']-1_400_000)<100000

def test_proxy(source,tmp_path):
    from ai_shorts_studio.media.proxy import ProxyRenderer
    out=tmp_path/'proxy.mp4'; m=probe(source)
    ProxyRenderer().render_proxy(source,m,out,lambda _:None)
    result=probe(out)
    assert abs(result['width']/result['height']-m['width']/m['height'])<.02
    assert abs(result['duration_us']-m['duration_us'])<100000
    assert len(result['audio'])==1

def test_live_cancel(source,tmp_path):
    from ai_shorts_studio.domain.model import Clip
    renderer=Renderer(); out=tmp_path/'live-cancel.mp4'
    def progress(value): renderer.cancel()
    with pytest.raises(Cancelled):
        renderer.render(source,Clip('cancel',0,3_000_000,width=1080,height=1920),probe(source),out,progress=progress)
    assert not out.exists()
    assert not list(tmp_path.glob('.ai-shorts-render-*'))

def test_sample_aspect_ratio(tmp_path):
    source=tmp_path/'anamorphic.mkv'
    run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=1',
         '-vf','setsar=2/1','-c:v','libx264',str(source)])
    m=probe(source); assert m['width']==320
    out=tmp_path/'normalized.mp4'
    Renderer().render(source,Clip('sar',0,500000,mode='blur'),m,out)
    assert probe(out)['sar']=='1:1'

def test_audio_normalization_and_ducking(tmp_path):
    import numpy as np
    source=tmp_path/'separate.mkv'
    run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=2',
         '-f','lavfi','-i',"aevalsrc=if(between(t\\,0.5\\,1.5)\\,0.5*sin(2*PI*440*t)\\,0):s=48000:d=2",
         '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=2',
         '-filter_complex','[1:a]pan=stereo|c0=c0|c1=0*c0[voice];[2:a]pan=stereo|c0=0*c0|c1=c0[game]',
         '-map','0:v','-map','[voice]','-map','[game]','-c:v','libx264','-c:a','pcm_s16le',str(source)])
    metadata=probe(source); out=tmp_path/'ducked.mp4'
    clip=Clip('audio',0,2_000_000,audio={'1':1,'2':1},duck_game=True,voice_index=1)
    Renderer().render(source,clip,metadata,out)
    raw=subprocess.check_output([binary('ffmpeg'),'-v','error','-i',str(out),'-vn','-ac','2','-ar','48000','-f','f32le','-'])
    samples=np.frombuffer(raw,dtype='<f4').reshape(-1,2)
    rms=lambda x: float(np.sqrt(np.mean(x*x)))
    before=rms(samples[4800:19000,1]); during=rms(samples[38000:57000,1])
    assert during < before*.8
    assert rms(samples[38000:57000,0])>.1
    clip.normalize_audio=True
    normalized=tmp_path/'normalized.mp4'; Renderer().render(source,clip,metadata,normalized)
    assert probe(normalized)['audio']
    clip.audio={'1':1}
    with pytest.raises(ValueError): clip.validate(metadata['duration_us'])
