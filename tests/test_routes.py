import pytest
from sqlalchemy import func, select


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


def _seed_series_with_exports(SessionFactory, media_root):
    from app.config import get_settings
    from app.crypto import encrypt
    from app.exporter import export_many
    from app.models import Episode, Series, XCServer

    with SessionFactory() as s:
        srv = XCServer(
            name="t", scheme="http", host="box.tv", port=8080,
            username="u", password_enc=encrypt("pw"),
        )
        s.add(srv)
        s.commit()
        series = Series(
            server_id=srv.id, xc_series_id="20", name="Cool Show",
            title_clean="Cool Show", year=2020,
        )
        s.add(series)
        s.commit()
        eps = [
            Episode(
                series_id=series.id, xc_episode_id=str(50 + n),
                season=1, episode=n, container_ext="mp4",
            )
            for n in (1, 2)
        ]
        s.add_all(eps)
        s.commit()
        export_many(s, [], eps, get_settings())
        return series.id


def _seed_two_server_series_with_exports(SessionFactory, media_root):
    """Same show synced from two servers, both with cached S01E01/E02;
    only the first server's episodes are exported."""
    from app.config import get_settings
    from app.exporter import export_many
    from app.models import Episode, Series

    srv1_id, srv2_id = _two_servers(SessionFactory)
    with SessionFactory() as s:
        series1 = Series(
            server_id=srv1_id, xc_series_id="20",
            name="Cool Show", title_clean="Cool Show", year=2020,
        )
        series2 = Series(
            server_id=srv2_id, xc_series_id="20",
            name="Cool Show", title_clean="Cool Show", year=2020,
        )
        s.add_all([series1, series2])
        s.commit()
        eps1 = [
            Episode(
                series_id=series1.id, xc_episode_id=f"a{n}",
                season=1, episode=n, container_ext="mp4",
            )
            for n in (1, 2)
        ]
        eps2 = [
            Episode(
                series_id=series2.id, xc_episode_id=f"b{n}",
                season=1, episode=n, container_ext="mp4",
            )
            for n in (1, 2)
        ]
        s.add_all(eps1 + eps2)
        s.commit()
        export_many(s, [], eps1, get_settings())
        return series1.id, series2.id


def _episode_at(session, series_id, season, episode):
    from app.models import Episode

    return session.scalar(
        select(Episode).where(
            Episode.series_id == series_id, Episode.season == season, Episode.episode == episode,
        )
    )


def test_delete_series_exports_removes_all_episodes(client, SessionFactory, media_root):
    series_id = _seed_series_with_exports(SessionFactory, media_root)

    from app.models import Export

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Export)) == 2

    r = client.post(f"/tv/{series_id}/delete-exports")
    assert r.status_code == 200
    assert "has strm" not in r.text

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Export)) == 0
    assert not (media_root / "TV Shows/Cool Show (2020)").exists()


def test_exports_bulk_delete(client, SessionFactory, media_root):
    _seed_series_with_exports(SessionFactory, media_root)

    from app.models import Export

    with SessionFactory() as s:
        ids = list(s.scalars(select(Export.id)))
    assert len(ids) == 2

    r = client.post(
        "/exports/bulk-delete",
        data={"ids": [str(i) for i in ids]},
        follow_redirects=True,
    )
    assert r.status_code == 200
    assert "Deleted 2 export(s)" in r.text


def test_manage_series_drawer_offers_sibling_source(client, SessionFactory, media_root):
    series1_id, _series2_id = _seed_two_server_series_with_exports(SessionFactory, media_root)

    r = client.get(f"/exports/series/{series1_id}/manage")
    assert r.status_code == 200
    assert "Apply to whole series" in r.text
    assert "Change" in r.text  # per-episode "Change" button


def test_retarget_episode_export_switches_server(client, SessionFactory, media_root):
    from app.models import Export

    _series1_id, series2_id = _seed_two_server_series_with_exports(SessionFactory, media_root)
    with SessionFactory() as s:
        export = s.scalar(select(Export).where(Export.title.contains("S01E01")))
        target_ep = _episode_at(s, series2_id, 1, 1)
        export_id, target_ep_id = export.id, target_ep.id

    r = client.post(f"/exports/{export_id}/retarget", json={"episode_id": target_ep_id})
    assert r.status_code == 200
    assert r.json()["ok"] is True

    with SessionFactory() as s:
        # still exactly one export for this episode slot (upserted in place, not duplicated)
        assert s.scalar(select(func.count()).select_from(Export)) == 2
        export = s.get(Export, export_id)
        assert export.episode_id == target_ep_id

    strm = media_root / "TV Shows/Cool Show (2020)/Season 01/Cool Show S01E01.strm"
    assert "b.tv" in strm.read_text()


def test_retarget_episode_export_rejects_mismatched_episode(client, SessionFactory, media_root):
    from app.models import Export

    _series1_id, series2_id = _seed_two_server_series_with_exports(SessionFactory, media_root)
    with SessionFactory() as s:
        export = s.scalar(select(Export).where(Export.title.contains("S01E01")))
        wrong_ep = _episode_at(s, series2_id, 1, 2)
        export_id, wrong_ep_id = export.id, wrong_ep.id

    r = client.post(f"/exports/{export_id}/retarget", json={"episode_id": wrong_ep_id})
    assert r.status_code == 400


def test_retarget_series_exports_switches_every_episode(client, SessionFactory, media_root):
    from app.models import Export

    series1_id, series2_id = _seed_two_server_series_with_exports(SessionFactory, media_root)

    r = client.post(f"/exports/series/{series1_id}/retarget", json={"target_series_id": series2_id})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["changed"] == 2

    with SessionFactory() as s:
        exports = list(s.scalars(select(Export)))
        assert len(exports) == 2
        assert all("b.tv" in e.stream_url_snapshot for e in exports)


def _two_servers(SessionFactory):
    from app.crypto import encrypt
    from app.models import XCServer

    with SessionFactory() as s:
        srv1 = XCServer(
            name="A", scheme="http", host="a.tv", port=80, username="u", password_enc=encrypt("pw"),
        )
        srv2 = XCServer(
            name="B", scheme="http", host="b.tv", port=80, username="u", password_enc=encrypt("pw"),
        )
        s.add_all([srv1, srv2])
        s.commit()
        return srv1.id, srv2.id


def test_movie_ids_endpoint_matches_category_filter(client, SessionFactory):
    from app.crypto import encrypt
    from app.models import Movie, XCServer

    with SessionFactory() as s:
        srv = XCServer(
            name="t", scheme="http", host="box.tv", port=8080,
            username="u", password_enc=encrypt("pw"),
        )
        s.add(srv)
        s.commit()
        s.add_all([
            Movie(
                server_id=srv.id, xc_stream_id="1", name="A1",
                title_clean="A1", category_name="Action",
            ),
            Movie(
                server_id=srv.id, xc_stream_id="2", name="A2",
                title_clean="A2", category_name="Action",
            ),
            Movie(
                server_id=srv.id, xc_stream_id="3", name="C1",
                title_clean="C1", category_name="Comedy",
            ),
        ])
        s.commit()
        action_ids = sorted(s.scalars(select(Movie.id).where(Movie.category_name == "Action")))

    r = client.get("/movies/ids", params={"category": "Action"})
    assert r.status_code == 200
    assert sorted(r.json()["ids"]) == action_ids
    assert len(client.get("/movies/ids").json()["ids"]) == 3

    # multi-select: both categories at once should union, not intersect
    r_multi = client.get("/movies/ids", params=[("category", "Action"), ("category", "Comedy")])
    assert len(r_multi.json()["ids"]) == 3

    page = client.get("/movies?category=Action")
    assert "Select all 2" in page.text
    assert "/movies/ids?" in page.text
    assert "categoryPicker(" in page.text

    # regression: tojson output must be HTML-attribute-escaped (forceescape),
    # or its raw quotes terminate the x-data="..." attribute early and Alpine
    # never initializes — categories silently don't render, no buttons work.
    assert 'categoryPicker({"' not in page.text

    # the provider select moved inside the filters popup — confirm the
    # current server filter still threads through to the Alpine component
    # as its 3rd arg, and the results list still narrows by server.
    server_page = client.get(f"/movies?server={srv.id}")
    assert f", '{srv.id}')\"" in server_page.text
    assert "3 movie" in server_page.text  # all 3 belong to this one server
    assert "&#34;Action&#34;" in page.text


def test_series_ids_endpoint_matches_category_filter(client, SessionFactory):
    from app.crypto import encrypt
    from app.models import Series, XCServer

    with SessionFactory() as s:
        srv = XCServer(
            name="t", scheme="http", host="box.tv", port=8080,
            username="u", password_enc=encrypt("pw"),
        )
        s.add(srv)
        s.commit()
        s.add_all([
            Series(
                server_id=srv.id, xc_series_id="1", name="D1",
                title_clean="D1", category_name="Drama",
            ),
            Series(
                server_id=srv.id, xc_series_id="2", name="D2",
                title_clean="D2", category_name="Drama",
            ),
            Series(
                server_id=srv.id, xc_series_id="3", name="C1",
                title_clean="C1", category_name="Comedy",
            ),
        ])
        s.commit()
        drama_ids = sorted(s.scalars(select(Series.id).where(Series.category_name == "Drama")))

    r = client.get("/tv/ids", params={"category": "Drama"})
    assert r.status_code == 200
    assert sorted(r.json()["ids"]) == drama_ids
    assert len(client.get("/tv/ids").json()["ids"]) == 3

    r_multi = client.get("/tv/ids", params=[("category", "Drama"), ("category", "Comedy")])
    assert len(r_multi.json()["ids"]) == 3

    page = client.get("/tv?category=Drama")
    assert "categoryPicker(" in page.text


def test_movies_page_shows_duplicate_source_picker(client, SessionFactory):
    from app.models import Movie

    srv1_id, srv2_id = _two_servers(SessionFactory)
    with SessionFactory() as s:
        s.add_all([
            Movie(
                server_id=srv1_id, xc_stream_id="1", name="The Matrix",
                title_clean="The Matrix", year=1999, container_ext="mkv",
            ),
            Movie(
                server_id=srv2_id, xc_stream_id="1", name="The Matrix",
                title_clean="The Matrix", year=1999, container_ext="mp4",
            ),
        ])
        s.commit()

    r = client.get("/movies?view=list")
    assert r.status_code == 200
    assert "Pick which server" in r.text
    assert 'x-data="{ pick: 1 }"' in r.text
    # regression: exported_map's tojson output must be attribute-escaped too
    assert 'x-show="{"' not in r.text
    assert "&#34;1&#34;: false" in r.text


def test_series_page_shows_duplicate_source_picker(client, SessionFactory):
    from app.models import Series

    srv1_id, srv2_id = _two_servers(SessionFactory)
    with SessionFactory() as s:
        s.add_all([
            Series(
                server_id=srv1_id, xc_series_id="1",
                name="Cool Show", title_clean="Cool Show", year=2020,
            ),
            Series(
                server_id=srv2_id, xc_series_id="1",
                name="Cool Show", title_clean="Cool Show", year=2020,
            ),
        ])
        s.commit()

    r = client.get("/tv?view=list")
    assert r.status_code == 200
    assert "Pick which server" in r.text


def test_sync_interval_reschedules_live(client):
    from app import scheduler

    assert scheduler._scheduler is not None  # started by lifespan

    r = client.post("/settings", data={"sync_interval_hours": "3"}, follow_redirects=True)
    assert r.status_code == 200
    job = scheduler._scheduler.get_job("catalog-sync")
    assert job is not None

    client.post("/settings", data={"sync_interval_hours": "0"})
    assert scheduler._scheduler.get_job("catalog-sync") is None
