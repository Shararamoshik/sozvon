"""Integration probes use real subprocesses and bounded outer timeouts."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sozvon import __version__
from sozvon.runtime.framing import encode_frame, read_frame

ROOT = Path(__file__).resolve().parents[1]
COMMAND = [sys.executable, "-m", "sozvon.runtime.worker"]


def frames(data):
    stream = io.BytesIO(data)
    events = []
    while (event := read_frame(stream)) is not None:
        events.append(event)
    return events


def invoke(data, *, command=COMMAND, timeout=5):
    return subprocess.run(
        command, input=data, capture_output=True, cwd=ROOT, timeout=timeout, check=False
    )


def test_real_worker_ping_is_framed_and_reports_child_identity():
    completed = invoke(encode_frame({"operation": "ping", "payload": {}}))
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")
    events = frames(completed.stdout)
    assert [e["type"] for e in events] == ["ready", "result"]
    result = events[-1]["result"]
    assert result["pid"] != os.getpid()
    assert result["python"] == sys.executable
    assert result["version"] == __version__


@pytest.mark.parametrize(
    "data,code",
    [
        (b"\x00", "protocol_error"),
        (b"\x00\x10\x00\x01", "protocol_error"),
        (encode_frame({"operation": "execute", "payload": {}}), "unknown_operation"),
        (encode_frame({"operation": "ping", "payload": []}), "invalid_request"),
        (encode_frame({"operation": ["ping"], "payload": {}}), "invalid_request"),
        (encode_frame({"operation": "ping", "payload": {}, "extra": 1}), "invalid_request"),
    ],
)
def test_worker_rejects_invalid_frames_and_non_allowlisted_operations(data, code):
    completed = invoke(data)
    events = frames(completed.stdout)
    assert completed.returncode != 0
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == code
    assert "Traceback" not in completed.stderr.decode(errors="replace")


def test_worker_clean_initial_eof_exits_without_events():
    completed = invoke(b"")
    assert completed.returncode == 0
    assert completed.stdout == b""


def test_ping_does_not_import_audio_or_llm_modules():
    script = """
import sys
from sozvon.runtime.worker import main
code = main()
assert not any(k.startswith(("sozvon.audio", "sozvon.llm", "av", "sounddevice",
                             "faster_whisper")) for k in sys.modules)
raise SystemExit(code)
"""
    completed = invoke(
        encode_frame({"operation": "ping", "payload": {}}), command=[sys.executable, "-c", script]
    )
    assert completed.returncode == 0, completed.stderr.decode(errors="replace")


STUB = """
import os, sys, types
module = types.ModuleType("sozvon.audio.operations")
def run(operation, payload, stop_event, emit):
    print("python chatter", flush=True)
    os.write(1, b"native chatter\\n")
    emit({"type": "progress", "stage": "Готово"})
    return {"operation": operation, "payload": payload}
module.run = run
sys.modules[module.__name__] = module
provider = types.ModuleType("sozvon.llm.provider")
provider.generate = lambda payload, stop_event, emit: {"document": payload}
sys.modules[provider.__name__] = provider
from sozvon.runtime.worker import main
raise SystemExit(main())
"""


@pytest.mark.parametrize("operation", ["devices", "audio_info", "transcribe", "record", "report"])
def test_lazy_dispatch_and_python_and_native_stdout_isolation(operation):
    # Keep input open: EOF is deliberately an operation cancellation signal.
    process = subprocess.Popen(
        [sys.executable, "-c", STUB],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        process.stdin.write(encode_frame({"operation": operation, "payload": {"text": "Привет"}}))
        process.stdin.flush()
        process.wait(timeout=5)
        output, stderr = process.communicate(timeout=2)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
    events = frames(output)
    assert process.returncode == 0, stderr.decode(errors="replace")
    if operation == "report":
        assert events[-1]["result"] == {"document": {"text": "Привет"}}
    else:
        assert [e["type"] for e in events] == ["ready", "progress", "result"]
        assert events[-1]["result"] == {"operation": operation, "payload": {"text": "Привет"}}
        assert b"python chatter" in stderr
        assert b"native chatter" in stderr


WAITING_STUB = STUB.replace('print("python chatter", flush=True)', "stop_event.wait(30)")


@pytest.mark.parametrize("operation", ["record", "transcribe", "report"])
@pytest.mark.parametrize("ending", ["stop", "eof", "malformed"])
def test_worker_control_stop_and_eof_save_only_recording(operation, ending):
    script = WAITING_STUB.replace(
        'provider.generate = lambda payload, stop_event, emit: {"document": payload}',
        'provider.generate = lambda payload, stop_event, emit: run("report", payload, stop_event, emit)',
    )
    command = encode_frame({"operation": operation, "payload": {}})
    if ending == "stop":
        command += encode_frame({"operation": "stop"})
    elif ending == "malformed":
        command += b"\x00"
    completed = invoke(command, command=[sys.executable, "-c", script])
    events = frames(completed.stdout)
    if ending == "malformed":
        assert events[-1]["type"] == "error"
        assert events[-1]["code"] == "protocol_error"
    elif operation == "record":
        assert events[-1]["type"] == "result"
        assert events[-1]["result"]["operation"] == "record"
    else:
        assert events[-1]["type"] == "error"
        assert events[-1]["code"] == "cancelled"


@pytest.mark.parametrize(
    "failure,message",
    [
        ('raise OSError("sk-private-key")', "Не удалось выполнить операцию worker"),
        ('raise KeyError("sk-private-key")', "Не удалось выполнить операцию worker"),
        ('raise ValueError("Нет речи")', "Нет речи"),
        ("return []", "Обработчик вернул некорректный результат"),
    ],
)
def test_handler_errors_become_safe_framed_error(failure, message):
    script = STUB.replace('print("python chatter", flush=True)', failure)
    # Record is allowed to finish when invoke closes stdin.
    completed = invoke(
        encode_frame({"operation": "record", "payload": {}}), command=[sys.executable, "-c", script]
    )
    events = frames(completed.stdout)
    assert completed.returncode != 0
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "operation_error"
    assert events[-1]["message"] == message
    assert b"sk-private-key" not in completed.stdout + completed.stderr


def test_handler_safe_errors_still_redact_api_key():
    script = STUB.replace(
        'print("python chatter", flush=True)',
        'raise ValueError("invalid key: " + payload["api_key"])',
    )
    completed = invoke(
        encode_frame({"operation": "record", "payload": {"api_key": "private-test-secret"}}),
        command=[sys.executable, "-c", script],
    )
    assert b"private-test-secret" not in completed.stdout + completed.stderr
    assert frames(completed.stdout)[-1]["type"] == "error"


def test_control_cancelled_handler_error_remains_cancellation():
    script = WAITING_STUB.replace(
        'return {"operation": operation, "payload": payload}',
        'raise ValueError("Обработка остановлена")',
    )
    completed = invoke(
        encode_frame({"operation": "transcribe", "payload": {}}),
        command=[sys.executable, "-c", script],
    )
    assert frames(completed.stdout)[-1]["code"] == "cancelled"


@pytest.mark.skipif(os.name != "posix", reason="libc symbol lookup is POSIX-specific")
def test_native_c_stdio_chatter_cannot_corrupt_frames():
    script = STUB.replace(
        'print("python chatter", flush=True)',
        'import ctypes; libc = ctypes.CDLL(None); libc.printf(b"C stdio chatter\\\\n"); libc.fflush(None)',
    )
    completed = invoke(
        encode_frame({"operation": "record", "payload": {}}), command=[sys.executable, "-c", script]
    )
    assert completed.returncode == 0
    assert frames(completed.stdout)[-1]["type"] == "result"
    assert b"C stdio chatter" in completed.stderr


def test_control_eof_forces_exit_if_operation_ignores_stop():
    script = STUB.replace('print("python chatter", flush=True)', "import time; time.sleep(60)")
    completed = invoke(
        encode_frame({"operation": "record", "payload": {}}), command=[sys.executable, "-c", script], timeout=20
    )
    assert completed.returncode != 0
    assert [e["type"] for e in frames(completed.stdout)] == ["ready"]
