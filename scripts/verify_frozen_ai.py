"""Explicit developer/CI integration test. Downloads public fixtures only with --download-model."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request
from ai_shorts_studio.ai.models import ModelDownloader
from ai_shorts_studio.media.probe import binary,run,probe,fingerprint

FIXTURE='https://raw.githubusercontent.com/openai/whisper/6e3be77e1a105e59086e3e21ff5f609fd6fa89a5/tests/jfk.flac'
FIXTURE_SHA='63a4b1e4c1dc655ac70961ffbf518acd249df237e5a0152faae9a4a836949715'

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--exe',required=True); parser.add_argument('--download-model',action='store_true')
    parser.add_argument('--model-dir'); args=parser.parse_args()
    executable=str(Path(args.exe).resolve())
    with tempfile.TemporaryDirectory(prefix='проверка AI ') as temporary:
        root=Path(temporary)
        model=Path(args.model_dir) if args.model_dir else root/'модель small'
        if args.download_model:
            last=[-1]
            def progress(value):
                percent=int(value*10)*10
                if percent!=last[0]: print(f'Model download: {percent}%',flush=True); last[0]=percent
            ModelDownloader().download(model,progress)
        elif not args.model_dir:
            parser.error('Specify --model-dir or explicitly enable --download-model')
        audio=root/'public-jfk.flac'
        with urllib.request.urlopen(FIXTURE,timeout=30) as response: content=response.read()
        if hashlib.sha256(content).hexdigest()!=FIXTURE_SHA: raise RuntimeError('Public test fixture checksum mismatch')
        audio.write_bytes(content)
        source=root/'исходник с пробелами.mkv'
        run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','testsrc2=s=160x90:r=15:d=12','-i',str(audio),
             '-map','0:v','-map','1:a','-c:v','libx264','-c:a','pcm_s16le','-shortest',str(source)])
        metadata=probe(source)
        job=root/'job'; job.mkdir()
        request=dict(source=str(source),fingerprint=fingerprint(source),metadata=metadata,cache_dir=str(root/'cache'),
            model_dir=str(model),work_dir=str(job/'work'),audio_index=metadata['audio'][0]['index'],language='en',
            target_us=10_000_000,min_us=3_000_000,max_us=12_000_000,limit=3)
        request_file=job/'request.json'; request_file.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
        environment=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1')
        process=subprocess.run([executable,'--analysis-worker',str(request_file)],env=environment,timeout=180)
        result_path=job/'result.json'
        if not result_path.exists(): raise RuntimeError(f'Frozen AI process returned no result, exit {process.returncode}')
        response=json.loads(result_path.read_text(encoding='utf-8'))
        if process.returncode or 'error' in response: raise RuntimeError(str(response))
        blocks=list((root/'cache'/'analysis').rglob('block-*.json'))
        text=' '.join(s['text'] for file in blocks for s in json.loads(file.read_text(encoding='utf-8'))['segments'])
        if 'country' not in text.lower(): raise RuntimeError('Expected word not found in public speech fixture')
        proposals=response['result']['candidates']
        if not proposals: raise RuntimeError('Expected evidence-based candidate not produced')
        if any(not 0<=c['start_us']<c['end_us']<=metadata['duration_us'] for c in proposals):
            raise RuntimeError('Invalid proposal boundaries')
        print(f"Frozen offline ASR + ranking passed: {response['result']['segment_count']} segments, {len(proposals)} candidates",flush=True)

if __name__=='__main__': main()
