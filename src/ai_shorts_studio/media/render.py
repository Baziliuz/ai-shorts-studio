"""One composition graph shared by preview and export. No shell invocation."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
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
    duration = (clip.end_us - clip.start_us) / 1e6
    # FFmpeg autorotates before filters; normalize sample aspect ratio before crop.
    prefix = (
        f"[0:{metadata['video_index']}]trim=end={duration:.6f},"
        "scale=trunc(iw*sar/2)*2:ih,setsar=1"
    )
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
    parts += [f'[v0]fps=fps={clip.fps:.8f}:start_time=0,format=yuv420p[v]']
    valid = {str(a['index']) for a in metadata['audio']}
    labels = []
    by_index = {}
    for index, volume in clip.audio.items():
        if str(index) not in valid:
            raise ValueError('Аудиодорожка отсутствует в исходнике')
        label = f'a{len(labels)}'
        # Align every stream to the same origin before mixing. Pad delayed streams with silence.
        parts += [
            f'[0:{int(index)}]atrim=end={duration:.6f},volume={float(volume)},'
            f'aresample=48000:async=1:first_pts=0[{label}]'
        ]
        labels.append(f'[{label}]')
        by_index[str(index)]=f'[{label}]'
    if labels:
        if clip.duck_game:
            voice=by_index[str(clip.voice_index)]
            games=[v for k,v in by_index.items() if k!=str(clip.voice_index)]
            parts += [voice+'asplit=2[voice][side]',
                      ''.join(games)+f'amix=inputs={len(games)}:normalize=0:duration=longest[games]',
                      '[games][side]sidechaincompress=threshold=0.03:ratio=8:attack=15:release=300[ducked]',
                      '[voice][ducked]amix=inputs=2:normalize=0:duration=longest[mixed]']
        else:
            parts += [''.join(labels)+f'amix=inputs={len(labels)}:normalize=0:duration=longest[mixed]']
        effects='loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000,' if clip.normalize_audio else ''
        parts += [f'[mixed]{effects}alimiter=limit=0.95:latency=1[a]']
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
    required = ['trim','atrim','scale','crop','overlay','boxblur','vstack','amix','alimiter','fps','loudnorm','sidechaincompress']
    missing = [x for x in required if f' {x} ' not in filters]
    if missing:
        raise RuntimeError('В FFmpeg отсутствуют фильтры: '+', '.join(missing))
    return {'nvenc':encoder_available('h264_nvenc'), 'software':encoder_available('libx264')}

def choose_encoder(preference='auto'):
    if preference not in ('auto', 'h264_nvenc', 'libx264'):
        raise ValueError('Неизвестный кодировщик H.264')
    available = capabilities()
    if preference != 'libx264' and available['nvenc']:
        return 'h264_nvenc'
    if available['software']:
        return 'libx264'
    raise RuntimeError('Нет доступного кодировщика H.264 для выбранного режима')

def check_space(folder, duration, width, height):
    # Conservative workspace reserve; final encoder size depends on content.
    needed = max(128*1024**2, int(duration * width * height * .6))
    if shutil.disk_usage(folder).free < needed:
        raise RuntimeError(f'Недостаточно места: требуется резерв {needed/1024**3:.1f} ГБ')

def validate_export(path, clip, duration, audio):
    result = probe(path, strict_fps=True)
    if (result['width'], result['height']) != (clip.width, clip.height):
        raise RuntimeError('Разрешение результата отличается от выбранного профиля')
    if abs(result['duration_us'] / 1e6 - duration) > max(.15, 2 / clip.fps):
        raise RuntimeError('Длительность результата отличается от границ клипа')
    if abs(result['fps'] - clip.fps) > .01:
        raise RuntimeError('Частота кадров результата отличается от выбранной')
    if result['video_codec'] != 'h264':
        raise RuntimeError('Видеодорожка результата имеет кодек, отличный от H.264')
    if [track['codec'] for track in result['audio']] != (['aac'] if audio else []):
        raise RuntimeError('Проверка звуковой дорожки не пройдена')

class Renderer:
    def __init__(self):
        self.cancelled = threading.Event()
        self.process = None
        self.on_status = lambda message: None

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def render(self, source, clip, metadata, destination, encoder='libx264', progress=lambda x:None, _proxy=False):
        if self.cancelled.is_set():
            raise Cancelled()
        if not _proxy:
            clip.validate(metadata['duration_us'])
        if clip.fps > metadata['fps'] + .01:
            raise ValueError('Частота экспорта выше исходной. Выберите частоту исходника.')
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(f'Файл уже существует: {destination}')
        duration = (clip.end_us-clip.start_us)/1e6
        check_space(destination.parent,duration,clip.width,clip.height)
        if encoder == 'auto':
            encoder = choose_encoder()
        filters,audio = graph(clip,metadata)
        args = [binary('ffmpeg'), '-hide_banner', '-nostdin', '-v', 'error']
        if clip.start_us:
            # Accurate input seeking decodes from the preceding keyframe and shifts every track together.
            args += ['-ss', f'{clip.start_us / 1e6:.6f}']
        args += ['-i', str(source), '-filter_complex', filters, '-map', '[v]']
        if audio:
            args += ['-map','[a]','-c:a','aac','-b:a','192k']
        args += ['-c:v',encoder,'-pix_fmt','yuv420p','-movflags','+faststart','-t',f'{duration:.6f}',
                 '-progress','pipe:1','-nostats','-n']
        attempt_dir = Path(tempfile.mkdtemp(prefix='.ai-shorts-render-', dir=destination.parent))
        temporary = attempt_dir / 'result.mp4'
        try:
            self._encode(args + [str(temporary)], attempt_dir, duration, progress)
            validate_export(temporary, clip, duration, audio)
            if self.cancelled.is_set():
                raise Cancelled()
            # Atomic no-clobber publication on the same filesystem.
            if os.name == 'nt':
                os.rename(temporary,destination)  # Windows rename rejects an existing destination.
            else:
                os.link(temporary,destination)
            progress(1)
            return str(destination)
        finally:
            # Windows/antivirus can briefly retain the output after process termination.
            for attempt in range(7):
                try:
                    shutil.rmtree(attempt_dir)
                    break
                except PermissionError:
                    if os.name != 'nt' or attempt == 6:
                        raise
                    time.sleep(.05 * 2**attempt)

    def _encode(self, args, attempt_dir, duration, progress):
        with tempfile.TemporaryFile(dir=attempt_dir) as errors:
            if self.cancelled.is_set():
                raise Cancelled()
            process = subprocess.Popen(
                args, stdout=subprocess.PIPE, stderr=errors, text=True, encoding='utf-8',
                creationflags=0x08000000 if os.name == 'nt' else 0,
            )
            self.process = process
            try:
                if self.cancelled.is_set():
                    raise Cancelled()
                for line in process.stdout:
                    if self.cancelled.is_set():
                        break
                    if line.startswith('out_time_us='):
                        try:
                            elapsed = int(line.split('=', 1)[1]) / 1e6
                        except ValueError:
                            continue
                        progress(min(.99, max(0, elapsed / duration)))
                if self.cancelled.is_set():
                    raise Cancelled()
                code = process.wait(timeout=5)
                if self.cancelled.is_set():
                    raise Cancelled()
                if code:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode('utf-8', errors='replace')[-4000:])
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()
                self.process = None
