import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from platformdirs import user_data_dir,user_cache_dir,user_log_dir

def main():
    if '--analysis-worker' in sys.argv:
        from ai_shorts_studio.ai.pipeline import worker_main
        return worker_main(sys.argv[sys.argv.index('--analysis-worker')+1])
    if '--ai-smoke-test' in sys.argv:
        import json
        import faster_whisper,ctranslate2,onnxruntime,av,numpy
        from ai_shorts_studio.ai.models import REGISTRY
        from faster_whisper.vad import get_speech_timestamps
        assert get_speech_timestamps(numpy.zeros(16000,dtype=numpy.float32))==[]
        assert 'int8' in ctranslate2.get_supported_compute_types('cpu')
        target=Path(sys.argv[sys.argv.index('--ai-smoke-test')+1])
        target.write_text(json.dumps(dict(engine=faster_whisper.__version__,model=REGISTRY['id'],
            providers=onnxruntime.get_available_providers())),encoding='utf-8')
        return 0
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from ai_shorts_studio.ui.window import Window
    app = QApplication(sys.argv); app.setApplicationName('AI Shorts Studio'); app.setOrganizationName('AI Shorts Studio')
    data,cache,logs = [Path(f('AI Shorts Studio',appauthor=False)) for f in (user_data_dir,user_cache_dir,user_log_dir)]
    for path in (data,cache,logs): path.mkdir(parents=True,exist_ok=True)
    handler = RotatingFileHandler(logs/'studio.log',maxBytes=2_000_000,backupCount=2,encoding='utf-8')
    logging.basicConfig(handlers=[handler],level=logging.INFO)
    window = Window(data,cache); window.show()
    if '--smoke-test' in sys.argv: QTimer.singleShot(1200,app.quit)
    return app.exec()

if __name__=='__main__':
    sys.exit(main())
