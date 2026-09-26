"""Регрессии проверки ревизий, длительности и состояния приложения."""
import threading
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sozvon.services.application import Application, BusyError
from sozvon.storage.repository import Repository


def test_recording_duration_comes_from_total_not_last_track(tmp_path):
    class Worker:
        def run(self, operation, payload, **kwargs):
            tracks=[]
            for name, duration in [('mic',2000),('system',1000)]:
                target=Path(payload['output_dir'])/(name+'.wav')
                with wave.open(str(target),'wb') as f:
                    f.setparams((1,2,8000,0,'NONE',''));f.writeframes(b'\0'*(duration*16))
                tracks.append({'source':name,'path':str(target),'duration_ms':duration})
            return {'tracks':tracks,'duration_ms':2000,'warnings':[]}
    app=Application(tmp_path,worker_factory=Worker)
    try:
        result=app.start_recording(False,10)
        assert app.wait(3)
        assert app.repo.detail(result['id'])['duration_ms']==2000
    finally:app.close()


def test_report_is_bound_to_input_revision_even_if_new_text_exists(tmp_path):
    repo=Repository(tmp_path)
    mid=repo.create_text('test','Старый текст')
    first=repo.detail(mid)
    repo.save_segments(mid,[{'text':'Новый текст','start_ms':None,'end_ms':None}])
    repo.save_report(mid,{'document':{'summary':[]},'model':'test'},
                     transcript_revision=first['transcript_revision'])
    current=repo.detail(mid)
    assert current['reports'][0]['stale']
    assert current['reports'][0]['transcript_revision']==1


def test_busy_report_is_rejected_before_reading_old_text(tmp_path,monkeypatch):
    started=threading.Event()
    release=threading.Event()
    class Worker:
        def run(self,*args,**kwargs):
            started.set();release.wait(3)
            return {'document':{'summary':[]},'model':'test'}
    app=Application(tmp_path,worker_factory=Worker)
    app.settings.save({'llm':{'model':'test'}})
    mid=app.repo.create_text('test','old')
    try:
        app.report(mid)
        assert started.wait(3)
        def forbidden(*args):
            pytest.fail('Read transcript while another operation was still active')
        monkeypatch.setattr(app.repo,'detail',forbidden)
        with pytest.raises(BusyError):app.report(mid)
    finally:
        release.set();app.close()


def test_settings_cannot_change_during_job(tmp_path):
    started=threading.Event();release=threading.Event()
    class Worker:
        def run(self,*args,**kwargs):
            started.set();release.wait(3)
            return {'document':{'summary':[]},'model':'test'}
    app=Application(tmp_path,worker_factory=Worker)
    app.settings.save({'llm':{'model':'old'}})
    mid=app.repo.create_text('test','text')
    try:
        app.report(mid);assert started.wait(3)
        with pytest.raises(BusyError):app.save_settings({'llm':{'model':'new'}})
        assert app.settings.snapshot()['llm']['model']=='old'
    finally:release.set();app.close()


def test_stale_report_markdown_is_explicitly_refused(tmp_path):
    from sozvon.web.server import create_app
    app=create_app(tmp_path)
    repo=app.state.service.repo
    mid=repo.create_text('test','Срок 1 мая')
    repo.save_report(mid,{'document':{'summary':[{'text':'Срок 1 мая','evidence':[]}]},'model':'test'})
    repo.save_segments(mid,[{'text':'Срок 2 мая'}])
    with TestClient(app,base_url='http://127.0.0.1') as client:
        client.post('/api/session',json={'key':app.state.launch_key},headers={'Origin':'http://127.0.0.1'})
        response=client.get(f'/api/meetings/{mid}/export?format=md')
        assert response.status_code==409
        assert 'устарел' in response.json()['detail'].lower()
        exported = client.get(f'/api/meetings/{mid}/export?format=json').json()
        assert [revision['revision'] for revision in exported['transcript_history']] == [1, 2]
        assert exported['transcript_history'][0]['segments'][0]['text'] == 'Срок 1 мая'
