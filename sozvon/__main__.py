"""Точка входа интерфейса, диагностических проб и закрытого worker."""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

from sozvon import __version__


def main(argv: list[str] | None = None) -> int:
    args_raw = list(sys.argv[1:] if argv is None else argv)
    # Проверяется прежде всех импортов веба/моделей, также в замороженной сборке.
    if args_raw == ["--worker"]:
        from sozvon.runtime.worker import main as worker_main
        return worker_main()
    parser = argparse.ArgumentParser(description="Созвон — локальные записи и отчёты")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8098)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--probe", choices=["ping", "devices", "audio_info", "transcribe", "record"])
    parser.add_argument("--input", type=Path)
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seconds", type=int, default=5)
    args = parser.parse_args(args_raw)
    if args.version:
        print(f"Созвон {__version__}")
        return 0
    if args.probe:
        from sozvon.audio.paths import local_path
        from sozvon.runtime.host import WorkerError, WorkerHost
        payload = {}
        if args.input:
            payload["path"] = str(local_path(os.path.abspath(args.input)))
        if args.model_path:
            payload["model_path"] = str(local_path(os.path.abspath(args.model_path)))
            payload.update(device="cpu", compute_type="int8", language="ru")
        if args.probe == "record":
            if not args.output:
                parser.error("Для записи укажите --output с отдельной папкой")
            payload.update(output_dir=str(local_path(os.path.abspath(args.output))), max_seconds=args.seconds,
                           allow_partial=False, input_device=None, output_device=None)
        try:
            result = WorkerHost().run(args.probe, payload, timeout=max(120, args.seconds + 30))
        except WorkerError as exc:
            print(json.dumps({"error": str(exc), "code": exc.code}, ensure_ascii=True))
            return 1
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    import uvicorn
    from platformdirs import user_data_path

    from sozvon.instance import InstanceLock
    from sozvon.web.lifecycle import ApplicationServer
    from sozvon.web.server import create_app

    root = (args.data_dir or user_data_path("sozvon", appauthor=False)).resolve()
    instance = InstanceLock(root)
    if not instance.acquire():
        print("Созвон уже работает. Откройте его вкладку в браузере.", file=sys.stderr)
        return 1
    app = create_app(root)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", args.port))
    except OSError:
        sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = ApplicationServer(uvicorn.Config(app, log_level="warning", access_log=False))
    url = f"http://127.0.0.1:{port}/#key={app.state.launch_key}"
    runtime = root / "runtime.json"

    def ready():
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline and not server.should_exit:
            time.sleep(0.05)
        if not server.started:
            return
        runtime.write_text(json.dumps({"pid": os.getpid(), "url": url}, ensure_ascii=True),
                           encoding="utf-8")
        if os.name != "nt":
            runtime.chmod(0o600)
        # Не печатать одноразовый ключ в журнал. Для CLI ссылка хранится в runtime.json.
        print(f"Созвон: http://127.0.0.1:{port}/", flush=True)
        if not args.no_browser:
            webbrowser.open(url)

    thread = threading.Thread(target=ready, name="sozvon-ready", daemon=True)
    thread.start()
    try:
        server.run(sockets=[sock])
    except KeyboardInterrupt:
        print("Приложение завершено.", flush=True)
    finally:
        service = app.state.service
        service.close()
        sock.close()
        runtime.unlink(missing_ok=True)
        instance.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
