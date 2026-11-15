import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from platformdirs import user_data_dir,user_cache_dir,user_log_dir
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from ai_shorts_studio.ui.window import Window

def main():
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
