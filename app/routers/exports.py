"""Create exports from library selections; manage existing ``.strm`` files."""

from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.exporter import (
    delete_export,
    export_many,
    resolve_selections,
    rewrite_export,
    verify_exports,
)
from app.models import Export
from app.schemas import ExportIn
from app.security import require_auth
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
    stmt = select(Export).order_by(Export.updated_at.desc())
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
