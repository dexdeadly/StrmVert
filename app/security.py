"""Authentication.

Modes (first match wins):

* ``env``   — ``APP_PASSWORD`` is set: single password, no username. Managed
              entirely via the env var (no setup wizard, no in-app change).
* ``open``  — the setup wizard was skipped ("run without a login"). No auth.
* ``user``  — an :class:`AdminUser` row exists: username + password login,
              changeable on the Settings page.
* ``setup`` — none of the above: first run. Everything redirects to ``/setup``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Depends, HTTPException, Request, status
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import AdminUser, Setting

SESSION_COOKIE = "strmvert_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days
_AUTH_OPEN_KEY = "auth_open"
_PUBLIC_PREFIXES = ("/static", "/healthz", "/login", "/logout", "/setup")

MIN_PASSWORD_LEN = 8


# ---------------------------------------------------------------------------
# password hashing (stdlib scrypt)
# ---------------------------------------------------------------------------

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password_hash(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.scrypt(
            password.encode("utf-8"), salt=salt,
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk, expected)


# ---------------------------------------------------------------------------
# session cookie
# ---------------------------------------------------------------------------


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().secret_key, salt="strmvert-session")


def issue_session(username: str | None = None) -> str:
    return _serializer().dumps({"ok": True, "u": username})


def read_session(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        data = _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) and data.get("ok") else None


def session_valid(token: str | None) -> bool:
    return read_session(token) is not None


# ---------------------------------------------------------------------------
# admin account
# ---------------------------------------------------------------------------


def get_admin(session: Session) -> AdminUser | None:
    return session.scalars(select(AdminUser).limit(1)).first()


def admin_exists(session: Session) -> bool:
    return (session.scalar(select(func.count()).select_from(AdminUser)) or 0) > 0


def create_admin(session: Session, username: str, password: str) -> AdminUser:
    admin = AdminUser(username=username.strip(), password_hash=hash_password(password))
    session.add(admin)
    session.commit()
    return admin


def update_admin(
    session: Session, admin: AdminUser, *, username: str | None = None, password: str | None = None
) -> None:
    if username:
        admin.username = username.strip()
    if password:
        admin.password_hash = hash_password(password)
    session.commit()


def _auth_open(session: Session) -> bool:
    row = session.get(Setting, _AUTH_OPEN_KEY)
    return bool(row and row.value.strip().lower() in ("1", "true", "yes", "on"))


def set_auth_open(session: Session, value: bool) -> None:
    row = session.get(Setting, _AUTH_OPEN_KEY)
    if row is None:
        session.add(Setting(key=_AUTH_OPEN_KEY, value="true" if value else "false"))
    else:
        row.value = "true" if value else "false"
    session.commit()


# ---------------------------------------------------------------------------
# mode resolution
# ---------------------------------------------------------------------------


@dataclass
class AuthInfo:
    mode: str  # env | open | user | setup
    authenticated: bool
    username: str | None = None

    @property
    def login_active(self) -> bool:
        return self.mode in ("env", "user")


def password_login_ok(session: Session, username: str, password: str) -> bool:
    """Check a login attempt against the active credential source."""
    env_pw = get_settings().app_password
    if env_pw:
        return hmac.compare_digest(password.encode(), env_pw.encode())
    admin = get_admin(session)
    if admin is None:
        return False
    return admin.username == username.strip() and verify_password_hash(
        password, admin.password_hash
    )


# backward-compatible name used by the env-only path / tests
def verify_password(candidate: str) -> bool:
    expected = get_settings().app_password
    return bool(expected) and hmac.compare_digest(candidate.encode(), expected.encode())


def auth_mode(session: Session) -> str:
    if get_settings().app_password:
        return "env"
    if admin_exists(session):
        return "user"
    if _auth_open(session):
        return "open"
    return "setup"


def resolve_auth(session: Session, request: Request) -> AuthInfo:
    mode = auth_mode(session)
    if mode == "open":
        return AuthInfo("open", authenticated=True)
    if mode == "setup":
        return AuthInfo("setup", authenticated=False)
    authed = read_session(request.cookies.get(SESSION_COOKIE)) is not None
    username = None
    if mode == "user":
        admin = get_admin(session)
        username = admin.username if admin else None
    return AuthInfo(mode, authenticated=authed, username=username)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def require_auth(request: Request, session: Session = Depends(get_session)) -> None:
    info = resolve_auth(session, request)
    request.state.auth = info

    if info.mode in ("open", "env", "user") and (info.mode == "open" or info.authenticated):
        return

    if info.mode == "setup":
        target = "/setup"
    else:
        nxt = request.url.path or "/"
        target = f"/login?next={quote(nxt, safe='')}"

    if request.headers.get("HX-Request") == "true":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers={"HX-Redirect": target})
    raise HTTPException(status.HTTP_303_SEE_OTHER, headers={"Location": target})
