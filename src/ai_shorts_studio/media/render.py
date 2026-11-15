"""One composition graph shared by preview and export. No shell invocation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
from uuid import uuid4
from ai_shorts_studio.media.probe import binary, probe, run

class Cancelled(Exception):
    pass

def crop(rect):
    return f'crop=iw*{rect.w}:ih*{rect.h}:iw*{rect.x}:ih*{rect.y}'

def fill(w,h):
    return f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1'

def fit(w,h):
    return f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1'

def graph(clip, metadata):
    w,h = clip.width,clip.height
    start,end = clip.start_us/1e6,clip.end_us/1e6
    # FFmpeg autorotates before filters; normalize sample aspect ratio before crop.
    prefix = f"[0:{metadata['video_index']}]trim=start={start:.6f}:end={end:.6f},setpts=PTS-STARTPTS,scale=trunc(iw*sar/2)*2:ih,setsar=1"
    parts = []
    if clip.mode == 'blur':
        parts += [prefix+' ,split=2[bg][fg]',f'[bg]{fill(w,h)},boxblur=20:2[b]',
                  f'[fg]scale={w}:{h}:force_original_aspect_ratio=decrease[f]',
                  '[b][f]overlay=(W-w)/2:(H-h)/2[v0]']
    elif clip.mode == 'gaming':
        top = int(h*clip.camera_share)//2*2
        parts += [prefix+',split=2[cam][game]', f'[cam]{crop(clip.camera)},{fill(w,top)}[c]',
                  f'[game]{crop(clip.game)},{fit(w,h-top)}[g]', '[c][g]vstack=inputs=2[v0]']
    else:
        parts += [prefix+(','+crop(clip.crop) if clip.mode=='manual' else '')+','+fill(w,h)+'[v0]']
    parts += [f'[v0]fps={clip.fps:.8f},format=yuv420p[v]']
    valid = {str(a['index']) for a in metadata['audio']}
    labels = []
    for index, volume in clip.audio.items():
        if str(index) not in valid:
            raise ValueError('Аудиодорожка отсутствует в исходнике')
        label = f'a{len(labels)}'
        parts += [f'[0:{int(index)}]atrim=start={start:.6f}:end={end:.6f},asetpts=PTS-STARTPTS,volume={float(volume)},aresample=48000[{label}]']
        labels.append(f'[{label}]')
    if labels:
        parts += [''.join(labels)+f'amix=inputs={len(labels)}:normalize=0:duration=longest,alimiter=limit=0.95:latency=1[a]']
    return ';'.join(parts), bool(labels)

def encoder_available(encoder):
    try:
        run([binary('ffmpeg'),'-v','error','-f','lavfi','-i','color=s=64x64:r=1',
             '-frames:v','1','-c:v',encoder,'-f','null','-'],timeout=20)
        return True
    except (RuntimeError, subprocess.TimeoutExpired):
        return False

def capabilities():
    filters = run([binary('ffmpeg'),'-hide_banner','-filters'])
    required = ['trim','atrim','scale','crop','overlay','boxblur','vstack','amix','alimiter','fps']
    missing = [x for x in required if f' {x} ' not in filters]
    if missing:
        raise RuntimeError('В FFmpeg отсутствуют фильтры: '+', '.join(missing))
    return {'nvenc':encoder_available('h264_nvenc'), 'software':encoder_available('libx264')}

def check_space(folder, duration, width, height):
    # Conservative workspace reserve; final encoder size depends on content.
    needed = max(128*1024**2, int(duration * width * height * .6))
    if shutil.disk_usage(folder).free < needed:
        raise RuntimeError(f'Недостаточно места: требуется резерв {needed/1024**3:.1f} ГБ')

class Renderer:
    def __init__(self):
        self.cancelled = threading.Event()
        self.process = None

    def cancel(self):
        self.cancelled.set()
        if self.process and self.process.poll() is None:
            try:
                self.process.terminate()
            except OSError:
                pass

    def render(self, source, clip, metadata, destination, encoder='libx264', progress=lambda x:None, _proxy=False):
        if not _proxy:
            clip.validate(metadata['duration_us'])
        if clip.fps > metadata['fps'] + .01:
            raise ValueError('Частота экспорта выше исходной. Выберите частоту исходника.')
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(f'Файл уже существует: {destination}')
        duration = (clip.end_us-clip.start_us)/1e6
        check_space(destination.parent,duration,clip.width,clip.height)
        temporary = destination.with_name('.'+destination.stem+'.'+uuid4().hex+'.partial.mp4')
        filters,audio = graph(clip,metadata)
        args = [binary('ffmpeg'),'-hide_banner','-nostdin','-v','error','-i',str(source),
                '-filter_complex',filters,'-map','[v]']
        if audio:
            args += ['-map','[a]','-c:a','aac','-b:a','192k']
        args += ['-c:v',encoder,'-pix_fmt','yuv420p','-movflags','+faststart','-t',f'{duration:.6f}',
                 '-progress','pipe:1','-nostats','-n',str(temporary)]
        try:
            with tempfile.TemporaryFile() as errors:
                if self.cancelled.is_set():
                    raise Cancelled()
                self.process = subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors,text=True,
                    encoding='utf-8', creationflags=0x08000000 if os.name=='nt' else 0)
                for line in self.process.stdout:
                    if self.cancelled.is_set():
                        self.process.terminate()
                        break
                    if line.startswith('out_time_us='):
                        try:
                            progress(min(.99,max(0,int(line.split('=')[1])/1e6/duration)))
                        except ValueError:
                            pass
                try:
                    code = self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    code = self.process.wait()
                if self.cancelled.is_set():
                    raise Cancelled()
                if code:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode('utf-8',errors='replace')[-4000:])
            result = probe(temporary)
            if (result['width'],result['height']) != (clip.width,clip.height) or abs(result['duration_us']/1e6-duration) > max(.15,2/clip.fps):
                raise RuntimeError('Проверка результата экспорта не пройдена')
            if bool(result['audio']) != audio:
                raise RuntimeError('Проверка звуковой дорожки не пройдена')
            # Atomic no-clobber publication on the same filesystem.
            if os.name == 'nt':
                os.rename(temporary,destination)  # Windows rename rejects an existing destination.
            else:
                os.link(temporary,destination)
            progress(1)
            return str(destination)
        finally:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.process = None
            temporary.unlink(missing_ok=True)
