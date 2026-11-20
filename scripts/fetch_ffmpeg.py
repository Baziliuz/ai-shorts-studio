"""Developer/CI bootstrap only. Never downloads during end-user processing."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

URL = 'https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip'
SHA256 = '04861d3339c5ebe38b56c19a15cf2c0cc97f5de4fa8910e4d47e5e6404e4a2d4'

def main():
    root = Path(__file__).resolve().parents[1]/'vendor'/'ffmpeg'
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary)/'ffmpeg.zip'
        digest = hashlib.sha256()
        with urllib.request.urlopen(URL,timeout=60) as response,archive.open('wb') as output:
            while block:=response.read(1024*1024):
                digest.update(block); output.write(block)
        if digest.hexdigest()!=SHA256:
            raise RuntimeError('FFmpeg archive checksum mismatch')
        with zipfile.ZipFile(archive) as zipped:
            for suffix,destination in [('bin/ffmpeg.exe',root/'bin'/'ffmpeg.exe'),
                                       ('bin/ffprobe.exe',root/'bin'/'ffprobe.exe'),
                                       ('LICENSE',root/'LICENSE')]:
                matches=[n for n in zipped.namelist() if n.endswith('/'+suffix)]
                if len(matches)!=1: raise RuntimeError(f'Missing or ambiguous binary: {suffix}')
                destination.parent.mkdir(parents=True,exist_ok=True)
                with zipped.open(matches[0]) as src,destination.open('wb') as out:
                    shutil.copyfileobj(src,out)
    print('Verified FFmpeg 7.1.1; extracted ffmpeg, ffprobe, LICENSE')

if __name__=='__main__': main()
