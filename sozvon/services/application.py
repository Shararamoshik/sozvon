"""Прикладные сценарии. Долгие операции исполняются отдельным процессом."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from sozvon.audio.paths import local_path
from sozvon.config import SettingsStore
from sozvon.core.errors import ResourceConflict, RevisionConflict
from sozvon.services.requests import ReportInput, TranscribeInput
from sozvon.shared.network import is_loopback_url, validated_base_url
from sozvon.storage.repository import Repository
from sozvon.storage.templates import get_template
from sozvon.storage.transcripts import save_recognition


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
        self._export_lock = threading.Lock()
        self._export_stop = threading.Event()

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
            if operation in {"transcribe", "transcribe_cloud"}:
                parent = self.root / "processing"
                if any(p.is_symlink() or p.is_junction() for p in (parent, *parent.parents)):
                    raise ValueError("Нужна локальная папка обработки без ссылок")
                parent.mkdir(parents=True, exist_ok=True)
                # Родитель владеет папкой: очистка работает и после kill зависшего worker.
                with TemporaryDirectory(prefix="job-", dir=parent) as directory:
                    processing = {**payload, "output_dir": directory}
                    result = self.worker().run(operation, processing, timeout=timeout,
                                               stop_event=stop, on_event=event)
            else:
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
                elif operation in {"transcribe", "transcribe_cloud"}:
                    secret = payload.get("api_key")
                    if secret and json.dumps(secret, ensure_ascii=False)[1:-1] in json.dumps(result, ensure_ascii=False):
                        raise ValueError("Ответ STT содержит конфиденциальные данные; расшифровка не сохранена")
                    save_recognition(self.repo, mid, payload["base_revision"], result["segments"],
                                     "cloud_stt" if operation == "transcribe_cloud" else "local_stt",
                                     {k: result[k] for k in ("model", "device", "warnings", "timed") if k in result})
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
            if "template" in candidate:
                if not isinstance(candidate["template"], str) or not 1 <= len(candidate["template"]) <= 64:
                    raise ValueError("Недопустимый идентификатор шаблона")
                template = get_template(self.repo, candidate["template"])
                if template["archived"]:
                    raise ResourceConflict("Выберите действующий шаблон")
            return self.settings.save(candidate)

    def report(self, mid: str, choice: ReportInput | None = None) -> str:
        with self._lock:
            self._ensure_idle()
            return self._report(mid, choice or ReportInput())

    def _report(self, mid: str, choice: ReportInput) -> str:
        detail = self.repo.detail(mid)
        if not detail["segments"]:
            raise ValueError("Сначала добавьте текст или распознайте аудио")
        cfg, secret = self.settings.report_snapshot()
        template = get_template(self.repo, choice.template_id or cfg["template"])
        if template["archived"]:
            raise ResourceConflict("Выберите действующий шаблон")
        if choice.template_revision is not None and choice.template_revision != template["revision"]:
            raise RevisionConflict(template["revision"])
        snapshot = {key: template[key] for key in ("id", "revision", "spec")}
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
                   "api_key": secret, "timeout": llm["timeout_s"],
                   "max_output_tokens": llm["max_output_tokens"], "segments": detail["segments"],
                   "title": detail["title"], "template": template["id"],
                   "template_snapshot": snapshot, "language": snapshot["spec"]["language"]}
        return self._submit(mid, "report", payload, llm["timeout_s"] + 15,
                            transcript_revision=detail["transcript_revision"])

    def transcribe(self, mid: str, request: TranscribeInput | None = None) -> str:
        with self._lock:
            self._ensure_idle()
            return self._transcribe(mid, request or TranscribeInput())

    def _transcribe(self, mid: str, request: TranscribeInput) -> str:
        detail = self.repo.detail(mid)
        if not detail["sources"]:
            raise ValueError("У этой записи нет аудиофайла")
        base = detail["transcript_revision"]
        if request.base_revision is not None and request.base_revision != base:
            raise RevisionConflict(base)
        if base and (request.base_revision is None or not request.replace_confirmed):
            raise ResourceConflict("Подтвердите создание новой версии расшифровки")
        config, secret = self.settings.stt_snapshot()
        cfg = config["stt"]
        if cfg["engine"] == "cloud":
            cloud = cfg["cloud"]
            endpoint = validated_base_url(cloud["base_url"]) + "/audio/transcriptions"
            if request.base_revision is None or request.cloud_confirmed_url != endpoint:
                raise ResourceConflict("Подтвердите отправку аудио на текущий адрес сервиса")
            if not is_loopback_url(cloud["base_url"]):
                if not cloud["allow_remote"]:
                    raise ResourceConflict("Разрешите отправку аудио во внешний сервис STT")
                if not secret:
                    raise ResourceConflict("Сохраните отдельный API-ключ распознавания")
            payload = {**cloud, "api_key": secret or "", "language": cfg["language"]}
            operation, timeout = "transcribe_cloud", cloud["timeout_s"] + 180
        else:
            if not cfg["model_path"] or not local_path(cfg["model_path"]).is_dir():
                raise ValueError("Укажите папку установленной модели распознавания")
            payload = {k: cfg[k] for k in ("model_path", "device", "language")}
            payload["compute_type"] = "int8"
            operation, timeout = "transcribe", 14400
        paths = [str(self.repo.source_path(s["id"])) for s in detail["sources"]]
        payload.update(path=paths[0], paths=paths, base_revision=base)
        return self._submit(mid, operation, payload, timeout)

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

    def request_close(self):
        """Только сигнал: HTTP-задачи должны получить его до ожидания Uvicorn."""
        with self._lock:
            self._closed = True
            self._stop.set()
            self._export_stop.set()

    def close(self):
        self.request_close()
        self.wait(20)
