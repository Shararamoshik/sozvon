"""Короткая двухдорожечная проверка на Windows с синтетической речью."""
import json
import sys
import threading
import time
from pathlib import Path

import winsound

from sozvon.runtime.host import WorkerHost

ROOT = Path(__file__).resolve().parents[1]
exe = Path(sys.argv[sys.argv.index("--exe") + 1]).resolve() if "--exe" in sys.argv else ROOT / "dist/Sozvon/Sozvon.exe"
host_args = {"command": [str(exe), "--worker"]} if "--frozen" in sys.argv or "--exe" in sys.argv else {}
folder = ROOT / "artifacts" / (("frozen-capture-" if host_args else "capture-") + str(time.time_ns()))
folder.mkdir(parents=True)
events = []
started = threading.Event()
stop = threading.Event()


def event(data):
    events.append(data)
    if data.get("type") == "progress" and data.get("stage") == "Запись":
        started.set()


def play():
    if started.wait(15):
        winsound.PlaySound(str(ROOT / "tests/fixtures/hello_ru.wav"), winsound.SND_FILENAME)
        stop.set()


thread = threading.Thread(target=play)
thread.start()
try:
    result = WorkerHost(**host_args).run("record", {"output_dir": str(folder), "input_device": None,
        "output_device": None, "allow_partial": False, "max_seconds": 15},
        timeout=35, stop_event=stop, on_event=event)
    result["readback"] = [WorkerHost(**host_args).run("audio_info", {"path":t["path"]}, timeout=15)
                          for t in result["tracks"]]
    assert {t["source"] for t in result["tracks"]} == {"mic", "system"}
    system = next(t for t in result["tracks"] if t["source"] == "system")
    assert system["peak"] > .001, "No loopback signal"
    result["events"] = events
    (folder / "result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("CAPTURE_OK=" + str(folder))
finally:
    thread.join(20)
