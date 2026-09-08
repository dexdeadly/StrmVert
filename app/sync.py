"""Pull an Xtream panel's VOD catalog into the local database.

- :func:`sync_server` — shallow sync: categories + every movie + every series
  (series episodes are *not* fetched here; that is one API call per series).
- :func:`ensure_series_episodes` — fetch + cache one series' episodes on demand
  (used when the TV tab expands a row, and before exporting a series/season).
- :func:`deep_sync_server` — fetch episodes for every series, rate-limited.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Category, Episode, Movie, Series, XCServer
from app.xtream import XtreamClient, XtreamError, clean_title

log = logging.getLogger("strmvert.sync")

_BATCH = 400
_EPISODE_CACHE_TTL = timedelta(hours=24)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _s(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _dt(value) -> datetime | None:
    ts = _int(value)
    if ts is None or ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, UTC).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _ext(value) -> str:
    return (_s(value) or "mp4").lstrip(".").lower()


def _year_from_date(value) -> int | None:
    text = _s(value)
    if text and len(text) >= 4 and text[:4].isdigit():
        return int(text[:4])
    return None


# ---------------------------------------------------------------------------
# field extraction
# ---------------------------------------------------------------------------


def _movie_fields(item: dict, cat_by_xc: dict[str, Category]) -> dict:
    name = _s(item.get("name")) or _s(item.get("title")) or "Untitled"
    title_clean, year = clean_title(name)
    year = _int(item.get("year")) or year
    cat = cat_by_xc.get(_s(item.get("category_id")) or "")
    return {
        "name": name,
        "title_clean": title_clean,
        "year": year,
        "tmdb_id": _s(item.get("tmdb_id")) or _s(item.get("tmdb")),
        "rating": _s(item.get("rating")),
        "poster_url": _s(item.get("stream_icon")),
        "container_ext": _ext(item.get("container_extension")),
        "category_id": cat.id if cat else None,
        "category_name": cat.name if cat else None,
        "added_at": _dt(item.get("added")),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def _series_fields(item: dict, cat_by_xc: dict[str, Category]) -> dict:
    name = _s(item.get("name")) or _s(item.get("title")) or "Untitled"
    title_clean, year = clean_title(name)
    year = year or _year_from_date(item.get("releaseDate") or item.get("release_date"))
    cat = cat_by_xc.get(_s(item.get("category_id")) or "")
    return {
        "name": name,
        "title_clean": title_clean,
        "year": year,
        "tmdb_id": _s(item.get("tmdb")) or _s(item.get("tmdb_id")),
        "plot": _s(item.get("plot")),
        "genre": _s(item.get("genre")),
        "rating": _s(item.get("rating")),
        "poster_url": _s(item.get("cover")),
        "category_id": cat.id if cat else None,
        "category_name": cat.name if cat else None,
        "last_modified": _dt(item.get("last_modified")),
        "raw_json": json.dumps(item, ensure_ascii=False),
    }


def _episode_fields(ep: dict, season_no: int) -> dict | None:
    xc_id = _s(ep.get("id"))
    if not xc_id:
        return None
    info = ep.get("info") if isinstance(ep.get("info"), dict) else {}
    season = _int(ep.get("season")) or _int(info.get("season")) or season_no
    number = _int(ep.get("episode_num")) or _int(info.get("episode")) or 0
    return {
        "xc_episode_id": xc_id,
        "season": int(season),
        "episode": int(number),
        "title": _s(ep.get("title")) or _s(info.get("name")),
        "plot": _s(info.get("plot")),
        "rating": _s(info.get("rating")),
        "container_ext": _ext(ep.get("container_extension")),
        "added_at": _dt(ep.get("added")),
        "raw_json": json.dumps(ep, ensure_ascii=False),
    }


# ---------------------------------------------------------------------------
# category upsert
# ---------------------------------------------------------------------------


def _upsert_categories(session, server_id: int, kind: str, rows: list[dict]) -> dict[str, Category]:
    existing = {
        c.xc_category_id: c
        for c in session.scalars(
            select(Category).where(Category.server_id == server_id, Category.kind == kind)
        )
    }
    for row in rows:
        xc_id = _s(row.get("category_id"))
        if not xc_id:
            continue
        name = _s(row.get("category_name")) or f"Category {xc_id}"
        cat = existing.get(xc_id)
        if cat is None:
            cat = Category(server_id=server_id, kind=kind, xc_category_id=xc_id, name=name)
            session.add(cat)
            existing[xc_id] = cat
        else:
            cat.name = name
    session.flush()
    return existing


# ---------------------------------------------------------------------------
# shallow sync
# ---------------------------------------------------------------------------


def _set_message(server_id: int, message: str | None) -> None:
    """Write the live progress line shown on the Servers tab (own short txn)."""
    with SessionLocal() as session:
        server = session.get(XCServer, server_id)
        if server is not None:
            server.last_sync_message = message
            session.commit()


async def sync_server(server_id: int) -> None:
    """Entry point for a background sync of one server."""
    with SessionLocal() as session:
        server = session.get(XCServer, server_id)
        if server is None:
            return
        if server.is_syncing:
            log.info("server %s already syncing, skipping", server_id)
            return
        server.is_syncing = True
        server.last_sync_status = "running"
        server.last_sync_message = "Starting sync…"
        session.commit()

    try:
        await _run_shallow_sync(server_id)
    except Exception as exc:  # noqa: BLE001 — record every failure mode
        log.exception("sync failed for server %s", server_id)
        _finish(server_id, status="error", error=str(exc)[:2000])
    else:
        _finish(server_id, status="ok", error=None)


def _finish(server_id: int, *, status: str, error: str | None) -> None:
    with SessionLocal() as session:
        server = session.get(XCServer, server_id)
        if server is None:
            return
        server.is_syncing = False
        server.last_sync_status = status
        server.last_sync_error = error
        server.last_sync_message = None
        if status == "ok":
            server.last_sync_at = _utcnow()
        server.movie_count = (
            session.scalar(
                select(func.count())
                .select_from(Movie)
                .where(Movie.server_id == server_id, Movie.is_stale.is_(False))
            )
            or 0
        )
        server.series_count = (
            session.scalar(
                select(func.count())
                .select_from(Series)
                .where(Series.server_id == server_id, Series.is_stale.is_(False))
            )
            or 0
        )
        session.commit()


async def _run_shallow_sync(server_id: int) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        server = session.get(XCServer, server_id)
        client = XtreamClient.from_server(server, timeout=settings.http_timeout)

    try:
        _set_message(server_id, "Connecting to panel…")
        account = await client.authenticate()
        _set_message(server_id, "Downloading catalog from panel…")
        vod_cats = await client.get_vod_categories()
        vod_streams = await client.get_vod_streams()
        series_cats = await client.get_series_categories()
        series_list = await client.get_series()
    finally:
        await client.aclose()

    _set_message(
        server_id,
        f"Received {len(vod_streams):,} movies and {len(series_list):,} series — importing…",
    )

    with SessionLocal() as session:
        server = session.get(XCServer, server_id)
        server.account_expires_at = account.expires_at
        cat_movie = _upsert_categories(session, server_id, "movie", vod_cats)
        cat_series = _upsert_categories(session, server_id, "series", series_cats)
        session.commit()

        await _upsert_movies(session, server, vod_streams, cat_movie)
        await _upsert_series(session, server, series_list, cat_series)
        session.commit()


async def _upsert_movies(session, server, items, cat_by_xc) -> None:
    total = len(items)
    existing = {
        m.xc_stream_id: m
        for m in session.scalars(select(Movie).where(Movie.server_id == server.id))
    }
    seen: set[str] = set()
    now = _utcnow()
    for idx, item in enumerate(items, start=1):
        sid = _s(item.get("stream_id"))
        if not sid:
            continue
        seen.add(sid)
        fields = _movie_fields(item, cat_by_xc)
        row = existing.get(sid)
        if row is None:
            session.add(Movie(server_id=server.id, xc_stream_id=sid, is_stale=False, **fields))
        else:
            # Preserve fields filled in by on-demand enrichment (get_vod_info).
            if row.enriched:
                fields.pop("tmdb_id", None)
            for key, value in fields.items():
                setattr(row, key, value)
            row.is_stale = False
            row.synced_at = now
        if idx % _BATCH == 0:
            server.last_sync_message = f"Importing movies… {idx:,} / {total:,}"
            session.commit()  # persist progress + release the write lock
            await asyncio.sleep(0)
    for sid, row in existing.items():
        if sid not in seen:
            row.is_stale = True
    server.last_sync_message = f"Imported {total:,} movies — starting series…"
    session.commit()


async def _upsert_series(session, server, items, cat_by_xc) -> None:
    total = len(items)
    existing = {
        s.xc_series_id: s
        for s in session.scalars(select(Series).where(Series.server_id == server.id))
    }
    seen: set[str] = set()
    now = _utcnow()
    for idx, item in enumerate(items, start=1):
        sid = _s(item.get("series_id"))
        if not sid:
            continue
        seen.add(sid)
        fields = _series_fields(item, cat_by_xc)
        row = existing.get(sid)
        if row is None:
            session.add(Series(server_id=server.id, xc_series_id=sid, is_stale=False, **fields))
        else:
            for key, value in fields.items():
                setattr(row, key, value)
            row.is_stale = False
            row.synced_at = now
        if idx % _BATCH == 0:
            server.last_sync_message = f"Importing series… {idx:,} / {total:,}"
            session.commit()
            await asyncio.sleep(0)
    for sid, row in existing.items():
        if sid not in seen:
            row.is_stale = True
    server.last_sync_message = f"Imported {total:,} series — finishing…"
    session.commit()


# ---------------------------------------------------------------------------
# episodes
# ---------------------------------------------------------------------------


def _iter_episode_groups(info: dict):
    episodes = info.get("episodes")
    if isinstance(episodes, dict):
        for season_key, eps in episodes.items():
            if isinstance(eps, list):
                yield _int(season_key) or 0, eps
    elif isinstance(episodes, list):  # some panels flatten it
        yield 0, episodes


def _apply_series_info(series: Series, info: dict) -> None:
    block = info.get("info") if isinstance(info.get("info"), dict) else {}
    if not block:
        return
    series.plot = series.plot or _s(block.get("plot"))
    series.genre = series.genre or _s(block.get("genre"))
    series.rating = series.rating or _s(block.get("rating"))
    series.tmdb_id = series.tmdb_id or _s(block.get("tmdb_id")) or _s(block.get("tmdb"))
    series.poster_url = series.poster_url or _s(block.get("cover"))
    series.year = series.year or _year_from_date(
        block.get("releaseDate") or block.get("release_date")
    )


def _upsert_episodes(session, series: Series, info: dict) -> int:
    existing = {(e.season, e.episode): e for e in series.episodes}
    count = 0
    for season_no, eps in _iter_episode_groups(info):
        for ep in eps:
            if not isinstance(ep, dict):
                continue
            fields = _episode_fields(ep, season_no)
            if fields is None:
                continue
            key = (fields["season"], fields["episode"])
            row = existing.get(key)
            if row is None:
                session.add(Episode(series_id=series.id, **fields))
            else:
                for k, v in fields.items():
                    setattr(row, k, v)
            count += 1
    _apply_series_info(series, info)
    series.episodes_synced_at = _utcnow()
    session.flush()
    return count


async def ensure_series_episodes(series_id: int, *, force: bool = False) -> Series | None:
    """Fetch + cache one series' episodes if missing or stale. Returns the Series."""
    settings = get_settings()
    with SessionLocal() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        fresh = (
            series.episodes_synced_at is not None
            and _utcnow() - series.episodes_synced_at < _EPISODE_CACHE_TTL
        )
        if fresh and not force:
            return series
        server = series.server
        xc_series_id = series.xc_series_id
        client = XtreamClient.from_server(server, timeout=settings.http_timeout)

    try:
        info = await client.get_series_info(xc_series_id)
    finally:
        await client.aclose()

    with SessionLocal() as session:
        series = session.get(Series, series_id)
        if series is None:
            return None
        _upsert_episodes(session, series, info)
        session.commit()
        session.refresh(series)
        return series


def apply_vod_info(movie: Movie, info: dict) -> None:
    """Merge a ``get_vod_info`` payload into a Movie row (fills blanks only)."""
    block = info.get("info") if isinstance(info.get("info"), dict) else {}
    if not block:
        return
    movie.plot = movie.plot or _s(block.get("plot")) or _s(block.get("description"))
    movie.genre = movie.genre or _s(block.get("genre"))
    movie.rating = _s(block.get("rating")) or movie.rating
    movie.tmdb_id = movie.tmdb_id or _s(block.get("tmdb_id")) or _s(block.get("tmdb"))
    imdb = _s(block.get("imdb_id")) or _s(block.get("imdb"))
    if imdb and imdb.startswith("tt"):
        movie.imdb_id = movie.imdb_id or imdb
    movie.year = movie.year or _year_from_date(
        block.get("releasedate") or block.get("release_date")
    )
    movie.poster_url = (
        movie.poster_url or _s(block.get("movie_image")) or _s(block.get("cover_big"))
    )


async def deep_sync_server(server_id: int) -> None:
    """Fetch episodes for every non-stale series on a server, rate-limited."""
    settings = get_settings()
    with SessionLocal() as session:
        server = session.get(XCServer, server_id)
        if server is None or server.is_syncing:
            return
        server.is_syncing = True
        server.last_sync_status = "running"
        server.last_sync_message = "Deep sync: preparing…"
        session.commit()
        ids = list(
            session.scalars(
                select(Series.id).where(
                    Series.server_id == server_id, Series.is_stale.is_(False)
                )
            )
        )

    total = len(ids)
    done = 0
    sem = asyncio.Semaphore(max(1, settings.xc_max_concurrency))

    async def _one(sid: int) -> None:
        nonlocal done
        async with sem:
            try:
                await ensure_series_episodes(sid, force=True)
            except XtreamError as exc:
                log.warning("deep sync: series %s failed: %s", sid, exc)
            done += 1
            if done % 10 == 0 or done == total:
                _set_message(server_id, f"Deep sync: episodes for {done:,} / {total:,} shows")

    try:
        await asyncio.gather(*(_one(sid) for sid in ids))
    except Exception as exc:  # noqa: BLE001
        log.exception("deep sync failed for server %s", server_id)
        _finish(server_id, status="error", error=str(exc)[:2000])
    else:
        _finish(server_id, status="ok", error=None)
