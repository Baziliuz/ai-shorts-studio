import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from ai_shorts_studio.media.render import Cancelled

class AnalysisRunner:
    def __init__(self):
        self.cancelled=threading.Event(); self.process=None; self.on_status=lambda _:None
    def cancel(self): self.cancelled.set()

    def stop_tree(self):
        if self.process is None or self.process.poll() is not None: return
        if os.name=='nt':
            subprocess.run(['taskkill','/PID',str(self.process.pid),'/T','/F'],capture_output=True,creationflags=0x08000000)
        else:
            try: os.killpg(self.process.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        self.process.wait()

    def analyze(self,request,progress=lambda _:None):
        root=Path(request['cache_dir'])/'analysis-jobs'; root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='job-',dir=root,ignore_cleanup_errors=True) as temporary:
            directory=Path(temporary); path=directory/'request.json'
            request=dict(request,work_dir=str(directory/'work'))
            path.write_text(json.dumps(request,ensure_ascii=False),encoding='utf-8')
            command=([sys.executable] if getattr(sys,'frozen',False) else [sys.executable,'-m','ai_shorts_studio'])
            command+=['--analysis-worker',str(path)]
            environment=dict(os.environ,HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',DO_NOT_TRACK='1')
            try:
                if self.cancelled.is_set(): raise Cancelled()
                self.process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    env=environment,creationflags=0x08000200 if os.name=='nt' else 0,start_new_session=os.name!='nt')
                previous=None; completed=0.0
                while self.process.poll() is None:
                    if self.cancelled.wait(.2): self.stop_tree(); raise Cancelled()
                    try:
                        state=json.loads((directory/'progress.json').read_text(encoding='utf-8'))
                        if state!=previous:
                            completed=max(completed,state['value'])
                            progress(completed); self.on_status(state['message']); previous=state
                    except (OSError,ValueError,KeyError): pass
                if self.cancelled.is_set(): raise Cancelled()
                result_path=directory/'result.json'
                if not result_path.exists():
                    raise RuntimeError('Процесс анализа завершился без результата. Возможны нехватка памяти или отсутствие системной библиотеки. Готовые блоки сохранены.')
                result=json.loads(result_path.read_text(encoding='utf-8'))
                if 'error' in result: raise RuntimeError(result['error'])
                if self.process.returncode: raise RuntimeError('Ошибка процесса анализа')
                progress(1); return result['result']
            finally:
                self.stop_tree(); self.process=None
                for attempt in range(7):
                    try:
                        shutil.rmtree(directory); break
                    except FileNotFoundError: break
                    except PermissionError:
                        if attempt==6: break
                        time.sleep(.05*2**attempt)
