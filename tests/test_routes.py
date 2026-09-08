import pytest


@pytest.mark.parametrize(
    "path", ["/", "/movies", "/tv", "/exports", "/settings", "/servers", "/healthz"]
)
def test_pages_render(client, path):
    assert client.get(path).status_code == 200


@pytest.mark.parametrize("path", ["/movies?view=list", "/movies?view=grid&page=2", "/tv?view=grid"])
def test_view_param_pages_are_full_html(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower()
    assert "/static/app.css" in r.text


def test_results_partial_when_htmx(client):
    r = client.get("/movies?view=list", headers={"HX-Request": "true"})
    assert r.status_code == 200
    assert "<!doctype html>" not in r.text.lower()


@pytest.mark.parametrize("path", ["/movies", "/tv"])
def test_empty_filter_params_are_accepted(client, path):
    # "All" <option value=""> submits server=/category=; blank page/page_size
    # from a hand-edited URL must not 422 either.
    r = client.get(f"{path}?q=matrix&server=&category=&sort=name&page=&page_size=")
    assert r.status_code == 200
    r2 = client.get(
        f"{path}?q=matrix&server=&page_size=50",
        headers={"HX-Request": "true"},
    )
    assert r2.status_code == 200


def test_unknown_page_is_404(client):
    assert client.get("/does-not-exist").status_code == 404


def test_servers_page_redirects_to_settings(client):
    r = client.get("/servers", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("/settings")


def test_add_server_then_listed_on_settings(client):
    r = client.post(
        "/servers",
        data={
            "name": "Box", "scheme": "http", "host": "example.invalid",
            "port": "8080", "username": "u", "password": "p", "is_active": "true",
        },
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Box" in r.text
    assert "Settings" in r.text  # landed on the settings page


def test_probe_route_not_shadowed_by_id(client):
    r = client.post(
        "/servers/probe",
        data={"scheme": "http", "host": "127.0.0.1", "port": "1", "username": "u", "password": "p"},
    )
    assert r.status_code == 200
    assert "✗" in r.text


def test_export_rejects_bad_tokens(client):
    assert client.post("/export", json={"selections": ["garbage"]}).status_code == 422


def test_settings_save_persists(client):
    r = client.post(
        "/settings",
        data={"movies_dir": "Films", "tv_dir": "Series", "sync_interval_hours": "6"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    from app.settings_store import effective

    eff = effective()
    assert eff.movies_dir == "Films"
    assert eff.tv_dir == "Series"
    assert eff.sync_interval_hours == 6


def test_tmdb_test_without_key(client):
    r = client.post("/settings/tmdb/test", data={"tmdb_api_key": "", "tmdb_language": "en-US"})
    assert r.status_code == 200
    assert "API key" in r.text


def test_sync_interval_reschedules_live(client):
    from app import scheduler

    assert scheduler._scheduler is not None  # started by lifespan

    r = client.post("/settings", data={"sync_interval_hours": "3"}, follow_redirects=True)
    assert r.status_code == 200
    job = scheduler._scheduler.get_job("catalog-sync")
    assert job is not None

    client.post("/settings", data={"sync_interval_hours": "0"})
    assert scheduler._scheduler.get_job("catalog-sync") is None
