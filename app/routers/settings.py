"""Settings page — library layout, sync, TMDB, and the XC Servers section."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import scheduler, security, settings_store
from app.config import get_settings
from app.db import get_session
from app.models import XCServer
from app.security import require_auth
from app.settings_store import effective as effective_settings
from app.templating import render
from app.tmdb import TMDBClient, TMDBError

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/settings", include_in_schema=False)
async def settings_page(
    request: Request,
    session: Session = Depends(get_session),
    note: str | None = None,
    error: str | None = None,
):
    servers = list(session.scalars(select(XCServer).order_by(XCServer.name)))
    base = get_settings()
    eff = effective_settings()
    mode = security.auth_mode(session)
    admin = security.get_admin(session)
    return render(
        request,
        "settings.html",
        active="settings",
        note=note,
        error=error,
        groups=settings_store.groups(),
        values=settings_store.current_values(),
        servers=servers,
        any_syncing=any(s.is_syncing for s in servers),
        account={"mode": mode, "username": admin.username if admin else None},
        env={
            "media_root": str(base.media_root),
            "data_dir": str(base.data_dir),
            "auth_mode": mode,
            "sync_interval": eff.sync_interval_hours,
            "tmdb_ready": bool(eff.tmdb_enabled and eff.tmdb_api_key),
        },
    )


@router.post("/settings", include_in_schema=False)
async def save_settings(request: Request):
    form = dict(await request.form())
    settings_store.save({k: str(v) for k, v in form.items()})
    hours = scheduler.apply_interval()  # take the new interval live, no restart
    schedule = "auto-sync off" if hours == 0 else f"auto-sync every {hours} h"
    return RedirectResponse(
        f"/settings?note={quote(f'Settings saved — {schedule}.')}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/settings/account", include_in_schema=False)
async def change_account(
    session: Session = Depends(get_session),
    current_password: str = Form(""),
    username: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    if security.auth_mode(session) != "user":
        return _settings_redirect(error="The admin account is not managed in-app.")
    admin = security.get_admin(session)
    if admin is None or not security.verify_password_hash(current_password, admin.password_hash):
        return _settings_redirect(error="Current password is incorrect.")

    new_username = username.strip() or admin.username
    if len(new_username) < 3:
        return _settings_redirect(error="Username must be at least 3 characters.")
    if new_password:
        if len(new_password) < security.MIN_PASSWORD_LEN:
            return _settings_redirect(
                error=f"New password must be at least {security.MIN_PASSWORD_LEN} characters."
            )
        if new_password != confirm_password:
            return _settings_redirect(error="New passwords don't match.")

    security.update_admin(
        session, admin,
        username=new_username if new_username != admin.username else None,
        password=new_password or None,
    )
    what = "password" if new_password and new_username == admin.username else "account"
    return _settings_redirect(note=f"Admin {what} updated.")


def _settings_redirect(*, note: str | None = None, error: str | None = None) -> RedirectResponse:
    q = f"note={quote(note)}" if note else f"error={quote(error)}"
    return RedirectResponse(f"/settings?{q}#account", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/settings/tmdb/test", include_in_schema=False)
async def tmdb_test(
    request: Request,
    tmdb_api_key: str = Form(""),
    tmdb_language: str = Form("en-US"),
):
    key = tmdb_api_key.strip()
    if not key:
        return render(
            request, "partials/tmdb_result.html",
            ok=False, message="Enter an API key or v4 token first.",
        )
    client = TMDBClient(key, language=tmdb_language or "en-US")
    try:
        await client.validate()
        sample = await client.find("movie", "The Matrix", 1999)
        found = sample.get("title") if sample else None
        msg = "Key works." + (f" Test lookup → “{found}”." if found else "")
        return render(request, "partials/tmdb_result.html", ok=True, message=msg)
    except TMDBError as exc:
        return render(request, "partials/tmdb_result.html", ok=False, message=str(exc))
    finally:
        await client.aclose()
