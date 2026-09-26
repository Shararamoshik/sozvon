"""One fresh child per run, bounded pipes/queues and deterministic cleanup.

`command` is a trusted embedding/test hook, never user payload. Production uses
worker_command(). Callbacks run synchronously on the caller's thread and must
return promptly; runtime deadlines cannot preempt arbitrary callback code.
"""

import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from copy import deepcopy

from sozvon.runtime.framing import FrameError, encode_frame, read_frame, write_bytes
from sozvon.runtime.protocol import WorkerError, validate_request

__all__ = ["WorkerError", "WorkerHost", "worker_command"]
STDERR_LIMIT = 64 * 1024
EVENT_QUEUE_LIMIT = 8
POLL_INTERVAL = 0.02


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    return [sys.executable, "-m", "sozvon", "--worker"]


class _StderrTail:
    """Bounded diagnostic tail, deliberately never included in public errors."""

    def __init__(self):
        self.data = bytearray()

    def append(self, chunk: bytes):
        self.data.extend(chunk[-STDERR_LIMIT:])
        del self.data[:-STDERR_LIMIT]


def _signal_process(process, *, kill=False):
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL if kill else signal.SIGTERM)
        elif process.poll() is None:
            (process.kill if kill else process.terminate)()
    except ProcessLookupError:
        pass


def _cleanup(process, done, threads):
    done.set()
    if process.poll() is None:
        _signal_process(process)
        try:
            process.wait(timeout=0.3)
        except subprocess.TimeoutExpired:
            _signal_process(process, kill=True)
            process.wait(timeout=1)
    # Also release inherited pipes held by descendants in our private POSIX group.
    if os.name == "posix":
        _signal_process(process, kill=True)
    deadline = time.monotonic() + 0.5
    for thread in threads:
        thread.join(timeout=max(0, deadline - time.monotonic()))
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _validate_event(event, ready, terminal):
    kind = event.get("type")
    if terminal or not isinstance(kind, str):
        raise WorkerError("protocol_error", "Некорректная последовательность событий worker")
    if kind == "ready":
        if ready or type(event.get("pid")) is not int or event["pid"] <= 0:
            raise WorkerError("protocol_error", "Некорректное событие ready")
    elif kind == "progress":
        if not ready:
            raise WorkerError("protocol_error", "Progress до ready")
    elif kind == "result":
        if not ready or not isinstance(event.get("result"), dict):
            raise WorkerError("protocol_error", "Некорректный результат worker")
    elif kind == "error":
        if not isinstance(event.get("code"), str) or not isinstance(event.get("message"), str):
            raise WorkerError("protocol_error", "Некорректная ошибка worker")
    else:
        raise WorkerError("protocol_error", "Неизвестное событие worker")
    return kind


class WorkerHost:
    def __init__(self, *, command: Sequence[str] | None = None, stop_grace: float | None = None):
        command = worker_command() if command is None else command
        if (
            isinstance(command, (str, bytes))
            or not command
            or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
        ):
            raise ValueError("command должен быть непустым списком аргументов")
        if stop_grace is not None and (not math.isfinite(stop_grace) or stop_grace <= 0):
            raise ValueError("stop_grace должен быть положительным конечным числом")
        self.command = tuple(command)
        self.stop_grace = stop_grace

    def run(
        self,
        operation: str,
        payload: dict,
        *,
        timeout: float = 120,
        stop_event: threading.Event | None = None,
        on_event: Callable[[dict], None] | None = None,
    ) -> dict:
        command = {"operation": operation, "payload": payload}
        validate_request(command)
        if not math.isfinite(timeout) or timeout <= 0:
            raise WorkerError(
                "invalid_request", "timeout должен быть положительным конечным числом"
            )
        try:
            request = encode_frame(command)
        except FrameError as exc:
            raise WorkerError("invalid_request", str(exc)) from exc
        if stop_event is not None and stop_event.is_set():
            raise WorkerError("cancelled", "Операция отменена до запуска")
        grace = self.stop_grace if self.stop_grace is not None else (15.0 if operation == "record" else 2.0)
        deadline = time.monotonic() + timeout
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
                close_fds=True,
                start_new_session=(os.name == "posix"),
            )
        except OSError as exc:
            raise WorkerError("worker_start", "Не удалось запустить worker") from exc

        done = threading.Event()
        commands = queue.Queue(maxsize=1)
        events = queue.Queue(maxsize=EVENT_QUEUE_LIMIT)
        stderr = _StderrTail()
        threads = []

        def deliver(kind, value=None):
            while not done.is_set():
                try:
                    events.put((kind, value), timeout=POLL_INTERVAL)
                    return
                except queue.Full:
                    pass

        def read_output():
            try:
                while not done.is_set():
                    event = read_frame(process.stdout)
                    if event is None:
                        deliver("eof")
                        return
                    deliver("event", event)
            except FrameError as exc:
                deliver("protocol", str(exc))
            except OSError:
                deliver("eof")

        def read_stderr():
            try:
                while chunk := process.stderr.read(8192):
                    stderr.append(chunk)
            except OSError:
                pass

        def write_input():
            try:
                write_bytes(process.stdin, request)
                while not done.is_set():
                    try:
                        control = commands.get(timeout=POLL_INTERVAL)
                    except queue.Empty:
                        continue
                    write_bytes(process.stdin, control)
            except OSError:
                # The output reader provides the authoritative exit/error; a
                # broken stdin must not race and hide a useful worker error.
                pass

        stop_reason = None
        stop_deadline = None
        terminal = None
        exit_deadline = None
        ready = False
        eof = False
        try:
            for name, target in (
                ("stdout", read_output),
                ("stderr", read_stderr),
                ("stdin", write_input),
            ):
                thread = threading.Thread(
                    target=target, name=f"sozvon-{name}-{process.pid}", daemon=True
                )
                thread.start()
                threads.append(thread)
            while True:
                now = time.monotonic()
                if stop_reason is None:
                    if now >= deadline:
                        stop_reason = "timeout"
                    elif stop_event is not None and stop_event.is_set():
                        stop_reason = "cancelled"
                    if stop_reason:
                        commands.put_nowait(encode_frame({"operation": "stop"}))
                        stop_deadline = now + grace
                if stop_deadline is not None and now >= stop_deadline:
                    raise WorkerError(
                        stop_reason,
                        "Время ожидания истекло"
                        if stop_reason == "timeout"
                        else "Операция отменена",
                    )
                if terminal is not None and eof and process.poll() is not None:
                    if stop_reason == "timeout" or (
                        stop_reason == "cancelled" and operation != "record"
                    ):
                        raise WorkerError(
                            stop_reason,
                            "Время ожидания истекло"
                            if stop_reason == "timeout"
                            else "Операция отменена",
                        )
                    if terminal["type"] == "error":
                        raise WorkerError(terminal["code"], terminal["message"])
                    if process.returncode != 0:
                        raise WorkerError(
                            "worker_exit", "Worker аварийно завершился после результата"
                        )
                    return terminal["result"]
                if eof and terminal is None:
                    raise WorkerError(
                        stop_reason or "worker_exit", "Worker завершился без результата"
                    )
                if exit_deadline is not None and now >= exit_deadline:
                    raise WorkerError(
                        stop_reason or "worker_exit", "Worker не завершился после результата"
                    )
                try:
                    kind, value = events.get(timeout=POLL_INTERVAL)
                except queue.Empty:
                    continue
                if kind == "protocol":
                    raise WorkerError("protocol_error", value)
                if kind == "eof":
                    eof = True
                    continue
                event_type = _validate_event(value, ready, terminal)
                if event_type == "ready":
                    ready = True
                if event_type in {"result", "error"}:
                    terminal = value
                    exit_deadline = time.monotonic() + grace
                if on_event is not None:
                    on_event(deepcopy(value))
        finally:
            _cleanup(process, done, threads)
