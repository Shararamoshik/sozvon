"""Настоящий EXE -> HTTP -> сессия -> сохранение -> перезапуск. Без внешнего API."""
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

import httpx

ROOT=Path(__file__).resolve().parents[1]
root=ROOT/'artifacts'/'frozen-web-data'
root.mkdir(parents=True,exist_ok=True)
exe=ROOT/'dist'/'Sozvon'/'Sozvon.exe'
runtime=root/'runtime.json'
process=subprocess.Popen([str(exe),'--no-browser','--port','8118','--data-dir',str(root)],
                         stdout=subprocess.PIPE,stderr=subprocess.PIPE)
try:
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if process.poll() is not None:raise RuntimeError(process.stderr.read().decode(errors='replace'))
        if runtime.exists():
            r=json.loads(runtime.read_text(encoding='utf-8'))
            if r['pid']==process.pid:break
        time.sleep(.05)
    else:raise RuntimeError('No runtime')
    url=urlsplit(r['url']);base=f'{url.scheme}://{url.netloc}'
    with httpx.Client(base_url=base,trust_env=False) as client:
        assert client.get('/api/health').status_code==200
        assert client.get('/api/meetings').status_code==401
        session=client.post('/api/session',json={'key':parse_qs(url.fragment)['key'][0]},headers={'Origin':base})
        assert session.status_code==200,session.text
        h={'Origin':base,'X-CSRF-Token':session.json()['csrf']}
        created=client.post('/api/import/text',json={'title':'Проверка EXE','text':'Синтетическая запись из упакованного приложения.'},headers=h)
        assert created.status_code==200,created.text
        mid=created.json()['id']
        detail=client.get(f'/api/meetings/{mid}').json()
        assert detail['segments'][0]['start_ms'] is None
        assert client.get('/').status_code==200
        assert client.get('/static/app.css').status_code==200
        assert client.get('/static/fonts/GolosText-Regular.ttf').status_code==200
        assert client.get(f'/api/meetings/{mid}/export?format=md').status_code==200
    (ROOT/'artifacts/frozen-web-result.json').write_text(json.dumps({'ok':True,'meeting_id':mid,'exe':str(exe)},ensure_ascii=False),encoding='utf-8')
    print('FROZEN_WEB_OK')
finally:
    process.terminate()
    process.wait(15)
    process.stdout.close();process.stderr.close()
