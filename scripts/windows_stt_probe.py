"""Диагностика настоящего CPU STT без сети и пользовательских записей."""
import faulthandler
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
out = ROOT / "artifacts" / "windows-stt.log"
out.parent.mkdir(parents=True, exist_ok=True)
with out.open("w", encoding="utf-8") as f:
    faulthandler.enable(f)
    faulthandler.dump_traceback_later(30, repeat=True, file=f)
    def log(x):
        f.write(str(x) + "\n")
        f.flush()
    os.environ["HF_HUB_OFFLINE"] = "1"
    start = time.monotonic()
    log("import faster_whisper")
    from faster_whisper import WhisperModel
    log("construct model")
    model = WhisperModel(str(ROOT / "artifacts/models/tiny"), device="cpu", compute_type="int8",
                         cpu_threads=4, local_files_only=True)
    log("transcribe fixture")
    segments, info = model.transcribe(str(ROOT / "tests/fixtures/hello_ru.wav"), language="ru", vad_filter=True)
    for s in segments:
        log(json.dumps({"start":s.start,"text":s.text}, ensure_ascii=False))
    log(f"elapsed={time.monotonic()-start}")
    faulthandler.cancel_dump_traceback_later()
print("REPORT=" + str(out))
