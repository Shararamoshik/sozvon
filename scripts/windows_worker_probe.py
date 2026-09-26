"""Изолировать Windows-зависание по этапам, без сетевых запросов."""
import faulthandler
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
mode = sys.argv[1]
output = ROOT / 'artifacts' / f'windows-{mode}.log'
with output.open('w', encoding='utf-8') as f:
    faulthandler.enable(f)
    faulthandler.dump_traceback_later(15, repeat=True, file=f)
    def emit(event):
        f.write(json.dumps(event, ensure_ascii=False) + '\n')
        f.flush()
    payload = {'path':str(ROOT/'tests/fixtures/hello_ru.wav'),
               'model_path':str(ROOT/'artifacts/models/tiny'),
               'device':'cpu', 'language':'ru', 'compute_type':'int8'}
    start = time.monotonic()
    if mode == 'direct':
        from sozvon.audio.operations import run
        result = run('transcribe', payload, threading.Event(), emit)
    else:
        from sozvon.runtime.host import WorkerHost
        result = WorkerHost().run('transcribe',payload, timeout=45, on_event=emit)
    emit(result)
    emit({'elapsed': time.monotonic()-start})
    faulthandler.cancel_dump_traceback_later()
print('REPORT='+str(output))
