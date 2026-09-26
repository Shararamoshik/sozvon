"""Real worker and loopback HTTP with explicitly synthetic STT responses."""

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import av
import pytest
from test_cloud_stt_upload import audio_payload as audio_payload  # noqa: PLC0414 - fixture export
from test_cloud_stt_upload import multipart

from sozvon.runtime.host import WorkerHost


def test_cloud_worker_real_multipart_and_synthetic_response(audio_payload):
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            body = self.rfile.read(size)
            observed.append((self.path, dict(self.headers), body))
            response = json.dumps(
                {
                    "segments": [
                        {
                            "text": "Синтетический ответ, не распознавание тона.",
                            "start": 0,
                            "end": 1,
                        }
                    ]
                },
                ensure_ascii=False,
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        audio_payload["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
        audio_payload["api_key"] = None  # Local synthetic services need no key or consent.
        events = []
        result = WorkerHost().run(
            "transcribe_cloud", audio_payload, timeout=20, on_event=events.append
        )
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()
    assert result["device"] == "cloud" and result["timed"] is True
    assert result["segments"][0]["text"] == "Синтетический ответ, не распознавание тона."
    assert result["segments"][0]["end_ms"] == 1000
    assert len(observed) == 1
    path, headers, body = observed[0]
    assert path == "/v1/audio/transcriptions"
    assert "Authorization" not in headers
    assert int(headers["Content-Length"]) == len(body)
    parts = multipart(headers["Content-Type"], body)
    assert parts["model"].get_payload(decode=True) == b"exact-model-ID"
    with av.open(io.BytesIO(parts["file"].get_payload(decode=True))) as source:
        assert source.duration / av.time_base == pytest.approx(1)
    assert list(Path(audio_payload["output_dir"]).iterdir()) == []
    assert any(event["type"] == "progress" for event in events)
