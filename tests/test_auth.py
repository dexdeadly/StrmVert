from fastapi.testclient import TestClient
from sqlalchemy import delete


def _clear_auth_open(SessionFactory):
    from app.models import Setting

    with SessionFactory() as s:
        s.execute(delete(Setting).where(Setting.key == "auth_open"))
        s.commit()


def test_open_app_when_skipped(client):
    assert client.get("/movies").status_code == 200
    assert client.get("/healthz").json() == {"status": "ok"}


def test_env_password_gate(monkeypatch, SessionFactory):
    monkeypatch.setenv("APP_PASSWORD", "hunter2")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        from app.main import app

        with TestClient(app) as c:
            blocked = c.get("/movies", follow_redirects=False)
            assert blocked.status_code == 303
            assert blocked.headers["location"].startswith("/login")

            bad = c.post("/login", data={"password": "wrong"}, follow_redirects=False)
            assert bad.status_code == 401

            good = c.post("/login", data={"password": "hunter2"}, follow_redirects=False)
            assert good.status_code == 303
            assert "strmvert_session" in good.headers.get("set-cookie", "")
            assert c.get("/movies", follow_redirects=False).status_code == 200
    finally:
        get_settings.cache_clear()


def test_first_run_setup_then_login(SessionFactory):
    _clear_auth_open(SessionFactory)
    from app.main import app

    with TestClient(app) as c:
        r = c.get("/movies", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/setup"

        # too-short password is rejected
        bad = c.post(
            "/setup",
            data={"action": "create", "username": "admin", "password": "short", "confirm": "short"},
        )
        assert bad.status_code == 400

        ok = c.post(
            "/setup",
            data={
                "action": "create", "username": "admin",
                "password": "hunter2!!", "confirm": "hunter2!!",
            },
            follow_redirects=False,
        )
        assert ok.status_code == 303
        assert "strmvert_session" in ok.headers.get("set-cookie", "")
        assert c.get("/movies", follow_redirects=False).status_code == 200

        # setup is done -> /setup bounces away
        assert c.get("/setup", follow_redirects=False).headers["location"] == "/"

        c.post("/logout")
        assert c.get("/movies", follow_redirects=False).status_code == 303
        assert (
            c.post("/login", data={"username": "admin", "password": "nope"}, follow_redirects=False)
            .status_code == 401
        )
        good = c.post(
            "/login", data={"username": "admin", "password": "hunter2!!"}, follow_redirects=False
        )
        assert good.status_code == 303
        assert c.get("/settings", follow_redirects=False).status_code == 200


def test_change_admin_password_in_settings(SessionFactory):
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
        wrong = c.post(
            "/settings/account",
            data={
                "current_password": "nope",
                "new_password": "newpass12", "confirm_password": "newpass12",
            },
            follow_redirects=False,
        )
        assert "error=" in wrong.headers["location"]

        right = c.post(
            "/settings/account",
            data={
                "current_password": "origpass1",
                "new_password": "newpass12", "confirm_password": "newpass12",
            },
            follow_redirects=False,
        )
        assert "note=" in right.headers["location"]

        c.post("/logout")
        old = c.post(
            "/login", data={"username": "admin", "password": "origpass1"}, follow_redirects=False
        )
        assert old.status_code == 401
        new = c.post(
            "/login", data={"username": "admin", "password": "newpass12"}, follow_redirects=False
        )
        assert new.status_code == 303


def test_skip_setup_leaves_app_open(SessionFactory):
    _clear_auth_open(SessionFactory)
    from app.main import app

    with TestClient(app) as c:
        assert c.get("/movies", follow_redirects=False).status_code == 303
        c.post("/setup", data={"action": "skip"}, follow_redirects=False)
        assert c.get("/movies", follow_redirects=False).status_code == 200
