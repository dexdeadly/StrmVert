"""Test bootstrap — set env BEFORE any app import so the engine binds to a temp DB."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="strmvert-tests-"))
os.environ.setdefault("SECRET_KEY", "test-secret-key-0123456789abcdef")
os.environ.setdefault("APP_PASSWORD", "")
os.environ.setdefault("DEV", "1")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["MEDIA_ROOT"] = str(_TMP / "media")
os.environ["WRITE_NFO"] = "true"
os.environ["SYNC_INTERVAL_HOURS"] = "0"

import pytest  # noqa: E402

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()
get_settings()  # materialise; also creates DATA_DIR


@pytest.fixture()
def settings():
    return get_settings()


@pytest.fixture()
def SessionFactory():
    from app.db import Base, engine
    from app.db import SessionLocal as _SessionLocal
    from app.models import Setting

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    # Default the auth mode to "open" so tests don't hit the first-run wizard.
    # Tests that exercise setup/login clear this first.
    with _SessionLocal() as s:
        s.add(Setting(key="auth_open", value="true"))
        s.commit()
    return _SessionLocal


@pytest.fixture()
def session(SessionFactory):
    with SessionFactory() as s:
        yield s


@pytest.fixture()
def media_root(settings):
    import shutil

    root = settings.media_root
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture()
def client(SessionFactory):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
