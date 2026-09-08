"""Shared Jinja2 environment + a small render helper."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app import __version__
from app.config import get_settings

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _fmt_dt(value, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return value.strftime(fmt) if value else "—"


def _rel_time(value) -> str:
    if not value:
        return "never"
    from datetime import UTC, datetime

    delta = datetime.now(UTC).replace(tzinfo=None) - value
    secs = int(delta.total_seconds())
    if secs < 0:
        return _fmt_dt(value)
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= size:
            return f"{secs // size}{unit} ago"
    return "just now"


templates.env.filters["fmt_dt"] = _fmt_dt
templates.env.filters["rel_time"] = _rel_time


def render(
    request: Request, name: str, *, status_code: int = 200, **context: Any
):
    settings = get_settings()
    auth = getattr(request.state, "auth", None)
    payload: dict[str, Any] = {
        "request": request,
        "auth_enabled": auth.login_active if auth else bool(settings.app_password),
        "current_user": auth.username if auth else None,
        "app_version": __version__,
        "settings": settings,
        "active": None,
    }
    payload.update(context)
    return templates.TemplateResponse(
        request=request, name=name, context=payload, status_code=status_code
    )
