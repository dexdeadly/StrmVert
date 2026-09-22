"""Create exports from library selections; manage existing ``.strm`` files."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.exporter import (
    delete_export,
    delete_exports_bulk,
    export_episode,
    export_many,
    resolve_selections,
    rewrite_export,
    verify_exports,
)
from app.models import Episode, Export, Series
from app.schemas import ExportIn, RetargetEpisodeIn, RetargetSeriesIn
from app.security import require_auth
from app.settings_store import effective as effective_settings
from app.templating import render

log = logging.getLogger("strmvert.exports")
router = APIRouter(dependencies=[Depends(require_auth)])

_STATUSES = ("written", "stale", "missing", "error")


@router.get("/exports", include_in_schema=False)
async def exports_page(
    request: Request,
    session: Session = Depends(get_session),
    status_filter: str | None = Query(None, alias="status"),
    note: str | None = None,
):
    stmt = select(Export).order_by(Export.updated_at.desc()).options(selectinload(Export.episode))
    if status_filter in _STATUSES:
        stmt = stmt.where(Export.status == status_filter)
    rows = list(session.scalars(stmt))
    counts = dict(
        session.execute(select(Export.status, func.count()).group_by(Export.status)).all()
    )
    return render(
        request,
        "exports.html",
        active="exports",
        exports=rows,
        counts=counts,
        total=sum(counts.values()),
        status_filter=status_filter if status_filter in _STATUSES else None,
        note=note,
    )


@router.post("/export", include_in_schema=False)
async def create_exports(payload: ExportIn, session: Session = Depends(get_session)):
    movies, episodes, errors = await resolve_selections(session, payload.selections)
    if not movies and not episodes:
        return JSONResponse(
            {"ok": False, "written": 0, "failed": 0, "errors": errors or ["Nothing to export."]},
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    outcome = export_many(session, movies, episodes)
    return {
        "ok": outcome.failed == 0,
        "written": outcome.written,
        "created": outcome.created,
        "updated": outcome.updated,
        "failed": outcome.failed,
        "errors": errors + outcome.errors,
    }


@router.post("/exports/{export_id}/rewrite", include_in_schema=False)
async def rewrite_one(
    export_id: int, request: Request, session: Session = Depends(get_session)
):
    export = session.get(Export, export_id)
    if export is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        rewrite_export(session, export)
    except Exception as exc:  # noqa: BLE001
        log.exception("rewrite failed for export %s", export_id)
        export.status = "error"
        export.error = str(exc)[:2000]
        session.commit()
    session.refresh(export)
    return render(request, "partials/export_row.html", e=export)


@router.post("/exports/{export_id}/delete", include_in_schema=False)
async def delete_one(export_id: int, session: Session = Depends(get_session)):
    export = session.get(Export, export_id)
    if export is not None:
        delete_export(session, export)
    return HTMLResponse("")


@router.post("/exports/bulk-delete", include_in_schema=False)
async def bulk_delete(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    ids = [int(v) for v in form.getlist("ids") if str(v).isdigit()]
    exports = list(session.scalars(select(Export).where(Export.id.in_(ids)))) if ids else []
    count = delete_exports_bulk(session, exports)
    note = f"Deleted {count} export(s)." if count else "Nothing selected."
    return RedirectResponse(f"/exports?note={quote(note)}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/exports/series/{series_id}/manage", include_in_schema=False)
async def manage_series_exports(
    series_id: int, request: Request, session: Session = Depends(get_session)
):
    series = session.get(Series, series_id)
    if series is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    exports = list(
        session.scalars(
            select(Export)
            .join(Episode, Export.episode_id == Episode.id)
            .where(Episode.series_id == series_id)
            .options(selectinload(Export.episode), selectinload(Export.server))
            .order_by(Episode.season, Episode.episode)
        )
    )
    sibling_series = list(
        session.scalars(
            select(Series)
            .where(
                Series.id != series_id,
                Series.title_clean == series.title_clean,
                Series.year == series.year,
                Series.is_stale.is_(False),
            )
            .options(selectinload(Series.server))
        )
    )
    sibling_eps: dict[tuple[int, int], list[Episode]] = {}
    if sibling_series:
        rows = session.scalars(
            select(Episode)
            .where(Episode.series_id.in_([s.id for s in sibling_series]))
            .options(selectinload(Episode.series).selectinload(Series.server))
        )
        for ep in rows:
            sibling_eps.setdefault((ep.season, ep.episode), []).append(ep)

    items = [
        {
            "export": export,
            "episode": export.episode,
            "candidates": sibling_eps.get((export.episode.season, export.episode.episode), [])
            if export.episode
            else [],
        }
        for export in exports
    ]
    return render(
        request, "partials/series_manage.html",
        series=series, items=items, sibling_series=sibling_series,
    )


@router.post("/exports/{export_id}/retarget", include_in_schema=False)
async def retarget_episode_export(
    export_id: int, payload: RetargetEpisodeIn, session: Session = Depends(get_session)
):
    export = session.get(Export, export_id)
    if export is None or export.kind != "episode":
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    new_episode = session.get(Episode, payload.episode_id)
    if new_episode is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "episode not found")

    old_episode = export.episode
    if old_episode is not None:
        old_series, new_series = old_episode.series, new_episode.series
        if (
            new_episode.season != old_episode.season
            or new_episode.episode != old_episode.episode
            or new_series.title_clean != old_series.title_clean
            or new_series.year != old_series.year
        ):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a matching episode")

    try:
        export_episode(session, effective_settings(), new_episode)
    except Exception as exc:  # noqa: BLE001
        log.exception("retarget failed for export %s", export_id)
        session.rollback()
        return JSONResponse(
            {"ok": False, "detail": str(exc)}, status_code=status.HTTP_400_BAD_REQUEST
        )
    session.commit()
    return {"ok": True}


@router.post("/exports/series/{series_id}/retarget", include_in_schema=False)
async def retarget_series_exports(
    series_id: int, payload: RetargetSeriesIn, session: Session = Depends(get_session)
):
    series = session.get(Series, series_id)
    target_series = session.get(Series, payload.target_series_id)
    if series is None or target_series is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    if target_series.title_clean != series.title_clean or target_series.year != series.year:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "not a matching series")

    exports = list(
        session.scalars(
            select(Export)
            .join(Episode, Export.episode_id == Episode.id)
            .where(Episode.series_id == series_id)
            .options(selectinload(Export.episode))
        )
    )
    settings = effective_settings()
    changed = 0
    errors: list[str] = []
    for export in exports:
        old_episode = export.episode
        if old_episode is None:
            continue
        new_episode = session.scalar(
            select(Episode).where(
                Episode.series_id == target_series.id,
                Episode.season == old_episode.season,
                Episode.episode == old_episode.episode,
            )
        )
        if new_episode is None:
            label = f"S{old_episode.season:02d}E{old_episode.episode:02d}"
            errors.append(f"{label}: not cached on the target server yet — sync it on the TV tab")
            continue
        try:
            export_episode(session, settings, new_episode)
            changed += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("series retarget failed for export %s", export.id)
            errors.append(f"S{old_episode.season:02d}E{old_episode.episode:02d}: {exc}")
    session.commit()
    return {"ok": not errors, "changed": changed, "errors": errors}


@router.post("/exports/verify", include_in_schema=False)
async def verify_all(session: Session = Depends(get_session)):
    stats = verify_exports(session)
    note = (
        f"Checked {stats['written'] + stats['missing']} export(s) on disk — "
        f"{stats['missing']} missing."
    )
    return RedirectResponse(f"/exports?note={quote(note)}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/exports/rewrite-stale", include_in_schema=False)
async def rewrite_stale(session: Session = Depends(get_session)):
    rows = list(
        session.scalars(
            select(Export).where(Export.status.in_(["stale", "missing", "error"]))
        )
    )
    ok = 0
    failed = 0
    for export in rows:
        try:
            rewrite_export(session, export)
            ok += 1
        except Exception:  # noqa: BLE001
            log.exception("rewrite-stale failed for export %s", export.id)
            export.status = "error"
            failed += 1
    session.commit()
    note = f"Re-wrote {ok} export(s)" + (f", {failed} failed" if failed else "") + "."
    return RedirectResponse(f"/exports?note={quote(note)}", status_code=status.HTTP_303_SEE_OTHER)
