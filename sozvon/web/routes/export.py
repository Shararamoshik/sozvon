"""Export endpoints live behind the application session/Origin middleware."""

import asyncio
from threading import Event
from typing import Literal

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from sozvon.core.errors import ResourceConflict
from sozvon.export.service import ExportTooLarge, export_meeting
from sozvon.runtime.protocol import WorkerError


def create_router(service) -> APIRouter:
    router = APIRouter()

    @router.get("/api/meetings/{mid}/export")
    async def export(request: Request, mid: str, format: Literal["md", "json", "docx", "pdf"] = "md",
                     content: Literal["auto", "report", "transcript"] = "auto",
                     include_transcript: Literal["0", "1"] = "0",
                     include_quotes: Literal["0", "1"] = "1", report_id: str | None = None):
        try:
            data, media, filename = await _run_export(request, service, mid, {
                "format": format, "content": content,
                "include_transcript": include_transcript == "1",
                "include_quotes": include_quotes == "1", "report_id": report_id,
            })
        except WorkerError as exc:
            raise HTTPException(409 if exc.code == "cancelled" else 422, str(exc)[:500]) from None
        except ExportTooLarge as exc:
            raise HTTPException(413, str(exc)) from None
        except ResourceConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return Response(data, media_type=media, headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    return router


async def _run_export(request, service, mid, options):
    """Never confuse cancelling an await with stopping its underlying thread/process."""
    stop = Event()
    work = asyncio.create_task(asyncio.to_thread(export_meeting, service, mid, options,
                                                cancel_event=stop))

    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    watcher = asyncio.create_task(disconnected())
    try:
        done, _ = await asyncio.wait({work, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done:
            stop.set()
            raise HTTPException(409, "Экспорт отменён: соединение закрыто")
        return await asyncio.shield(work)
    finally:
        stop.set()
        watcher.cancel()
        # Keep ownership until WorkerHost has reaped the process and the service
        # has removed the job directory / released its lock. Shield both AnyIO
        # level cancellation (middleware) and asyncio task cancellation.
        with anyio.CancelScope(shield=True):
            settled = asyncio.gather(work, watcher, return_exceptions=True)
            while not settled.done():
                try:
                    await asyncio.shield(settled)
                except asyncio.CancelledError:
                    continue
