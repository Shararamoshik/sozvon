"""Real subprocess/loopback regressions; all audio, keys and responses are synthetic."""

import json
import os
import threading
import wave
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sozvon.runtime.host import WorkerError, WorkerHost

SAFE_TEXT = 'Синтетическая речь: "план" готов.\nНе обрезать и не подменять.'
FIRST_TEXT = "Начало синтетического ответа."
SYNTHETIC_KEY = "SYNTHETIC-STT-REVIEW-KEY"


@contextmanager
def synthetic_provider(*, timed, echo_key):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            authorization = self.headers.get("Authorization")
            calls.append((self.path, authorization))
            text = SAFE_TEXT
            if echo_key:
                # Reflect the actual incoming credential, not a separately injected value.
                assert authorization is not None
                text += " Provider echoed: " + authorization.removeprefix("Bearer ")
            data = {"text": text}
            if timed:
                data = {
                    "text": FIRST_TEXT,
                    "segments": [
                        {"text": FIRST_TEXT, "start": 0, "end": 0.5},
                        {"text": text, "start": 0.5, "end": 1},
                    ],
                }
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        thread.join(5)
        server.server_close()


def audio_payload(tmp_path, port, key, timed):
    source = tmp_path / "synthetic.wav"
    with wave.open(str(source), "wb") as wav:
        wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\x00\x00" * 16000)
    return {
        "paths": [str(source)],
        "output_dir": str(tmp_path / "staging"),
        "base_url": f"http://127.0.0.1:{port}/v1",
        "model": "synthetic-model",
        "response_format": "verbose_json" if timed else "json",
        "timeout_s": 120,
        "max_upload_bytes": 24_000_000,
        "api_key": key,
        "language": "auto",
        "allow_remote": False,
    }


@pytest.mark.parametrize("timed", [False, True], ids=["json", "verbose_json"])
@pytest.mark.parametrize(
    "key", [SYNTHETIC_KEY, 'SYNTHETIC-"quoted"-\\-STT-KEY'], ids=["plain", "json-escaped"]
)
def test_worker_rejects_echoed_bearer_key_in_transcript(tmp_path, timed, key):
    events = []
    with synthetic_provider(timed=timed, echo_key=True) as (port, calls):
        payload = audio_payload(tmp_path, port, key, timed)
        with pytest.raises(WorkerError, match="конфиденциальные данные") as error:
            WorkerHost().run(
                "transcribe_cloud", payload, timeout=15, on_event=events.append
            )

    assert error.value.code == "operation_error"
    assert "расшифровка не сохранена" in str(error.value)
    assert key not in str(error.value)
    assert json.dumps(key, ensure_ascii=False)[1:-1] not in json.dumps(
        events, ensure_ascii=False
    )
    assert events[0]["type"] == "ready" and events[0]["pid"] != os.getpid()
    assert events[-1]["type"] == "error"
    assert not any(event["type"] == "result" for event in events)
    assert calls == [("/v1/audio/transcriptions", "Bearer " + key)]
    assert list(Path(payload["output_dir"]).iterdir()) == []


@pytest.mark.parametrize("timed", [False, True], ids=["json", "verbose_json"])
@pytest.mark.parametrize("key", [SYNTHETIC_KEY, None, ""], ids=["key", "none", "empty"])
def test_worker_preserves_safe_transcript(tmp_path, timed, key):
    events = []
    with synthetic_provider(timed=timed, echo_key=False) as (port, calls):
        payload = audio_payload(tmp_path, port, key, timed)
        result = WorkerHost().run(
            "transcribe_cloud", payload, timeout=15, on_event=events.append
        )

    expected_text = [FIRST_TEXT, SAFE_TEXT] if timed else [SAFE_TEXT]
    assert [row["text"] for row in result["segments"]] == expected_text
    expected_times = [(0, 500), (500, 1000)] if timed else [(None, None)]
    assert [(row["start_ms"], row["end_ms"]) for row in result["segments"]] == expected_times
    assert result["timed"] is timed
    assert result["model"] == "synthetic-model" and result["device"] == "cloud"
    assert events[0]["type"] == "ready" and events[0]["pid"] != os.getpid()
    assert events[-1] == {"type": "result", "result": result}
    assert calls == [("/v1/audio/transcriptions", "Bearer " + key if key else None)]
    assert list(Path(payload["output_dir"]).iterdir()) == []
