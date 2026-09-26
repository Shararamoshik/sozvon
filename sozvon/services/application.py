"""Прикладные сценарии. Долгие операции исполняются отдельным процессом."""
from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import urlsplit

from sozvon.audio.paths import local_path
from sozvon.config import SettingsStore
from sozvon.storage.repository import Repository


class BusyError(ValueError):
    pass


class Application:
    def __init__(self, root: Path, worker_factory=None):
        self.root = Path(root).resolve()
        self.settings = SettingsStore(self.root)
        self.repo = Repository(self.root)
        self.repo.recover()
        self._factory = worker_factory
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._current: str | None = None
        self._closed = False

    def worker(self):
        if self._factory is not None:
            return self._factory()
        from sozvon.runtime.host import WorkerHost
        return WorkerHost()

    def active(self, mid: str | None = None) -> dict | None:
        with self._lock:
            job = self.repo.job(self._current) if self._current else None
            if mid and job and job["meeting_id"] != mid:
                return None
            return job

    def _submit(self, mid: str, operation: str, payload: dict, timeout: float,
                *, transcript_revision: int | None = None) -> str:
        with self._lock:
            if self._closed:
                raise BusyError("Приложение завершается")
            if self._thread and self._thread.is_alive():
                raise BusyError("Другая операция уже идёт. Дождитесь окончания или остановите её")
            snapshot = {k: v for k, v in payload.items() if k not in {"api_key", "segments"}}
            jid = self.repo.add_job(mid, operation, snapshot)
            self._current = jid
            self._stop = threading.Event()
            self.repo.status(mid, "recording" if operation == "record" else "processing")
            thread = threading.Thread(target=self._work,
                                      args=(jid, mid, operation, payload, timeout, self._stop, transcript_revision),
                                      name=f"sozvon-{operation}", daemon=False)
            self._thread = thread
            thread.start()
            return jid

    def _work(self, jid: str, mid: str, operation: str, payload: dict,
              timeout: float, stop: threading.Event, transcript_revision: int | None):
        self.repo.job_update(jid, "running", stage="Подготовка")

        def event(item: dict):
            if item.get("type") in {"progress", "levels"}:
                self.repo.job_update(jid, "cancelling" if stop.is_set() else "running",
                                     stage=str(item.get("stage", "Выполняется")), progress=item)

        try:
            result = self.worker().run(operation, payload, timeout=timeout,
                                       stop_event=stop, on_event=event)
            # Проверка и публикация разделены с stop одним замком: поздний ответ не побеждает отмену.
            with self._lock:
                if stop.is_set() and operation != "record":
                    self.repo.job_update(jid, "cancelled", stage="Отменено")
                    self.repo.status(mid, "ready")
                    return
                if operation == "report":
                    self.repo.save_report(mid, result, transcript_revision=transcript_revision)
                elif operation == "transcribe":
                    self.repo.save_segments(mid, result["segments"])
                elif operation == "record":
                    for track in result["tracks"]:
                        self.repo.attach_source(mid, Path(track["path"]), track["source"],
                                                None)
                    self.repo.set_duration(mid, int(result.get("duration_ms") or
                        max(track["duration_ms"] for track in result["tracks"])))
                warnings = result.get("warnings", [])
                warning = "; ".join(str(item) for item in warnings)[:500] if warnings else None
                self.repo.status(mid, "partial" if warning else "ready", warning)
                self.repo.job_update(jid, "succeeded", stage="Сохранено с замечаниями" if warning else "Сохранено")
        except Exception as exc:  # noqa: BLE001 - граница отдельного задания
            cancelled = stop.is_set() and operation != "record"
            message = "Обработка отменена" if cancelled else str(exc)
            secret = payload.get("api_key")
            if secret:
                message = message.replace(secret, "[ключ скрыт]")
            self.repo.job_update(jid, "cancelled" if cancelled else "failed",
                                 stage="Отменено" if cancelled else "Ошибка", error=message[:500])
            self.repo.status(mid, "ready" if cancelled else "error", None if cancelled else message[:500])

    def _ensure_idle(self):
        if self._closed or (self._thread and self._thread.is_alive()):
            raise BusyError("Другая операция уже идёт. Дождитесь окончания или остановите её")

    def save_settings(self, candidate: dict) -> dict:
        with self._lock:
            self._ensure_idle()
            return self.settings.save(candidate)

    def report(self, mid: str) -> str:
        with self._lock:
            self._ensure_idle()
            return self._report(mid)

    def _report(self, mid: str) -> str:
        detail = self.repo.detail(mid)
        if not detail["segments"]:
            raise ValueError("Сначала добавьте текст или распознайте аудио")
        cfg, secret = self.settings.report_snapshot()
        llm = cfg["llm"]
        remote = urlsplit(llm["base_url"]).hostname not in {"127.0.0.1", "localhost", "::1"}
        if remote and not llm["allow_remote"]:
            raise ValueError("Разрешите внешнюю обработку в настройках; текст пока никуда не отправлен")
        if not llm["model"].strip():
            raise ValueError("Укажите модель отчёта в настройках")
        secret = secret or ""
        if remote and not secret:
            raise ValueError("Сохраните API-ключ в настройках отчёта")
        payload = {"base_url": llm["base_url"], "protocol": llm["protocol"], "model": llm["model"],
                   "api_key": secret, "timeout": 120, "segments": detail["segments"],
                   "title": detail["title"], "template": cfg["template"], "language": "ru"}
        return self._submit(mid, "report", payload, 135,
                            transcript_revision=detail["transcript_revision"])

    def transcribe(self, mid: str) -> str:
        with self._lock:
            self._ensure_idle()
            return self._transcribe(mid)

    def _transcribe(self, mid: str) -> str:
        detail = self.repo.detail(mid)
        if not detail["sources"]:
            raise ValueError("У этой записи нет аудиофайла")
        cfg = self.settings.snapshot()["stt"]
        if not cfg["model_path"] or not local_path(cfg["model_path"]).is_dir():
            raise ValueError("Укажите папку установленной модели распознавания")
        paths = [str(self.repo.source_path(s["id"])) for s in detail["sources"]]
        output = self.root / "processing" / mid
        output.mkdir(parents=True, exist_ok=True)
        payload = {**cfg, "path": paths[0], "paths": paths, "compute_type": "int8",
                   "output_dir": str(output)}
        return self._submit(mid, "transcribe", payload, 14400)

    def start_recording(self, allow_partial: bool, max_seconds: int) -> dict:
        with self._lock:
            if self._thread and self._thread.is_alive():
                raise BusyError("Другая операция уже идёт")
            mid = self.repo.create("Новая запись", "recording")
            folder = self.root / "recordings" / mid
            folder.mkdir(parents=True, exist_ok=True)
            payload = {**self.settings.snapshot()["recording"], "output_dir": str(folder),
                       "allow_partial": allow_partial, "max_seconds": max_seconds}
            jid = self._submit(mid, "record", payload, max_seconds + 30)
            return {"id": mid, "job_id": jid}

    def stop(self, jid: str):
        with self._lock:
            if jid != self._current:
                raise ValueError("Это задание уже не выполняется")
            job = self.repo.job(jid)
            if job["status"] in {"queued", "running", "cancelling"}:
                self._stop.set()
                self.repo.job_update(jid, "cancelling", stage="Завершение")

    def wait(self, seconds: float):
        thread = self._thread
        if thread:
            thread.join(seconds)
        return not thread or not thread.is_alive()

    def close(self):
        with self._lock:
            self._closed = True
            self._stop.set()
        self.wait(20)
