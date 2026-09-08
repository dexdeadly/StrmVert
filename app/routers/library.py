"""Movies + TV Shows browsing (server-rendered, HTMX-swapped result lists)."""

from __future__ import annotations

import logging
import math
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import tmdb
from app.db import get_session
from app.models import Episode, Export, Movie, Series, XCServer
from app.security import require_auth
from app.settings_store import effective as effective_settings
from app.sync import apply_vod_info, ensure_series_episodes
from app.templating import render
from app.xtream import XtreamClient, XtreamError

log = logging.getLogger("strmvert.library")

router = APIRouter(dependencies=[Depends(require_auth)])

PAGE_SIZES = (25, 50, 100)
DEFAULT_PAGE_SIZE = 50
VIEWS = ("grid", "list")


def _view(value: str | None, default: str) -> str:
    return value if value in VIEWS else default


def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _needs_full_page(request: Request, page_path: str) -> RedirectResponse | None:
    """A direct hit / refresh on a ``…/results`` URL (no HTMX header) should land
    on the real page instead of a bare, unstyled partial."""
    if _is_htmx(request):
        return None
    query = request.url.query
    return RedirectResponse(
        f"{page_path}?{query}" if query else page_path,
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _servers(session: Session) -> list[XCServer]:
    return list(session.scalars(select(XCServer).order_by(XCServer.name)))


def _as_int(value) -> int | None:
    """Coerce an optional query param ("" from an <option value=''>) to int/None."""
    text = str(value).strip() if value is not None else ""
    return int(text) if text.isdigit() else None


def _page_size(value) -> int:
    n = _as_int(value)
    return n if n in PAGE_SIZES else DEFAULT_PAGE_SIZE


def _page_no(value) -> int:
    return max(1, _as_int(value) or 1)


def _count(session: Session, stmt: Select) -> int:
    return session.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0


def _qs(base: dict, **overrides) -> str:
    merged = {k: v for k, v in {**base, **overrides}.items() if v not in (None, "", False)}
    return urlencode(merged)


# ---------------------------------------------------------------------------
# Movies
# ---------------------------------------------------------------------------


def _movie_context(
    session: Session,
    *,
    server: str | int | None,
    category: str | None,
    q: str | None,
    sort: str,
    page: str | int,
    page_size: str | int | None,
    hide_exported: bool,
    view: str | None = None,
) -> dict:
    server_id = _as_int(server)
    page_size = _page_size(page_size)
    page = _page_no(page)
    q = (q or "").strip()

    stmt = select(Movie).where(Movie.is_stale.is_(False))
    if server_id:
        stmt = stmt.where(Movie.server_id == server_id)
    if category:
        stmt = stmt.where(Movie.category_name == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Movie.title_clean.ilike(like), Movie.name.ilike(like)))

    exported_ids = set(
        session.scalars(
            select(Export.movie_id).where(
                Export.kind == "movie", Export.movie_id.is_not(None)
            )
        )
    )
    if hide_exported and exported_ids:
        stmt = stmt.where(Movie.id.not_in(exported_ids))

    total = _count(session, stmt)
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)

    orderings = {
        "name": (Movie.title_clean.asc(), Movie.year.desc()),
        "year_desc": (Movie.year.is_(None), Movie.year.desc(), Movie.title_clean.asc()),
        "year_asc": (Movie.year.is_(None), Movie.year.asc(), Movie.title_clean.asc()),
        "added_desc": (Movie.added_at.is_(None), Movie.added_at.desc(), Movie.title_clean.asc()),
    }
    stmt = stmt.order_by(*orderings.get(sort, orderings["name"]))
    rows = list(
        session.scalars(
            stmt.options(selectinload(Movie.server))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    categories = list(
        session.scalars(
            select(Movie.category_name)
            .where(Movie.is_stale.is_(False), Movie.category_name.is_not(None))
            .distinct()
            .order_by(Movie.category_name)
        )
    )
    filters = {
        "server": server_id,
        "category": category,
        "q": q,
        "sort": sort if sort in orderings else "name",
        "page_size": page_size,
        "hide_exported": hide_exported,
        "view": _view(view, "grid"),
    }
    return {
        "movies": rows,
        "total": total,
        "page": page,
        "pages": pages,
        "exported_ids": exported_ids,
        "servers": _servers(session),
        "categories": categories,
        "filters": filters,
        "qs": _qs(filters),
        "qs_noview": _qs({k: v for k, v in filters.items() if k != "view"}),
        "results_url": "/movies",
        "page_url": "/movies",
        "tab": "movies",
    }


@router.get("/", include_in_schema=False)
async def index():
    return RedirectResponse("/movies", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/movies", include_in_schema=False)
async def movies_page(
    request: Request,
    session: Session = Depends(get_session),
    server: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "name",
    page: str = "1",
    page_size: str | None = None,
    hide_exported: bool = False,
    view: str | None = None,
):
    ctx = _movie_context(
        session, server=server, category=category, q=q, sort=sort,
        page=page, page_size=page_size, hide_exported=hide_exported, view=view,
    )
    template = "partials/movie_results.html" if _is_htmx(request) else "movies.html"
    return render(request, template, active="movies", **ctx)


@router.get("/movies/results", include_in_schema=False)
async def movies_results(
    request: Request,
    session: Session = Depends(get_session),
    server: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "name",
    page: str = "1",
    page_size: str | None = None,
    hide_exported: bool = False,
    view: str | None = None,
):
    if (redirect := _needs_full_page(request, "/movies")) is not None:
        return redirect
    ctx = _movie_context(
        session, server=server, category=category, q=q, sort=sort,
        page=page, page_size=page_size, hide_exported=hide_exported, view=view,
    )
    return render(request, "partials/movie_results.html", **ctx)


@router.get("/movies/{movie_id}", include_in_schema=False)
async def movie_detail(
    movie_id: int, request: Request, session: Session = Depends(get_session)
):
    movie = session.get(Movie, movie_id)
    if movie is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if not movie.enriched:
        client = XtreamClient.from_server(movie.server)
        try:
            info = await client.get_vod_info(movie.xc_stream_id)
            apply_vod_info(movie, info)
            movie.enriched = True
            session.commit()
        except XtreamError:
            session.rollback()
        finally:
            await client.aclose()
        await _tmdb_enrich(session, "movie", movie)
    return render(request, "partials/movie_detail.html", movie=movie)


async def _tmdb_enrich(session: Session, kind: str, row) -> None:
    """Fill remaining metadata blanks from TMDB when the Settings page enables it."""
    eff = effective_settings()
    if not (eff.tmdb_enabled and eff.tmdb_api_key):
        return
    if row.plot and row.poster_url and row.tmdb_id:
        return
    try:
        if await tmdb.enrich(kind, row, eff.tmdb_api_key, eff.tmdb_language):
            session.commit()
    except tmdb.TMDBError as exc:
        log.warning("TMDB enrich failed for %s %s: %s", kind, row.id, exc)
        session.rollback()


# ---------------------------------------------------------------------------
# TV Shows
# ---------------------------------------------------------------------------


def _series_context(
    session: Session,
    *,
    server: str | int | None,
    category: str | None,
    q: str | None,
    sort: str,
    page: str | int,
    page_size: str | int | None,
    view: str | None = None,
) -> dict:
    server_id = _as_int(server)
    page_size = _page_size(page_size)
    page = _page_no(page)
    q = (q or "").strip()

    stmt = select(Series).where(Series.is_stale.is_(False))
    if server_id:
        stmt = stmt.where(Series.server_id == server_id)
    if category:
        stmt = stmt.where(Series.category_name == category)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Series.title_clean.ilike(like), Series.name.ilike(like)))

    total = _count(session, stmt)
    pages = max(1, math.ceil(total / page_size))
    page = min(page, pages)

    orderings = {
        "name": (Series.title_clean.asc(),),
        "year_desc": (Series.year.is_(None), Series.year.desc(), Series.title_clean.asc()),
        "recent": (Series.last_modified.is_(None), Series.last_modified.desc()),
    }
    stmt = stmt.order_by(*orderings.get(sort, orderings["name"]))
    rows = list(
        session.scalars(
            stmt.options(selectinload(Series.server))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )

    ep_counts = dict(
        session.execute(
            select(Episode.series_id, func.count())
            .where(Episode.series_id.in_([r.id for r in rows] or [0]))
            .group_by(Episode.series_id)
        ).all()
    )
    exported_series = set(
        session.scalars(
            select(Series.id)
            .join(Episode, Episode.series_id == Series.id)
            .join(Export, Export.episode_id == Episode.id)
            .where(Series.id.in_([r.id for r in rows] or [0]))
        )
    )
    categories = list(
        session.scalars(
            select(Series.category_name)
            .where(Series.is_stale.is_(False), Series.category_name.is_not(None))
            .distinct()
            .order_by(Series.category_name)
        )
    )
    filters = {
        "server": server_id,
        "category": category,
        "q": q,
        "sort": sort if sort in orderings else "name",
        "page_size": page_size,
        "view": _view(view, "list"),
    }
    return {
        "series_list": rows,
        "ep_counts": ep_counts,
        "exported_series": exported_series,
        "total": total,
        "page": page,
        "pages": pages,
        "servers": _servers(session),
        "categories": categories,
        "filters": filters,
        "qs": _qs(filters),
        "qs_noview": _qs({k: v for k, v in filters.items() if k != "view"}),
        "results_url": "/tv",
        "page_url": "/tv",
        "tab": "tv",
    }


@router.get("/tv", include_in_schema=False)
async def tv_page(
    request: Request,
    session: Session = Depends(get_session),
    server: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "name",
    page: str = "1",
    page_size: str | None = None,
    view: str | None = None,
):
    ctx = _series_context(
        session, server=server, category=category, q=q, sort=sort,
        page=page, page_size=page_size, view=view,
    )
    template = "partials/series_results.html" if _is_htmx(request) else "tv.html"
    return render(request, template, active="tv", **ctx)


@router.get("/tv/results", include_in_schema=False)
async def tv_results(
    request: Request,
    session: Session = Depends(get_session),
    server: str | None = None,
    category: str | None = None,
    q: str | None = None,
    sort: str = "name",
    page: str = "1",
    page_size: str | None = None,
    view: str | None = None,
):
    if (redirect := _needs_full_page(request, "/tv")) is not None:
        return redirect
    ctx = _series_context(
        session, server=server, category=category, q=q, sort=sort,
        page=page, page_size=page_size, view=view,
    )
    return render(request, "partials/series_results.html", **ctx)


@router.get("/tv/{series_id}/episodes", include_in_schema=False)
async def series_episodes(
    series_id: int,
    request: Request,
    session: Session = Depends(get_session),
    drawer: bool = False,
):
    if session.get(Series, series_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    error: str | None = None
    try:
        await ensure_series_episodes(series_id)
    except XtreamError as exc:
        error = str(exc)

    series = session.get(Series, series_id)
    session.refresh(series)
    if drawer:
        await _tmdb_enrich(session, "series", series)
    grouped: dict[int, list[Episode]] = {}
    for ep in series.episodes:
        grouped.setdefault(ep.season, []).append(ep)
    seasons = [
        {
            "number": number,
            "episodes": eps,
            "tokens": [f"e:{e.id}" for e in eps],
        }
        for number, eps in sorted(grouped.items())
    ]
    exported = set(
        session.scalars(select(Export.episode_id).where(Export.episode_id.is_not(None)))
    )
    return render(
        request,
        "partials/episode_drawer.html" if drawer else "partials/episode_tree.html",
        series=series,
        seasons=seasons,
        exported=exported,
        error=error,
    )
