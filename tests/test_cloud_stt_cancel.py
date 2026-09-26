"""Cooperative cancellation closes resources and never publishes partial STT."""

import threading
from pathlib import Path

import httpx
import pytest
from test_cloud_stt_upload import audio_payload as audio_payload  # noqa: PLC0414 - fixture export
from test_cloud_stt_upload import install_transport


def test_cancel_read_closes_stream_and_has_no_result(monkeypatch, audio_payload):
    from sozvon.audio.cloud_stt import transcribe_cloud

    stop, closed = threading.Event(), []

    class CancelResponse(httpx.SyncByteStream):
        def __iter__(self):
            stop.set()
            yield b'{"text":"synthetic"}'

        def close(self):
            closed.append(True)

    clients = install_transport(
        monkeypatch, lambda request: httpx.Response(200, stream=CancelResponse())
    )
    with pytest.raises(RuntimeError, match="отменена"):
        transcribe_cloud(audio_payload, stop, lambda e: None)
    assert closed == [True] and clients[0].is_closed
    assert list(Path(audio_payload["output_dir"]).iterdir()) == []


def test_cancel_during_upload_stops_stream(monkeypatch, audio_payload):
    from sozvon.audio.cloud_stt import transcribe_cloud

    stop, chunks = threading.Event(), []

    class CancelUpload(httpx.BaseTransport):
        def handle_request(self, request):
            for chunk in request.stream:
                chunks.append(chunk)
                stop.set()
            return httpx.Response(200, json={"text": "synthetic"})

    real_client = httpx.Client
    clients = []

    def factory(**kwargs):
        client = real_client(transport=CancelUpload(), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr("sozvon.audio.cloud_stt.httpx.Client", factory)
    with pytest.raises(RuntimeError, match="отменена"):
        transcribe_cloud(audio_payload, stop, lambda e: None)
    assert clients[0].is_closed
    assert len(chunks) == 1
    assert list(Path(audio_payload["output_dir"]).iterdir()) == []


def test_pre_cancel_does_not_prepare_audio_or_create_client(monkeypatch, audio_payload):
    from sozvon.audio import cloud_stt

    stop = threading.Event()
    stop.set()

    def forbid(*args, **kwargs):
        pytest.fail("audio or client invoked after cancellation")

    monkeypatch.setattr(cloud_stt, "prepared_audio", forbid)
    monkeypatch.setattr(cloud_stt.httpx, "Client", forbid)
    with pytest.raises(RuntimeError, match="отменена"):
        cloud_stt.transcribe_cloud(audio_payload, stop, lambda e: None)


def test_cancel_after_normalization_does_not_publish_result(monkeypatch, audio_payload):
    from sozvon.audio import cloud_stt

    stop = threading.Event()
    normalize = cloud_stt.normalize_response

    def cancel(*args):
        result = normalize(*args)
        stop.set()
        return result

    monkeypatch.setattr(cloud_stt, "normalize_response", cancel)
    install_transport(monkeypatch, lambda request: httpx.Response(200, json={"text": "synthetic"}))
    with pytest.raises(RuntimeError, match="отменена"):
        cloud_stt.transcribe_cloud(audio_payload, stop, lambda e: None)


def test_cancel_at_send_progress_makes_no_request(monkeypatch, audio_payload):
    from sozvon.audio.cloud_stt import transcribe_cloud

    stop = threading.Event()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"text": "synthetic"})

    clients = install_transport(monkeypatch, handler)

    def emit(event):
        if event["stage"] == "Отправка аудио в сервис":
            stop.set()

    with pytest.raises(RuntimeError, match="отменена"):
        transcribe_cloud(audio_payload, stop, emit)
    assert calls == []
    assert all(client.is_closed for client in clients)
    assert list(Path(audio_payload["output_dir"]).iterdir()) == []
