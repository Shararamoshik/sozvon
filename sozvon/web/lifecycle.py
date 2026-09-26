"""Сигнал отмены до ожидания активных HTTP-запросов Uvicorn."""
import uvicorn
from starlette.concurrency import run_in_threadpool


class ApplicationServer(uvicorn.Server):
    async def shutdown(self, sockets=None):
        await run_in_threadpool(self.config.app.state.service.request_close)
        await super().shutdown(sockets=sockets)
