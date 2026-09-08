"""XC server logins: list / add / edit / delete, test connection, trigger sync."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.crypto import decrypt, encrypt
from app.db import get_session
from app.exporter import mark_server_exports_stale
from app.models import XCServer
from app.schemas import ServerIn
from app.security import require_auth
from app.sync import deep_sync_server, sync_server
from app.templating import render
from app.xtream import XtreamClient, XtreamError

router = APIRouter(dependencies=[Depends(require_auth)])


@dataclass
class ProbeResult:
    ok: bool
    message: str | None = None
    status: str | None = None
    expires_at: datetime | None = None
    max_connections: int | None = None


async def _probe(scheme: str, host: str, port: int, username: str, password: str) -> ProbeResult:
    client = XtreamClient(
        scheme=scheme, host=host, port=port, username=username, password=password
    )
    try:
        account = await client.authenticate()
        return ProbeResult(
            ok=account.is_active,
            message=None if account.is_active else f"Account status: {account.status}",
            status=account.status,
            expires_at=account.expires_at,
            max_connections=account.max_connections,
        )
    except XtreamError as exc:
        return ProbeResult(ok=False, message=str(exc))
    finally:
        await client.aclose()


def _list_servers(session: Session) -> list[XCServer]:
    return list(session.scalars(select(XCServer).order_by(XCServer.name)))


@router.get("/servers", include_in_schema=False)
async def servers_page():
    # XC Servers live on the Settings page now.
    return RedirectResponse("/settings#servers", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/servers/table", include_in_schema=False)
async def servers_table(request: Request, session: Session = Depends(get_session)):
    servers = _list_servers(session)
    return render(
        request,
        "partials/servers_table.html",
        servers=servers,
        any_syncing=any(s.is_syncing for s in servers),
    )


def _form_to_schema(**kwargs) -> ServerIn:
    kwargs["port"] = int(kwargs.get("port") or 0) or 80
    return ServerIn(**kwargs).normalized()


@router.post("/servers", include_in_schema=False)
async def create_server(
    session: Session = Depends(get_session),
    name: str = Form(...),
    scheme: str = Form("http"),
    host: str = Form(...),
    port: str = Form("80"),
    username: str = Form(...),
    password: str = Form(...),
    is_active: bool = Form(False),
):
    try:
        data = _form_to_schema(
            name=name, scheme=scheme, host=host, port=port, username=username,
            password=password, is_active=is_active,
        )
    except (ValidationError, ValueError) as exc:
        return _redirect_servers(f"Invalid server details: {exc}")
    if not data.password:
        return _redirect_servers("A password is required when adding a server.")

    server = XCServer(
        name=data.name,
        scheme=data.scheme,
        host=data.host,
        port=data.port,
        username=data.username,
        password_enc=encrypt(data.password),
        is_active=data.is_active,
    )
    session.add(server)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return _redirect_servers("A server with that host, port and username already exists.")
    return _redirect_servers()


# NOTE: this literal route must be declared before "/servers/{server_id}" so the
# path "probe" is not swallowed by the id parameter.
@router.post("/servers/probe", include_in_schema=False)
async def test_unsaved_server(
    request: Request,
    scheme: str = Form("http"),
    host: str = Form(...),
    port: str = Form("80"),
    username: str = Form(...),
    password: str = Form(...),
):
    try:
        data = _form_to_schema(
            name="probe", scheme=scheme, host=host, port=port,
            username=username, password=password,
        )
    except (ValidationError, ValueError) as exc:
        return render(
            request, "partials/probe_result.html", result=ProbeResult(ok=False, message=str(exc))
        )
    result = await _probe(data.scheme, data.host, data.port, data.username, data.password or "")
    return render(request, "partials/probe_result.html", result=result)


@router.post("/servers/{server_id}", include_in_schema=False)
async def update_server(
    server_id: int,
    session: Session = Depends(get_session),
    name: str = Form(...),
    scheme: str = Form("http"),
    host: str = Form(...),
    port: str = Form("80"),
    username: str = Form(...),
    password: str = Form(""),
    is_active: bool = Form(False),
):
    server = session.get(XCServer, server_id)
    if server is None:
        return _redirect_servers("Server not found.")
    try:
        data = _form_to_schema(
            name=name, scheme=scheme, host=host, port=port, username=username,
            password=password, is_active=is_active,
        )
    except (ValidationError, ValueError) as exc:
        return _redirect_servers(f"Invalid server details: {exc}")

    creds_changed = (
        data.scheme != server.scheme
        or data.host != server.host
        or data.port != server.port
        or data.username != server.username
        or bool(data.password and data.password != decrypt(server.password_enc))
    )

    server.name = data.name
    server.scheme = data.scheme
    server.host = data.host
    server.port = data.port
    server.username = data.username
    server.is_active = data.is_active
    if data.password:
        server.password_enc = encrypt(data.password)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return _redirect_servers("Another server already uses that host, port and username.")

    if creds_changed:
        mark_server_exports_stale(session, server_id)
        return _redirect_servers(
            "Saved. Existing exports for this server were marked stale — "
            "use 'Re-write stale' on the Exports tab."
        )
    return _redirect_servers()


@router.post("/servers/{server_id}/delete", include_in_schema=False)
async def delete_server(server_id: int, session: Session = Depends(get_session)):
    server = session.get(XCServer, server_id)
    if server is not None:
        session.delete(server)
        session.commit()
    return _redirect_servers()


@router.post("/servers/{server_id}/test", include_in_schema=False)
async def test_saved_server(
    server_id: int, request: Request, session: Session = Depends(get_session)
):
    server = session.get(XCServer, server_id)
    if server is None:
        result = ProbeResult(ok=False, message="Server not found.")
    else:
        result = await _probe(
            server.scheme, server.host, server.port, server.username,
            decrypt(server.password_enc),
        )
    return render(request, "partials/probe_result.html", result=result)


@router.post("/servers/{server_id}/sync", include_in_schema=False)
async def sync_now(
    server_id: int,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    server = session.get(XCServer, server_id)
    if server is not None and not server.is_syncing:
        background.add_task(sync_server, server_id)
    session.expire_all()
    servers = _list_servers(session)
    return render(
        request,
        "partials/servers_table.html",
        servers=servers,
        any_syncing=True,
    )


@router.post("/servers/{server_id}/deep-sync", include_in_schema=False)
async def deep_sync_now(
    server_id: int,
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
):
    server = session.get(XCServer, server_id)
    if server is not None and not server.is_syncing:
        background.add_task(deep_sync_server, server_id)
    servers = _list_servers(session)
    return render(request, "partials/servers_table.html", servers=servers, any_syncing=True)


def _redirect_servers(message: str | None = None) -> RedirectResponse:
    url = "/settings#servers"
    if message:
        from urllib.parse import quote

        url = f"/settings?error={quote(message)}#servers"
    return RedirectResponse(url, status_code=status.HTTP_303_SEE_OTHER)
