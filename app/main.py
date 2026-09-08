"""StrmVert application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import __version__, scheduler
from app.config import get_settings
from app.db import init_db
from app.routers import auth, exports, health, library, servers
from app.routers import settings as settings_router
from app.templating import render

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("strmvert")

_STATIC_DIR = Path(__file__).parent / "static"
_REDIRECT_CODES = {301, 302, 303, 307, 308}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    settings.validate_runtime()
    init_db()
    log.info("StrmVert %s ready — media root %s, auth %s",
             __version__, settings.media_root, "on" if settings.auth_enabled else "off")

    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="StrmVert", version=__version__, lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(settings_router.router)
    app.include_router(servers.router)
    app.include_router(exports.router)
    app.include_router(library.router)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException):
        headers = dict(exc.headers or {})
        # Auth dependency raises redirects / HX-Redirect — honour them with an empty body.
        if exc.status_code in _REDIRECT_CODES and "location" in {k.lower() for k in headers}:
            return Response(status_code=exc.status_code, headers=headers)
        if exc.status_code == 401 and any(k.lower() == "hx-redirect" for k in headers):
            return Response(status_code=exc.status_code, headers=headers)
        if exc.status_code == 404 and request.headers.get("accept", "").startswith("text/html"):
            return render(request, "404.html", status_code=404)
        from fastapi.exception_handlers import http_exception_handler

        return await http_exception_handler(request, exc)

    return app


app = create_app()
