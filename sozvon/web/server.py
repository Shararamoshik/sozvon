"""Локальный интерфейс с серверной сессией и проверкой Origin/CSRF."""
from __future__ import annotations

import json
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from sozvon import __version__
from sozvon.runtime.host import WorkerError
from sozvon.services.application import Application
from sozvon.storage.repository import uid


class TextInput(BaseModel):
    title: str = Field(default="", max_length=200)
    text: str = Field(min_length=1, max_length=500_000)


class NoteInput(BaseModel):
    text: str = Field(max_length=100_000)


class RecordInput(BaseModel):
    allow_partial: bool = False
    max_seconds: int = Field(default=3600, ge=1, le=14400)


def create_app(root: Path, *, worker_factory=None) -> FastAPI:
    service = Application(root, worker_factory=worker_factory)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await run_in_threadpool(service.close)

    app = FastAPI(title="Созвон", docs_url=None, redoc_url=None, openapi_url=None,
                  lifespan=lifespan)
    app.state.service = service
    app.state.launch_key = secrets.token_urlsafe(32)
    app.state.launch_deadline = time.monotonic() + 60
    sessions: dict[str, str] = {}
    web = Path(__file__).resolve().parent
    app.mount("/static", StaticFiles(directory=web / "static"), name="static")
    templates = Jinja2Templates(directory=web / "templates")

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "")
        parsed_host = urlsplit(f"http://{host}")
        if parsed_host.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"detail": "Недопустимый адрес приложения"}, status_code=400)
        path = request.url.path
        changing = request.method not in {"GET", "HEAD", "OPTIONS"}
        if changing and request.headers.get("origin") != f"{request.url.scheme}://{host}":
            return JSONResponse({"detail": "Запрос с другого сайта запрещён"}, status_code=403)
        protected = path.startswith("/api/") and path not in {"/api/health", "/api/session"}
        if protected:
            session = request.cookies.get("sozvon_session", "")
            if session not in sessions:
                return JSONResponse({"detail": "Откройте приложение через ярлык"}, status_code=401)
            if changing and not secrets.compare_digest(
                    request.headers.get("x-csrf-token", ""), sessions[session]):
                return JSONResponse({"detail": "Недействительный токен формы"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; font-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; media-src 'self' blob:; "
            "frame-ancestors 'none'; object-src 'none'; base-uri 'self'"
        )
        if path.startswith("/api/") or path == "/":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(WorkerError)
    async def worker_error(request, exc):
        return JSONResponse({"detail": str(exc)[:500]}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse({"detail": str(exc)[:500]}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Запись не найдена"}, status_code=404)

    @app.exception_handler(FileNotFoundError)
    async def missing_file(request, exc):
        return JSONResponse({"detail": "Исходный файл недоступен"}, status_code=404)

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request, "index.html", {"version": __version__})

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__}

    @app.post("/api/session")
    async def session_start(request: Request):
        body = await request.json()
        key = body.get("key") if isinstance(body, dict) else None
        if (not isinstance(key, str) or not app.state.launch_key
                or time.monotonic() > app.state.launch_deadline
                or not secrets.compare_digest(key, app.state.launch_key)):
            raise HTTPException(401, "Ссылка запуска недействительна. Перезапустите приложение")
        app.state.launch_key = ""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        sessions[token] = csrf
        response = JSONResponse({"csrf": csrf})
        response.set_cookie("sozvon_session", token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/session")
    def session_get(request: Request):
        token = request.cookies.get("sozvon_session", "")
        if token not in sessions:
            raise HTTPException(401, "Откройте приложение через ярлык")
        return {"csrf": sessions[token]}

    @app.get("/api/meetings")
    def meetings():
        return {"items": service.repo.list(), "job": service.active()}

    @app.post("/api/import/text")
    def import_text(body: TextInput):
        return {"id": service.repo.create_text(body.title, body.text)}

    @app.post("/api/import/audio")
    async def import_audio(file: UploadFile):
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            raise HTTPException(422, "Поддерживаются WAV, MP3, M4A, FLAC и OGG")
        folder = service.root / "imports" / uid()
        folder.mkdir(parents=True)
        target = folder / f"source{extension}"
        size = 0
        try:
            with target.open("xb") as handle:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > 256 * 1024 * 1024:
                        raise HTTPException(413, "В технической версии лимит файла — 256 МБ")
                    await run_in_threadpool(handle.write, chunk)
            if not size:
                raise HTTPException(422, "Файл пуст")
            info = await run_in_threadpool(service.worker().run, "audio_info", {"path": str(target)},
                                          timeout=60)
            mid = service.repo.create(Path(file.filename or "Аудио").stem, "audio")
            service.repo.attach_source(mid, target, "audio", info["duration_ms"])
            return {"id": mid}
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @app.get("/api/meetings/{mid}")
    def detail(mid: str):
        result = service.repo.detail(mid)
        result["job"] = service.active(mid)
        return result

    @app.put("/api/meetings/{mid}/notes")
    def notes(mid: str, body: NoteInput):
        service.repo.save_notes(mid, body.text)
        return {"saved": True}

    @app.post("/api/meetings/{mid}/report", status_code=202)
    def report(mid: str):
        return {"job_id": service.report(mid)}

    @app.post("/api/meetings/{mid}/transcribe", status_code=202)
    def transcribe(mid: str):
        return {"job_id": service.transcribe(mid)}

    @app.get("/api/settings")
    def settings():
        return service.settings.public()

    @app.put("/api/settings")
    def settings_save(body: dict):
        return service.save_settings(body)

    @app.get("/api/devices")
    def devices():
        return service.worker().run("devices", {}, timeout=20)

    @app.post("/api/record/start", status_code=202)
    def record_start(body: RecordInput):
        return service.start_recording(body.allow_partial, body.max_seconds)

    @app.post("/api/jobs/{jid}/stop")
    def stop(jid: str):
        service.stop(jid)
        return {"accepted": True}

    @app.get("/api/sources/{sid}")
    def source(sid: str):
        return FileResponse(service.repo.source_path(sid))

    @app.get("/api/meetings/{mid}/export")
    def export(mid: str, format: str = "md"):
        item = service.repo.detail(mid)
        if format == "json":
            item["transcript_history"] = service.repo.transcript_history(mid)
            content = json.dumps(item, ensure_ascii=False, indent=2)
            media = "application/json"
        elif format == "md":
            if item["reports"] and item["reports"][0]["stale"]:
                raise HTTPException(409, "Отчёт устарел после изменения расшифровки. Создайте новую версию; JSON сохраняет историю.")
            lines = [f"# {item['title']}", ""]
            if item["reports"]:
                document = item["reports"][0]["document"]
                labels = {"summary": "Кратко", "decisions": "Решения", "proposals": "Предложения",
                          "tasks": "Задачи", "questions": "Открытые вопросы", "risks": "Риски"}
                for key, label in labels.items():
                    lines.extend([f"## {label}", ""])
                    for point in document.get(key, []):
                        lines.append(f"- {point['text']}")
                        if key == "tasks":
                            lines.append(f"  Исполнитель: {point.get('owner') or 'Не назначен'}; "
                                         f"срок: {point.get('due') or 'Не указан'}")
                        for evidence in point.get("evidence", []):
                            lines.append(f"  > {evidence['quote']} [фрагмент {evidence['segment_id']}]")
                    lines.append("")
            else:
                lines.extend(["## Расшифровка", ""])
                lines.extend(s["text"] for s in item["segments"])
            content = "\n".join(lines)
            media = "text/markdown"
        else:
            raise HTTPException(400, "Этот формат ещё не поддерживается")
        return Response(content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="sozvon-{mid}.{format}"'})

    return app
