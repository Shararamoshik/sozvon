from fastapi.testclient import TestClient


def session(root, factory):
    from sozvon.web.server import create_app
    app = create_app(root, worker_factory=factory)
    client = TestClient(app, base_url="http://127.0.0.1")
    origin = {"Origin":"http://127.0.0.1"}
    csrf=client.post('/api/session',json={"key":app.state.launch_key},headers=origin).json()['csrf']
    return app, client, {**origin,"X-CSRF-Token":csrf}


def test_failed_audio_validation_is_readable_and_does_not_add_meeting(tmp_path):
    from sozvon.runtime.host import WorkerError
    class Broken:
        def run(self,*args,**kwargs):
            raise WorkerError('operation_error','Не удалось декодировать аудио')
    _,client,headers=session(tmp_path,Broken)
    with client:
        response=client.post('/api/import/audio',files={'file':('bad.wav',b'not-wave','audio/wav')},headers=headers)
        assert response.status_code==422
        assert 'декодировать' in response.json()['detail']
        assert client.get('/api/meetings').json()['items']==[]


def test_recording_warning_stays_visible_after_finish(tmp_path):
    from sozvon.services.application import Application
    class Partial:
        def run(self,operation,payload,**kwargs):
            import wave
            from pathlib import Path
            target=Path(payload['output_dir'])/'mic.wav'
            with wave.open(str(target),'wb') as f:
                f.setparams((1,2,16000,0,'NONE',''));f.writeframes(b'\0'*3200)
            return {'tracks':[{'path':str(target),'source':'mic','duration_ms':100}],
                    'warnings':['Системный звук оборвался; сохранена доступная часть']}
    app=Application(tmp_path,worker_factory=Partial)
    try:
        result=app.start_recording(True,5)
        assert app.wait(3)
        detail=app.repo.detail(result['id'])
        assert 'Системный звук' in detail['error']
        assert detail['status']=='partial'
        assert len(detail['sources'])==1
    finally:app.close()
