"""Application factory for the ollama-queue FastAPI server."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from ollama_queue.db import Database
from ollama_queue.scanner import run_scan

_log = logging.getLogger(__name__)


def create_app(db: Database) -> FastAPI:
    """Application factory. Takes a Database instance for test injection."""
    from starlette.middleware.base import BaseHTTPMiddleware

    app = FastAPI(title="Ollama Queue")

    class _NoCacheSPA(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            if request.url.path.startswith("/ui"):
                response.headers["cache-control"] = "no-store"
            return response

    app.add_middleware(_NoCacheSPA)

    # Register all API routes via the api subpackage
    from ollama_queue.api import register_routes

    register_routes(app, db)

    # --- Static files for SPA ---
    spa_dir = Path(__file__).parent / "dashboard" / "spa" / "dist"
    if spa_dir.exists():
        _no_store = {"Cache-Control": "no-store"}

        @app.get("/ui/{path:path}")
        async def spa_static(path: str):
            if path and "\x00" in path:
                return HTMLResponse("Not found", status_code=404)
            real = (spa_dir / path).resolve() if path else None
            if real and real.is_file() and real.is_relative_to(spa_dir.resolve()):
                return FileResponse(real, headers=_no_store)
            index = spa_dir / "index.html"
            return (
                FileResponse(index, headers=_no_store)
                if index.is_file()
                else HTMLResponse("Not found", status_code=404)
            )

        app.mount("/ui", StaticFiles(directory=str(spa_dir), html=True), name="ui")

    # Auto-scan for Ollama consumers on startup (daemon thread — won't block shutdown)
    import threading as _threading

    def _startup_scan() -> None:
        try:
            run_scan(db)
        except Exception:
            _log.error("Startup consumer scan failed", exc_info=True)

    _threading.Thread(target=_startup_scan, daemon=True).start()

    return app
