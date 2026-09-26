"""Начало остановки Uvicorn должно отменить export до ожидания HTTP задач."""
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from sozvon.runtime.host import WorkerError
from sozvon.web.server import create_app


@pytest.mark.parametrize('real_worker', [False, True])
def test_server_shutdown_signals_export_before_waiting_for_request(tmp_path, real_worker):
    rendering, cancelled, release = threading.Event(), threading.Event(), threading.Event()

    class Worker:
        def run(self, operation, payload, **kwargs):
            assert operation == 'render_export'
            rendering.set()
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not release.is_set():
                if kwargs['stop_event'].is_set():
                    cancelled.set()
                    raise WorkerError('cancelled', 'Экспорт отменён')
                release.wait(.01)
            raise WorkerError('cancelled', 'Синтетический worker остановлен тестом')

    if real_worker:
        from sozvon.runtime.host import WorkerHost

        class RealWorker(WorkerHost):
            def run(self, *args, **kwargs):
                def on_event(event):
                    if event['type'] == 'progress':
                        rendering.set()
                try:
                    return super().run(*args, **kwargs, on_event=on_event)
                except WorkerError as exc:
                    if exc.code == 'cancelled':
                        cancelled.set()
                    raise

        factory = lambda: RealWorker(stop_grace=.05)
    else:
        factory = Worker
    app = create_app(tmp_path, worker_factory=factory)
    from sozvon.web.lifecycle import ApplicationServer
    server = ApplicationServer(uvicorn.Config(app, log_level='warning', access_log=False))
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    responses = []
    request_thread = None
    try:
        deadline = time.monotonic() + 5
        while not server.started and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        base = f'http://127.0.0.1:{port}'
        text = '\n'.join(['Длинная строка. ' * 50] * 500) if real_worker else 'Текст'
        mid = app.state.service.repo.create_text('Синтетический выход', text)
        with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
            assert client.post('/api/session', headers={'Origin': base},
                               json={'key': app.state.launch_key}).status_code == 200
            request_thread = threading.Thread(target=lambda: responses.append(
                client.get(f'/api/meetings/{mid}/export?format=pdf')))
            request_thread.start()
            assert rendering.wait(3)
            server.should_exit = True
            assert cancelled.wait(1), 'Shutdown ждёт HTTP, не отменив текущий export'
            request_thread.join(5)
            assert not request_thread.is_alive()
            assert responses[0].status_code == 409
            assert not app.state.service._export_lock.locked()
            assert list((tmp_path / 'exports' / 'tmp').iterdir()) == []
    finally:
        release.set()
        app.state.service._export_stop.set()
        if request_thread:
            request_thread.join(5)
        server.should_exit = True
        thread.join(10)
        listener.close()
        app.state.service.close()
        assert not thread.is_alive()
