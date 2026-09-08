import httpx
import respx
from sqlalchemy import func, select

from app.crypto import encrypt
from app.models import Movie, Series, XCServer
from app.sync import sync_server

_PAYLOADS = {
    None: {
        "user_info": {"auth": 1, "status": "Active", "exp_date": "1893456000"},
        "server_info": {"url": "box.tv"},
    },
    "get_vod_categories": [{"category_id": "1", "category_name": "Movies"}],
    "get_vod_streams": [
        {
            "stream_id": 10, "name": "The Matrix (1999)", "category_id": "1",
            "container_extension": "mkv", "stream_icon": "http://img/1.jpg",
            "rating": "8.7", "added": "1600000000",
        },
        {"stream_id": 11, "name": "Dune (2021)", "category_id": "1", "container_extension": "mp4"},
    ],
    "get_series_categories": [{"category_id": "2", "category_name": "Shows"}],
    "get_series": [
        {
            "series_id": 20, "name": "Cool Show", "category_id": "2",
            "plot": "stuff", "releaseDate": "2020-01-01", "cover": "http://img/s.jpg",
        }
    ],
}


def _router(request: httpx.Request) -> httpx.Response:
    action = request.url.params.get("action")
    return httpx.Response(200, json=_PAYLOADS.get(action, []))


@respx.mock
async def test_shallow_sync_upserts_and_dedupes(session, SessionFactory):
    respx.route(url__regex=r".*/player_api\.php.*").mock(side_effect=_router)

    server = XCServer(
        name="t", scheme="http", host="box.tv", port=8080,
        username="u", password_enc=encrypt("p"),
    )
    session.add(server)
    session.commit()
    server_id = server.id

    await sync_server(server_id)
    await sync_server(server_id)  # a second pass must not duplicate rows

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Movie)) == 2
        assert s.scalar(select(func.count()).select_from(Series)) == 1
        srv = s.get(XCServer, server_id)
        assert srv.last_sync_status == "ok"
        assert srv.is_syncing is False
        assert srv.last_sync_message is None  # progress line cleared when done
        assert srv.movie_count == 2
        assert srv.series_count == 1
        matrix = s.scalar(select(Movie).where(Movie.xc_stream_id == "10"))
        assert matrix.title_clean == "The Matrix"
        assert matrix.year == 1999
        assert matrix.container_ext == "mkv"
        assert matrix.category_name == "Movies"


@respx.mock
async def test_large_catalog_batches_without_lock_errors(session, SessionFactory):
    """Progress commits every _BATCH rows must not deadlock SQLite's single writer."""
    big = [
        {"stream_id": i, "name": f"Movie {i} (2020)", "category_id": "1",
         "container_extension": "mp4"}
        for i in range(1000)
    ]
    payloads = {**_PAYLOADS, "get_vod_streams": big}
    respx.route(url__regex=r".*/player_api\.php.*").mock(
        side_effect=lambda req: httpx.Response(
            200, json=payloads.get(req.url.params.get("action"), [])
        )
    )
    server = XCServer(
        name="big", scheme="http", host="box.tv", port=8080,
        username="u", password_enc=encrypt("p"),
    )
    session.add(server)
    session.commit()

    await sync_server(server.id)

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Movie)) == 1000
        assert s.get(XCServer, server.id).last_sync_status == "ok"


@respx.mock
async def test_unseen_rows_marked_stale_not_deleted(session, SessionFactory):
    respx.route(url__regex=r".*/player_api\.php.*").mock(side_effect=_router)
    server = XCServer(
        name="t", scheme="http", host="box.tv", port=8080,
        username="u", password_enc=encrypt("p"),
    )
    session.add(server)
    session.commit()
    server_id = server.id

    await sync_server(server_id)

    # Drop one movie from the feed, re-sync.
    shrunk = dict(_PAYLOADS)
    shrunk["get_vod_streams"] = _PAYLOADS["get_vod_streams"][:1]
    respx.route(url__regex=r".*/player_api\.php.*").mock(
        side_effect=lambda req: httpx.Response(
            200, json=shrunk.get(req.url.params.get("action"), [])
        )
    )
    await sync_server(server_id)

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Movie)) == 2  # still there
        gone = s.scalar(select(Movie).where(Movie.xc_stream_id == "11"))
        assert gone.is_stale is True
        srv = s.get(XCServer, server_id)
        assert srv.movie_count == 1  # stale excluded from the count
