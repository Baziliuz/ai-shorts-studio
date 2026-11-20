"""Offline block analysis in an isolated process. No network operations here."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import wave
from uuid import uuid4
import numpy as np
from ai_shorts_studio.ai.models import REGISTRY,verify_model
from ai_shorts_studio.ai.ranking import candidates,content_candidates
from ai_shorts_studio.media.probe import binary,run,fingerprint

PIPELINE_VERSION=1
BLOCK_US=300_000_000
OVERLAP_US=5_000_000

def atomic_json(path,value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)

def cache_key(request):
    # Independent of crop, export size, target clip length, and ranking parameters.
    data=dict(source=request['fingerprint'],track=request.get('audio_index'),language=request['language'],
              model=REGISTRY['revision'],asr='faster-whisper-1.1.1-int8-beam3',pipeline=PIPELINE_VERSION)
    if request.get('audio_index') is not None and not request.get('speech_enabled',True):
        data['asr']='disabled'
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

def audio_signals(path,offset,core_start,core_end):
    result=[]
    with wave.open(str(path),'rb') as audio:
        second=0
        while samples:=audio.readframes(audio.getframerate()):
            timestamp=offset+second*1_000_000
            if core_start<=timestamp<core_end:
                data=np.frombuffer(samples,dtype='<i2').astype(np.float32)/32768
                result.append(dict(time_us=timestamp,rms=float(np.sqrt(np.mean(data*data))),delta=0.0))
            second+=1
    return result

def motion_signals(source,start_us,end_us,video_index):
    values=[]; previous=None
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen([binary('ffmpeg'),'-nostdin','-v','error','-ss',f'{start_us/1e6:.6f}',
            '-i',source,'-t',f'{(end_us-start_us)/1e6:.6f}','-map',f'0:{video_index}',
            '-vf','fps=1,scale=32:18,format=gray','-f','rawvideo','-pix_fmt','gray','pipe:1'],
            stdout=subprocess.PIPE,stderr=errors,creationflags=0x08000000 if os.name=='nt' else 0)
        try:
            while frame:=process.stdout.read(32*18):
                if len(frame)!=32*18: raise ValueError('Не удалось прочитать кадр анализа')
                current=np.frombuffer(frame,dtype=np.uint8).astype(np.float32)
                values.append(float(np.mean(np.abs(current-previous))/255) if previous is not None else 0.0)
                previous=current
            if process.wait()!=0:
                errors.seek(0); raise RuntimeError(errors.read().decode('utf-8',errors='replace')[-2000:])
        finally:
            if process.poll() is None: process.kill(); process.wait()
            process.stdout.close()
    return [dict(time_us=start_us+i*1_000_000,rms=0.0,delta=v) for i,v in enumerate(values)]

def normalize_signals(signals):
    if not signals: return []
    loud=np.array([s['rms'] for s in signals]); motion=np.array([s['delta'] for s in signals])
    normalized=[]
    for values in (loud,motion):
        median,high=np.quantile(values,[.5,.95])
        normalized.append(np.clip((values-median)/max(high-median,1e-6),0,1))
    return [dict(time_us=s['time_us'],loudness=float(normalized[0][i]),motion=float(normalized[1][i])) for i,s in enumerate(signals)]

def analyze(request,notify=lambda value,message:None):
    source=request['source']; duration=request['metadata']['duration_us']
    if duration<=0: raise ValueError('Некорректная длительность записи')
    notify(0,'Проверка исходного файла')
    if fingerprint(source)!=request['fingerprint']: raise ValueError('Исходник изменился. Импортируйте его заново.')
    audio_index=request.get('audio_index')
    if audio_index is not None and audio_index not in {a['index'] for a in request['metadata']['audio']}:
        raise ValueError('В исходнике нет выбранной дорожки')
    speech_enabled=audio_index is not None and request.get('speech_enabled',True)
    directory=Path(request['cache_dir'])/'analysis'/cache_key(request)
    directory.mkdir(parents=True,exist_ok=True)
    work_root=Path(request.get('work_dir',directory)); work_root.mkdir(parents=True,exist_ok=True)
    model=None
    segments=[]; raw_signals=[]
    blocks=math.ceil(duration/BLOCK_US)
    for block in range(blocks):
        core_start=block*BLOCK_US; core_end=min(duration,(block+1)*BLOCK_US)
        saved=directory/f'block-{block}.json'
        if saved.exists():
            try:
                cached=json.loads(saved.read_text(encoding='utf-8'))
                if cached['version']!=PIPELINE_VERSION: raise ValueError('cache version')
                for segment in cached['segments']:
                    if not (isinstance(segment['text'],str) and isinstance(segment['id'],str) and 0<=segment['start_us']<segment['end_us']<=duration):
                        raise ValueError('cache segment')
                    for word in segment.get('words',[]):
                        if not (isinstance(word,list) and len(word)==3 and isinstance(word[2],str)
                                and segment['start_us']<=word[0]<word[1]<=segment['end_us']):
                            raise ValueError('cache word timestamp')
                for item in cached['signals']:
                    if not (0<=item['time_us']<duration and all(math.isfinite(item[k]) and item[k]>=0 for k in ('rms','delta'))):
                        raise ValueError('cache signal')
                segments.extend(cached['segments']); raw_signals.extend(cached['signals'])
                notify(core_end/duration*.98,f'Восстановлен готовый блок {block+1}/{blocks}')
                continue
            except (OSError,ValueError,KeyError,TypeError):
                pass
        start=max(0,core_start-OVERLAP_US); end=min(duration,core_end+OVERLAP_US)
        current=[]; signals=[]
        with tempfile.TemporaryDirectory(prefix='work-',dir=work_root) as work:
            if audio_index is not None:
                if speech_enabled and model is None:
                    notify(core_start/duration,'Проверка и загрузка локальной модели в память · CPU int8')
                    verify_model(request['model_dir'])
                    from faster_whisper import WhisperModel
                    model=WhisperModel(request['model_dir'],device='cpu',compute_type='int8',
                        cpu_threads=max(1,min(6,(os.cpu_count() or 2)//2)),num_workers=1,local_files_only=True)
                audio=Path(work)/'speech.wav'
                notify(core_start/duration,f'Извлечение звука · блок {block+1}/{blocks}')
                run([binary('ffmpeg'),'-nostdin','-v','error','-ss',f'{start/1e6:.6f}','-i',source,
                    '-t',f'{(end-start)/1e6:.6f}','-map',f'0:{audio_index}','-vn','-ac','1','-ar','16000',
                    '-c:a','pcm_s16le','-n',str(audio)],timeout=None)
                signals=audio_signals(audio,start,core_start,core_end)
            if speech_enabled:
                transcription,_=model.transcribe(str(audio),language=request['language'] or None,
                    beam_size=3,vad_filter=True,condition_on_previous_text=False,word_timestamps=True)
                for number,segment in enumerate(transcription):
                    first=max(0,start+round(segment.start*1e6)); last=min(duration,start+round(segment.end*1e6))
                    if core_start<=(first+last)//2<core_end and first<last and segment.text.strip():
                        words=[]
                        for word in segment.words or []:
                            word_start=max(first,start+round(word.start*1e6))
                            word_end=min(last,start+round(word.end*1e6))
                            if word_start<word_end:
                                words.append([word_start,word_end,word.word])
                        current.append(dict(id=f'b{block}_s{number}',start_us=first,end_us=last,
                                            text=segment.text.strip(),words=words))
                    notify(min(.97,last/duration*.98),f'Распознавание речи · блок {block+1}/{blocks} · CPU')
            notify(core_start/duration,f'Анализ разницы кадров · блок {block+1}/{blocks}')
            visual=motion_signals(source,core_start,core_end,request['metadata']['video_index'])
            by_second={s['time_us']//1_000_000:s for s in signals}
            for item in visual:
                second=item['time_us']//1_000_000
                if second in by_second: by_second[second]['delta']=item['delta']
                else: signals.append(item)
        atomic_json(saved,dict(version=PIPELINE_VERSION,segments=current,signals=signals))
        segments.extend(current); raw_signals.extend(signals)
        notify(core_end/duration*.98,f'Готово блоков: {block+1}/{blocks}')
    # Overlap ownership is by segment midpoint. Remove repeated transcript fragments near block edges.
    merged=[]
    for segment in sorted(segments,key=lambda s:s['start_us']):
        if merged and segment['text'].casefold()==merged[-1]['text'].casefold() and segment['start_us']<merged[-1]['end_us']:
            continue
        merged.append(segment)
    signals=normalize_signals(raw_signals)
    selection=request.get('selection','fixed')
    if selection=='content':
        proposals=content_candidates(merged,signals,duration,request.get('profile','auto'),request['fingerprint'],
                                     automatic=request.get('automatic',False))
    elif selection=='fixed':
        proposals=candidates(merged,signals,duration,request['target_us'],request['min_us'],request['max_us'],request['limit'])
    else:
        raise ValueError('Неизвестный режим выбора длительности')
    notify(1,'Анализ завершён')
    engine='faster-whisper-small / CPU int8' if speech_enabled else 'Без распознавания речи'
    return dict(candidates=proposals,segment_count=len(merged),engine=engine,
                ranking='эвристики речи, громкости и разницы кадров',cache_key=cache_key(request))

def worker_main(request_path):
    request_path=Path(request_path); directory=request_path.parent
    try:
        request=json.loads(request_path.read_text(encoding='utf-8'))
        def notify(value,message): atomic_json(directory/'progress.json',dict(value=value,message=message))
        result=analyze(request,notify)
        atomic_json(directory/'result.json',dict(result=result)); return 0
    except Exception as error:
        text=str(error)
        if 'out of memory' in text.lower() or isinstance(error,MemoryError):
            text='Недостаточно памяти. Готовые блоки сохранены; закройте другие приложения и повторите.'
        atomic_json(directory/'result.json',dict(error=text or type(error).__name__)); return 1
