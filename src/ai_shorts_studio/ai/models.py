"""Explicit, pinned model setup. Processing never downloads models implicitly."""
import hashlib
import json
from pathlib import Path
import shutil
import threading
import urllib.request
from ai_shorts_studio.media.render import Cancelled

REGISTRY = json.loads((Path(__file__).resolve().parents[1]/'assets'/'models.json').read_text(encoding='utf-8'))

def digest(path,cancel=None):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024):
            if cancel and cancel.is_set(): raise Cancelled()
            result.update(block)
    return result.hexdigest()

def model_ready(folder):
    folder=Path(folder)
    return all((folder/f['name']).is_file() and (folder/f['name']).stat().st_size==f['size'] for f in REGISTRY['files'])

def verify_model(folder,cancel=None):
    folder=Path(folder)
    for entry in REGISTRY['files']:
        if not (folder/entry['name']).is_file() or digest(folder/entry['name'],cancel)!=entry['sha256']:
            raise ValueError('Модель отсутствует или повреждена. Повторите установку модели.')

class ModelDownloader:
    def __init__(self): self.cancelled=threading.Event()
    def cancel(self): self.cancelled.set()

    def download(self,folder,progress=lambda _:None):
        folder=Path(folder); folder.mkdir(parents=True,exist_ok=True)
        total=sum(f['size'] for f in REGISTRY['files'])
        valid={f['name'] for f in REGISTRY['files'] if (folder/f['name']).is_file() and
               (folder/f['name']).stat().st_size==f['size'] and digest(folder/f['name'],self.cancelled)==f['sha256']}
        remaining=sum(f['size'] for f in REGISTRY['files'] if f['name'] not in valid)
        if shutil.disk_usage(folder).free < remaining+256*1024**2:
            raise ValueError('Недостаточно места для модели и временных файлов')
        completed=0
        for entry in REGISTRY['files']:
            final=folder/entry['name']; part=folder/(entry['name']+'.part')
            if entry['name'] in valid:
                completed+=entry['size']; progress(completed/total); continue
            if self.cancelled.is_set(): raise Cancelled()
            offset=part.stat().st_size if part.exists() else 0
            if offset>=entry['size']: part.unlink(); offset=0
            url=f"https://huggingface.co/{REGISTRY['repo']}/resolve/{REGISTRY['revision']}/{entry['name']}"
            request=urllib.request.Request(url,headers={'Range':f'bytes={offset}-'} if offset else {})
            with urllib.request.urlopen(request,timeout=30) as response:
                if response.status!=206: offset=0
                elif response.headers.get('Content-Range','').split('/')[0]!=f"bytes {offset}-{entry['size']-1}":
                    raise ValueError('Сервер вернул неверный диапазон загрузки. Повторите установку.')
                with part.open('ab' if offset else 'wb') as output:
                    received=offset
                    while block:=response.read(1024*1024):
                        if self.cancelled.is_set(): raise Cancelled()
                        received+=len(block)
                        if received>entry['size']: raise ValueError('Размер модели отличается от реестра')
                        output.write(block); progress((completed+received)/total)
            if part.stat().st_size!=entry['size']:
                raise ValueError('Загрузка не завершена. Можно повторить и продолжить загрузку.')
            if digest(part,self.cancelled)!=entry['sha256']:
                part.unlink()
                raise ValueError('Контрольная сумма модели не совпадает. Повторите загрузку.')
            part.replace(final); completed+=entry['size']
        verify_model(folder,self.cancelled)
        progress(1)
        return str(folder)
