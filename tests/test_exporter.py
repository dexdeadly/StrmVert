from sqlalchemy import func, select

from app.config import get_settings
from app.crypto import encrypt
from app.exporter import (
    delete_export,
    delete_exports_bulk,
    delete_series_exports,
    export_many,
    resolve_selections,
    verify_exports,
)
from app.models import Episode, Export, Movie, Series, XCServer


def _fixtures(session):
    srv = XCServer(
        name="t", scheme="http", host="box.tv", port=8080,
        username="u", password_enc=encrypt("pw"),
    )
    session.add(srv)
    session.commit()
    movie = Movie(
        server_id=srv.id, xc_stream_id="10", name="The Matrix (1999)",
        title_clean="The Matrix", year=1999, container_ext="mkv",
        plot="A hacker learns the truth.", tmdb_id="603",
    )
    series = Series(
        server_id=srv.id, xc_series_id="20", name="Cool Show",
        title_clean="Cool Show", year=2020, plot="Things happen.",
    )
    session.add_all([movie, series])
    session.commit()
    ep = Episode(
        series_id=series.id, xc_episode_id="55", season=1, episode=2,
        title="Pilot", container_ext="mp4",
    )
    session.add(ep)
    session.commit()
    return srv, movie, series, ep


def test_export_writes_strm_and_nfo_in_jellyfin_layout(session, media_root):
    _srv, movie, _series, ep = _fixtures(session)

    outcome = export_many(session, [movie], [ep], get_settings())
    assert (outcome.failed, outcome.written) == (0, 2)

    strm = media_root / "Movies/The Matrix (1999)/The Matrix (1999).strm"
    assert strm.read_text().strip() == "http://box.tv:8080/movie/u/pw/10.mkv"
    assert (media_root / "Movies/The Matrix (1999)/The Matrix (1999).nfo").exists()

    ep_strm = media_root / "TV Shows/Cool Show (2020)/Season 01/Cool Show S01E02.strm"
    assert ep_strm.read_text().strip() == "http://box.tv:8080/series/u/pw/55.mp4"
    assert (media_root / "TV Shows/Cool Show (2020)/Season 01/Cool Show S01E02.nfo").exists()
    assert (media_root / "TV Shows/Cool Show (2020)/tvshow.nfo").exists()

    assert session.scalar(select(func.count()).select_from(Export)) == 2


def test_same_title_from_two_servers_upserts_one_row(session, media_root):
    """Two Movie rows (different servers) that resolve to the same target_path
    must not crash the batch on the target_path unique constraint."""
    srv1, movie1, _series, _ep = _fixtures(session)
    srv2 = XCServer(
        name="t2", scheme="http", host="box2.tv", port=8080,
        username="u", password_enc=encrypt("pw"),
    )
    session.add(srv2)
    session.commit()
    movie2 = Movie(
        server_id=srv2.id, xc_stream_id="10", name="The Matrix (1999)",
        title_clean="The Matrix", year=1999, container_ext="mp4",
    )
    session.add(movie2)
    session.commit()

    outcome = export_many(session, [movie1, movie2], [], get_settings())

    assert outcome.failed == 0
    assert session.scalar(select(func.count()).select_from(Export)) == 1
    export = session.scalar(select(Export))
    assert export.movie_id == movie2.id  # last one in wins


def test_reexport_is_idempotent(session, media_root):
    _srv, movie, _series, _ep = _fixtures(session)
    export_many(session, [movie], [], get_settings())
    export_many(session, [movie], [], get_settings())
    assert session.scalar(select(func.count()).select_from(Export)) == 1
    assert session.scalar(select(Export)).status == "written"


def test_delete_export_removes_files_and_prunes(session, media_root):
    _srv, movie, _series, _ep = _fixtures(session)
    export_many(session, [movie], [], get_settings())
    export = session.scalar(select(Export))

    delete_export(session, export, get_settings())

    assert session.scalar(select(func.count()).select_from(Export)) == 0
    assert not (media_root / "Movies").exists()


def test_verify_flags_missing(session, media_root):
    _srv, movie, _series, _ep = _fixtures(session)
    export_many(session, [movie], [], get_settings())
    (media_root / "Movies/The Matrix (1999)/The Matrix (1999).strm").unlink()

    stats = verify_exports(session, get_settings())
    assert stats["missing"] == 1
    assert session.scalar(select(Export)).status == "missing"


async def test_resolve_selections_plain_tokens(session):
    _srv, movie, _series, ep = _fixtures(session)
    movies, episodes, errors = await resolve_selections(session, [f"m:{movie.id}", f"e:{ep.id}"])
    assert [m.id for m in movies] == [movie.id]
    assert [e.id for e in episodes] == [ep.id]
    assert errors == []


def test_delete_series_exports_removes_every_episode(session, media_root):
    _srv, _movie, series, ep = _fixtures(session)
    ep2 = Episode(
        series_id=series.id, xc_episode_id="56", season=1, episode=3,
        title="Second", container_ext="mp4",
    )
    session.add(ep2)
    session.commit()

    export_many(session, [], [ep, ep2], get_settings())
    assert session.scalar(select(func.count()).select_from(Export)) == 2

    count = delete_series_exports(session, series.id, get_settings())

    assert count == 2
    assert session.scalar(select(func.count()).select_from(Export)) == 0
    assert not (media_root / "TV Shows/Cool Show (2020)").exists()


def test_delete_exports_bulk_mixes_movies_and_episodes(session, media_root):
    _srv, movie, _series, ep = _fixtures(session)
    export_many(session, [movie], [ep], get_settings())
    exports = list(session.scalars(select(Export)))

    count = delete_exports_bulk(session, exports, get_settings())

    assert count == 2
    assert session.scalar(select(func.count()).select_from(Export)) == 0
    assert not (media_root / "Movies").exists()
    assert not (media_root / "TV Shows").exists()
