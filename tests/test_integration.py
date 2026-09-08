"""Full path through the HTTP layer: add server → sync → browse → export → files."""

import respx
from sqlalchemy import func, select

from app.models import Export, Movie, XCServer
from tests.test_sync import _router


@respx.mock
def test_add_sync_browse_export(client, SessionFactory, media_root):
    respx.route(url__regex=r".*/player_api\.php.*").mock(side_effect=_router)

    client.post(
        "/servers",
        data={
            "name": "Box", "scheme": "http", "host": "box.tv", "port": "8080",
            "username": "u", "password": "p", "is_active": "true",
        },
        follow_redirects=True,
    )
    with SessionFactory() as s:
        server_id = s.scalar(select(XCServer.id))
    assert server_id is not None

    # Background task runs inside this call under TestClient.
    assert client.post(f"/servers/{server_id}/sync").status_code == 200

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Movie)) == 2
        movie_ids = list(s.scalars(select(Movie.id).order_by(Movie.id)))

    page = client.get("/movies")
    assert page.status_code == 200
    assert "The Matrix" in page.text

    result = client.post("/export", json={"selections": [f"m:{i}" for i in movie_ids]})
    body = result.json()
    assert body["ok"] is True
    assert body["written"] == 2

    strm = media_root / "Movies/The Matrix (1999)/The Matrix (1999).strm"
    assert strm.is_file()
    assert strm.read_text().strip() == "http://box.tv:8080/movie/u/p/10.mkv"
    assert (media_root / "Movies/The Matrix (1999)/The Matrix (1999).nfo").is_file()

    with SessionFactory() as s:
        assert s.scalar(select(func.count()).select_from(Export)) == 2

    exports_page = client.get("/exports")
    assert "The Matrix (1999)" in exports_page.text
    assert "The Matrix (1999).strm" in exports_page.text
