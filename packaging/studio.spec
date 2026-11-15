# Build only on Windows. Qt hooks collect Multimedia plugins and FFmpeg backend DLLs.
from pathlib import Path
root = Path(SPECPATH).parent
binaries = [(str(root/'vendor'/'ffmpeg'/'bin'/'ffmpeg.exe'),'bin'),
            (str(root/'vendor'/'ffmpeg'/'bin'/'ffprobe.exe'),'bin')]
a = Analysis([str(root/'scripts'/'launch.py')],pathex=[str(root/'src')],
    binaries=binaries,datas=[(str(root/'THIRD_PARTY_NOTICES.md'),'.'),(str(root/'LICENSE'),'.'),(str(root/'packaging'/'licenses'),'licenses'),
                           (str(root/'vendor'/'ffmpeg'/'LICENSE'),'licenses/ffmpeg')],
    hiddenimports=['PySide6.QtMultimedia','PySide6.QtMultimediaWidgets'],hookspath=[],runtime_hooks=[],excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='AIShortsStudio',console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='AIShortsStudio')
