"""First-run setup wizard + login / logout."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import security
from app.db import get_session
from app.templating import render

router = APIRouter()


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else "/"


def _login(username: str | None) -> RedirectResponse:
    resp = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(
        security.SESSION_COOKIE,
        security.issue_session(username),
        max_age=security.SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
    )
    return resp


# ---------------------------------------------------------------------------
# setup wizard
# ---------------------------------------------------------------------------


@router.get("/setup", include_in_schema=False)
async def setup_form(request: Request, session: Session = Depends(get_session)):
    if security.auth_mode(session) != "setup":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(
        request, "setup.html", error=None, values={"username": ""}, auth_enabled=False
    )


@router.post("/setup", include_in_schema=False)
async def setup_submit(
    request: Request,
    session: Session = Depends(get_session),
    action: str = Form("create"),
    username: str = Form(""),
    password: str = Form(""),
    confirm: str = Form(""),
):
    mode = security.auth_mode(session)
    if action == "enable" and mode == "open":
        security.set_auth_open(session, False)  # open -> setup, then show the form
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    if mode != "setup":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    if action == "skip":
        security.set_auth_open(session, True)
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    username = username.strip()
    error = None
    if len(username) < 3:
        error = "Username must be at least 3 characters."
    elif len(password) < security.MIN_PASSWORD_LEN:
        error = f"Password must be at least {security.MIN_PASSWORD_LEN} characters."
    elif password != confirm:
        error = "Passwords don't match."
    if error:
        return render(
            request, "setup.html", error=error, values={"username": username},
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    security.create_admin(session, username, password)
    return _login(username)


# ---------------------------------------------------------------------------
# login / logout
# ---------------------------------------------------------------------------


@router.get("/login", include_in_schema=False)
async def login_form(
    request: Request, session: Session = Depends(get_session), next: str = "/"
):
    mode = security.auth_mode(session)
    if mode == "setup":
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    if mode == "open":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(
        request, "login.html", auth_enabled=False,
        error=None, next=_safe_next(next), needs_username=(mode == "user"),
    )


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    session: Session = Depends(get_session),
    username: str = Form(""),
    password: str = Form(...),
    next: str = Form("/"),
):
    mode = security.auth_mode(session)
    if mode == "setup":
        return RedirectResponse("/setup", status_code=status.HTTP_303_SEE_OTHER)
    if mode == "open":
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)

    if not security.password_login_ok(session, username, password):
        return render(
            request, "login.html", auth_enabled=False,
            error="Incorrect username or password." if mode == "user" else "Incorrect password.",
            next=_safe_next(next), needs_username=(mode == "user"),
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    resp = _login(username or None)
    resp.headers["Location"] = _safe_next(next)
    return resp


@router.post("/logout", include_in_schema=False)
async def logout():
    resp = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie(security.SESSION_COOKIE)
    return resp
