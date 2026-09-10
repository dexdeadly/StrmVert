"""The `python -m app reset-password` recovery command."""

from fastapi.testclient import TestClient
from sqlalchemy import delete

from app import cli, security


def _clear_auth_open(SessionFactory):
    from app.models import Setting

    with SessionFactory() as s:
        s.execute(delete(Setting).where(Setting.key == "auth_open"))
        s.commit()


def test_reset_creates_admin_when_none_exists(SessionFactory, capsys):
    rc = cli.main(["reset-password", "--password", "freshpass1"])
    assert rc == 0
    assert "created 'admin'" in capsys.readouterr().out

    with SessionFactory() as s:
        admin = security.get_admin(s)
        assert admin is not None and admin.username == "admin"
        assert security.verify_password_hash("freshpass1", admin.password_hash)


def test_reset_changes_existing_password_and_username(SessionFactory):
    with SessionFactory() as s:
        security.create_admin(s, "olduser", "origpass1")

    rc = cli.main(["reset-password", "--username", "newuser", "--password", "brandnew9"])
    assert rc == 0

    with SessionFactory() as s:
        admin = security.get_admin(s)
        assert admin.username == "newuser"
        assert not security.verify_password_hash("origpass1", admin.password_hash)
        assert security.verify_password_hash("brandnew9", admin.password_hash)


def test_reset_rejects_short_password(SessionFactory):
    with SessionFactory() as s:
        security.create_admin(s, "admin", "origpass1")

    rc = cli.main(["reset-password", "--password", "short"])
    assert rc == 2

    with SessionFactory() as s:
        admin = security.get_admin(s)
        assert security.verify_password_hash("origpass1", admin.password_hash)


def test_reset_refused_in_env_password_mode(monkeypatch, SessionFactory, capsys):
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        rc = cli.main(["reset-password", "--password", "whatever12"])
        assert rc == 2
        assert "APP_PASSWORD" in capsys.readouterr().err
    finally:
        get_settings.cache_clear()


def test_reset_then_login_end_to_end(SessionFactory):
    _clear_auth_open(SessionFactory)
    from app.main import app

    with TestClient(app) as c:
        c.post(
            "/setup",
            data={
                "action": "create", "username": "admin",
                "password": "origpass1", "confirm": "origpass1",
            },
        )
        c.post("/logout")

        assert cli.main(["reset-password", "--password", "recovered1"]) == 0

        assert c.post(
            "/login", data={"username": "admin", "password": "origpass1"},
            follow_redirects=False,
        ).status_code == 401
        good = c.post(
            "/login", data={"username": "admin", "password": "recovered1"},
            follow_redirects=False,
        )
        assert good.status_code == 303
        assert c.get("/settings", follow_redirects=False).status_code == 200
