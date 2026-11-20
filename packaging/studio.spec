# Build only on Windows. Qt hooks collect Multimedia plugins and FFmpeg backend DLLs.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files,collect_dynamic_libs,collect_submodules,copy_metadata
root = Path(SPECPATH).parent
binaries = [(str(root/'vendor'/'ffmpeg'/'bin'/'ffmpeg.exe'),'bin'),
            (str(root/'vendor'/'ffmpeg'/'bin'/'ffprobe.exe'),'bin')]
ai_datas=collect_data_files('faster_whisper')
ai_binaries=collect_dynamic_libs('ctranslate2')
ai_hidden=collect_submodules('huggingface_hub')+['faster_whisper','ctranslate2._ext','onnxruntime.capi.onnxruntime_pybind11_state','tokenizers']
for distribution in ('faster-whisper','ctranslate2','huggingface-hub','tokenizers','onnxruntime','av','numpy'):
    ai_datas+=copy_metadata(distribution)
a = Analysis([str(root/'scripts'/'launch.py')],pathex=[str(root/'src')],
    binaries=binaries+ai_binaries,datas=ai_datas+[(str(root/'src'/'ai_shorts_studio'/'assets'/'models.json'),'ai_shorts_studio/assets'),(str(root/'THIRD_PARTY_NOTICES.md'),'.'),(str(root/'LICENSE'),'.'),(str(root/'packaging'/'licenses'),'licenses'),
                           (str(root/'vendor'/'ffmpeg'/'LICENSE'),'licenses/ffmpeg')],
    hiddenimports=ai_hidden+['PySide6.QtMultimedia','PySide6.QtMultimediaWidgets'],hookspath=[],runtime_hooks=[],excludes=[])
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='AIShortsStudio',console=False)
coll = COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='AIShortsStudio')
