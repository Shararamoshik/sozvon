"""Single-operation worker entry point; no application/UI imports.

Call main() before importing noisy native/application libraries. FD 1 is redirected
for the entire remaining lifetime of this worker, not just Python print calls.
"""

import os
import sys
import threading

from sozvon import __version__
from sozvon.runtime.framing import FrameError, read_frame, write_frame
from sozvon.runtime.protocol import WorkerError, validate_request

CONTROL_GRACE = 3.0


def _channels():
    # Separate non-inheritable protocol FD: native code may write freely to fd 1.
    channel_fd = os.dup(1)
    os.set_inheritable(channel_fd, False)
    os.dup2(2, 1)
    if os.name == "nt":
        import msvcrt

        msvcrt.setmode(channel_fd, os.O_BINARY)
        msvcrt.setmode(0, os.O_BINARY)
    if sys.stderr is not None:
        sys.stdout = sys.stderr
    else:
        sys.stdout = os.fdopen(os.dup(2), "w", encoding="utf-8", buffering=1)
    # A raw (not buffered) reader avoids CPython shutdown locks in a daemon thread.
    return os.fdopen(os.dup(0), "rb", buffering=0), os.fdopen(channel_fd, "wb", buffering=0)


def _dispatch(operation, payload, stop_event, emit):
    if operation == "ping":
        return {"pid": os.getpid(), "python": sys.executable, "version": __version__}
    if operation == "report":
        from sozvon.llm.provider import generate

        return generate(payload, stop_event, emit)
    if operation == "transcribe_cloud":
        from sozvon.audio.cloud_stt import transcribe_cloud

        return transcribe_cloud(payload, stop_event, emit)
    if operation == "render_export":
        from sozvon.export.worker import render_export

        return render_export(payload, stop_event, emit)
    from sozvon.audio.operations import run

    return run(operation, payload, stop_event, emit)


def _serve(source, channel) -> int:
    stop_event = threading.Event()
    finished = threading.Event()
    control_errors = []
    operation = None
    payload = {}
    lock = threading.Lock()

    def send(event):
        with lock:
            write_frame(channel, event)

    def emit(event):
        if not isinstance(event, dict) or event.get("type") != "progress":
            raise FrameError("Обработчик может посылать только progress")
        send(event)

    def control():
        try:
            while True:
                command = read_frame(source)
                if command is None:
                    break
                if command == {"operation": "stop"}:
                    # Host owns the explicit-stop deadline. Keep reading so a
                    # later host disconnect still arms the orphan watchdog.
                    stop_event.set()
                    continue
                raise FrameError("Ожидается управляющая команда stop")
        except (FrameError, OSError) as exc:
            control_errors.append(str(exc))
        finally:
            stop_event.set()
            # A disconnected host cannot reap us if native code ignores stop.
            if not finished.wait(15.0 if operation == "record" else CONTROL_GRACE):
                os._exit(1)

    try:
        command = read_frame(source)
        if command is None:
            return 0
        operation, payload = validate_request(command)
        # Windows NumPy initialisation can deadlock against a thread blocked on
        # the inherited CRT stdin pipe. Load its native runtime before starting
        # that reader; the host still enforces the startup deadline.
        if os.name == "nt" and operation in {"audio_info", "transcribe", "transcribe_cloud", "record"}:
            import numpy  # noqa: F401
        # Ping is a one-shot diagnostic, valid even with stdin already at EOF.
        if operation != "ping":
            threading.Thread(target=control, name="sozvon-control", daemon=True).start()
        send({"type": "ready", "pid": os.getpid()})
        result = _dispatch(operation, payload, stop_event, emit)
        if control_errors:
            raise WorkerError("protocol_error", control_errors[0])
        if stop_event.is_set() and operation != "record":
            raise WorkerError("cancelled", "Операция отменена")
        if not isinstance(result, dict):
            raise WorkerError("operation_error", "Обработчик вернул некорректный результат")
        send({"type": "result", "result": result})
        return 0
    except BrokenPipeError:
        return 1
    except Exception as exc:  # noqa: BLE001 -- process boundary: serialize a safe error, no traceback
        if control_errors:
            code, message = "protocol_error", control_errors[0]
        elif stop_event.is_set() and operation != "record":
            code, message = "cancelled", "Операция отменена"
        elif isinstance(exc, WorkerError):
            code, message = exc.code, exc.message
        elif isinstance(exc, FrameError):
            code, message = "protocol_error", str(exc)
        elif isinstance(exc, (ValueError, RuntimeError)):
            code, message = "operation_error", str(exc)
        else:
            # Never serialize arbitrary tracebacks/exception repr (may contain keys).
            code, message = "operation_error", "Не удалось выполнить операцию worker"
        secret = payload.get("api_key")
        if isinstance(secret, str) and secret:
            message = message.replace(secret, "[ключ скрыт]")
        try:
            send({"type": "error", "code": code, "message": message[:4096]})
        except (OSError, FrameError):
            pass
        return 1
    finally:
        finished.set()


def main() -> int:
    source, channel = _channels()
    try:
        return _serve(source, channel)
    finally:
        channel.close()
        # Do not close source while the daemon is blocked on a raw read. The OS
        # closes it on worker exit; this entry point is not an in-process library.


if __name__ == "__main__":
    raise SystemExit(main())
