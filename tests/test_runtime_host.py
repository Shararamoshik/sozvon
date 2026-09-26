"""WorkerHost is exercised against actual OS pipes and child processes."""

import importlib.util
import os
import sys
import threading
import time

import pytest

from sozvon.runtime.framing import encode_frame

PRELUDE = """
import os, sys, time
from sozvon.runtime.framing import read_frame, write_frame
source, channel = sys.stdin.buffer, sys.stdout.buffer
command = read_frame(source)
write_frame(channel, {"type": "ready", "pid": os.getpid()})
"""


def child(script):
    return [sys.executable, "-u", "-c", PRELUDE + script]


@pytest.fixture
def spawned(monkeypatch):
    # Observe real Popen objects, not a fake subprocess implementation. Always
    # reap deliberately broken children even when a RED assertion fails.
    import subprocess

    original = subprocess.Popen
    processes = []

    def launch(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", launch)
    yield processes
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX select for anonymous pipes")
def test_host_keeps_stdin_open_until_result(host_module, spawned):
    host = host_module.WorkerHost(
        command=child("""
import select
if select.select([source], [], [], 0.05)[0]:
    raise SystemExit("stdin was prematurely closed")
write_frame(channel, {"type": "result", "result": {"ok": True}})
""")
    )
    assert host.run("devices", {}, timeout=2) == {"ok": True}


@pytest.mark.parametrize("operation", ["record", "transcribe"])
def test_stop_command_saves_recording_but_cancels_other_operations(host_module, spawned, operation):
    event = threading.Event()
    host = host_module.WorkerHost(
        command=child("""
control = read_frame(source)
write_frame(channel, {"type": "result", "result": {"control": control}})
""")
    )
    callback = lambda message: event.set() if message["type"] == "ready" else None
    if operation == "record":
        result = host.run(operation, {}, timeout=2, stop_event=event, on_event=callback)
        assert result == {"control": {"operation": "stop"}}
    else:
        with pytest.raises(host_module.WorkerError) as error:
            host.run(operation, {}, timeout=2, stop_event=event, on_event=callback)
        assert error.value.code == "cancelled"
    assert spawned[-1].poll() is not None


@pytest.mark.parametrize(
    "raw,code",
    [
        (b"\x00", "protocol_error"),
        (b"\x00\x10\x00\x01", "protocol_error"),
        (encode_frame({"type": "nonsense"}), "protocol_error"),
        (encode_frame({"type": "result", "result": []}), "protocol_error"),
        (b"", "worker_exit"),
        (
            encode_frame({"type": "error", "code": "operation_error", "message": "Нет речи"}),
            "operation_error",
        ),
    ],
)
def test_host_translates_bad_output_and_clean_exit_to_worker_errors(
    host_module, spawned, raw, code
):
    host = host_module.WorkerHost(command=child(f"os.write(1, {raw!r})\n"))
    with pytest.raises(host_module.WorkerError) as error:
        host.run("ping", {}, timeout=2)
    assert error.value.code == code
    assert spawned[-1].poll() is not None


def test_timeout_terminates_and_reaps_uncooperative_child(host_module, spawned):
    host = host_module.WorkerHost(command=child("time.sleep(60)\n"), stop_grace=0.1)
    start = time.monotonic()
    with pytest.raises(host_module.WorkerError) as error:
        host.run("transcribe", {}, timeout=0.15)
    assert error.value.code == "timeout"
    assert time.monotonic() - start < 2
    assert spawned[-1].poll() is not None


def test_callback_failure_always_cleans_up_child(host_module, spawned):
    host = host_module.WorkerHost(command=child("time.sleep(60)\n"), stop_grace=0.1)

    def callback(event):
        raise LookupError("callback failure")

    with pytest.raises(LookupError, match="callback failure"):
        host.run("ping", {}, timeout=1, on_event=callback)
    assert spawned[-1].poll() is not None


def test_stderr_is_drained_but_retained_tail_is_bounded(host_module, spawned):
    host = host_module.WorkerHost(
        command=child("""
for _ in range(128):
    os.write(2, b"x" * 8192)
write_frame(channel, {"type": "result", "result": {"ok": True}})
""")
    )
    assert host.run("ping", {}, timeout=3) == {"ok": True}
    tail = host_module._StderrTail()
    for _ in range(100):
        tail.append(b"a" * 8192)
    tail.append(b"last")
    assert len(tail.data) == host_module.STDERR_LIMIT
    assert tail.data.endswith(b"last")


def test_timeout_also_covers_blocked_stdin_write(host_module, spawned):
    host = host_module.WorkerHost(
        command=[sys.executable, "-c", "import time; time.sleep(60)"], stop_grace=0.1
    )
    start = time.monotonic()
    with pytest.raises(host_module.WorkerError) as error:
        host.run("record", {"big": "x" * 900000}, timeout=0.1)
    assert error.value.code == "timeout"
    assert time.monotonic() - start < 2
    assert spawned[-1].poll() is not None


def test_cancel_reaps_child_ignoring_stop_and_sigterm(host_module, spawned):
    script = """
import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
write_frame(channel, {"type": "progress"})
time.sleep(60)
"""
    host = host_module.WorkerHost(command=child(script), stop_grace=0.1)
    stop = threading.Event()
    with pytest.raises(host_module.WorkerError) as error:
        host.run(
            "record",
            {},
            timeout=2,
            stop_event=stop,
            on_event=lambda event: stop.set() if event["type"] == "progress" else None,
        )
    assert error.value.code == "cancelled"
    assert spawned[-1].poll() is not None
    assert not any(t.name.startswith("sozvon-") for t in threading.enumerate())


@pytest.mark.parametrize(
    "operation,payload",
    [
        ("eval", {}),
        ("ping; touch /ignored", {}),
        ("ping", []),
        ("ping", {"big": "x" * 1024 * 1024}),
        ("ping", {"bad": float("nan")}),
    ],
)
def test_invalid_requests_are_rejected_before_spawn(host_module, spawned, operation, payload):
    with pytest.raises(host_module.WorkerError):
        host_module.WorkerHost().run(operation, payload)
    assert spawned == []


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_nonfinite_timeouts_are_rejected_before_spawn(host_module, spawned, timeout):
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost().run("ping", {}, timeout=timeout)
    assert error.value.code == "invalid_request"
    assert spawned == []


def test_presignalled_cancellation_does_not_start_child(host_module, spawned):
    stop = threading.Event()
    stop.set()
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost().run("record", {}, stop_event=stop)
    assert error.value.code == "cancelled"
    assert spawned == []


def test_missing_executable_has_public_worker_error(host_module):
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost(command=["/not-an-executable"]).run("ping", {})
    assert error.value.code == "worker_start"


def test_result_followed_by_crash_is_not_success(host_module, spawned):
    script = """
write_frame(channel, {"type": "result", "result": {"ok": True}})
os._exit(7)
"""
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost(command=child(script)).run("ping", {}, timeout=2)
    assert error.value.code == "worker_exit"


def test_complete_result_must_be_followed_by_clean_eof(host_module, spawned):
    script = """
write_frame(channel, {"type": "result", "result": {"ok": True}})
os.write(1, b"garbage")
"""
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost(command=child(script)).run("ping", {}, timeout=2)
    assert error.value.code == "protocol_error"


def test_result_then_hang_is_reaped_not_reported_as_success(host_module, spawned):
    script = """
write_frame(channel, {"type": "result", "result": {"ok": True}})
time.sleep(60)
"""
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost(command=child(script), stop_grace=0.1).run("ping", {}, timeout=2)
    assert error.value.code == "worker_exit"
    assert spawned[-1].poll() is not None


def test_unexpected_stderr_is_never_exposed_in_public_error(host_module, spawned):
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost(command=child('os.write(2, b"sk-private-token")')).run("ping", {})
    assert "sk-private-token" not in str(error.value)


def test_command_injection_can_only_be_literal_payload(host_module, spawned, tmp_path):
    target = tmp_path / "should not exist"
    injection = f"; touch '{target}'; $(touch '{target}')"
    script = 'write_frame(channel, {"type": "result", "result": command["payload"]})'
    host = host_module.WorkerHost(command=child(script))
    assert host.run("ping", {"text": injection}, timeout=2) == {"text": injection}
    assert not target.exists()


def test_flooded_progress_is_bounded_and_still_cancellable(host_module, spawned):
    script = """
while True:
    write_frame(channel, {"type": "progress", "blob": "x" * 65536})
"""
    host = host_module.WorkerHost(command=child(script), stop_grace=0.1)
    count = 0
    stop = threading.Event()

    def callback(event):
        nonlocal count
        count += 1
        time.sleep(0.002)
        if count > 3:
            stop.set()

    with pytest.raises(host_module.WorkerError) as error:
        host.run("transcribe", {}, stop_event=stop, timeout=2, on_event=callback)
    assert error.value.code == "cancelled"
    assert spawned[-1].poll() is not None
    assert not any(t.name.startswith("sozvon-") for t in threading.enumerate())


def test_host_end_to_end_record_stop_uses_real_worker(host_module):
    from test_runtime_worker import WAITING_STUB

    host = host_module.WorkerHost(command=[sys.executable, "-c", WAITING_STUB])
    event = threading.Event()
    result = host.run(
        "record",
        {},
        timeout=3,
        stop_event=event,
        on_event=lambda e: event.set() if e["type"] == "ready" else None,
    )
    assert result == {"operation": "record", "payload": {}}


def test_host_end_to_end_transcribe_cancel_uses_real_worker(host_module):
    from test_runtime_worker import WAITING_STUB

    host = host_module.WorkerHost(command=[sys.executable, "-c", WAITING_STUB])
    event = threading.Event()
    with pytest.raises(host_module.WorkerError) as error:
        host.run(
            "transcribe",
            {},
            timeout=3,
            stop_event=event,
            on_event=lambda e: event.set() if e["type"] == "ready" else None,
        )
    assert error.value.code == "cancelled"


def test_explicit_stop_uses_host_grace_not_orphan_watchdog(host_module):
    from test_runtime_worker import WAITING_STUB

    script = WAITING_STUB.replace(
        'return {"operation": operation, "payload": payload}',
        'import time; time.sleep(0.12)\n    return {"operation": operation, "payload": payload}',
    )
    script = script.replace(
        "from sozvon.runtime.worker import main",
        "import sozvon.runtime.worker as worker; worker.CONTROL_GRACE = 0.04\n"
        "from sozvon.runtime.worker import main",
    )
    stop = threading.Event()
    host = host_module.WorkerHost(command=[sys.executable, "-c", script], stop_grace=1)
    result = host.run(
        "record",
        {},
        timeout=3,
        stop_event=stop,
        on_event=lambda e: stop.set() if e["type"] == "ready" else None,
    )
    assert result["operation"] == "record"


def test_default_host_uses_application_worker_dispatch(host_module):
    result = host_module.WorkerHost().run("ping", {}, timeout=5)
    assert result["pid"] != os.getpid()
    assert result["python"] == sys.executable


def test_incomplete_frame_without_eof_is_interrupted_by_timeout(host_module, spawned):
    host = host_module.WorkerHost(
        command=child('os.write(1, b"\\x00"); time.sleep(60)'), stop_grace=0.1
    )
    with pytest.raises(host_module.WorkerError) as error:
        host.run("ping", {}, timeout=0.15)
    assert error.value.code == "timeout"
    assert spawned[-1].poll() is not None


@pytest.mark.parametrize(
    "events",
    [
        [{"type": "ready", "pid": 1}, {"type": "ready", "pid": 1}],
        [{"type": "ready", "pid": True}],
        [{"type": "progress"}],
        [{"type": "result", "result": {}}],
        [{"type": "ready", "pid": 1}, {"type": "error", "code": 5, "message": "x"}],
        [{"type": "ready", "pid": 1}, {"type": "result", "result": {}}, {"type": "progress"}],
    ],
)
def test_invalid_event_sequences_are_protocol_errors(host_module, spawned, events):
    command = [
        sys.executable,
        "-c",
        f"import os; os.write(1, {b''.join(map(encode_frame, events))!r})",
    ]
    with pytest.raises(host_module.WorkerError) as error:
        host_module.WorkerHost(command=command).run("ping", {}, timeout=2)
    assert error.value.code == "protocol_error"


def test_callback_cannot_mutate_the_returned_result(host_module, spawned):
    host = host_module.WorkerHost(
        command=child('write_frame(channel, {"type": "result", "result": {"items": ["original"]}})')
    )

    def callback(event):
        if event["type"] == "result":
            event["result"]["items"].append("injected")
            event["type"] = "error"

    assert host.run("ping", {}, timeout=2, on_event=callback) == {"items": ["original"]}


@pytest.fixture
def host_module():
    assert importlib.util.find_spec("sozvon.runtime.host") is not None, "host is not implemented"
    from sozvon.runtime import host

    return host


def test_host_runs_a_new_real_child_per_call_and_forwards_events(host_module):
    host = host_module.WorkerHost(command=[sys.executable, "-m", "sozvon.runtime.worker"])
    events = []
    first = host.run("ping", {}, timeout=5, on_event=events.append)
    second = host.run("ping", {}, timeout=5)
    assert first["pid"] not in (os.getpid(), second["pid"])
    assert [event["type"] for event in events] == ["ready", "result"]
    if os.name == "posix":
        with pytest.raises(ProcessLookupError):
            os.kill(first["pid"], 0)


def test_source_and_frozen_commands_are_fixed_not_payload_derived(host_module, monkeypatch):
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert host_module.worker_command() == [sys.executable, "-m", "sozvon", "--worker"]
    monkeypatch.setattr(sys, "frozen", True)
    assert host_module.worker_command() == [sys.executable, "--worker"]
