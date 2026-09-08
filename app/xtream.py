"""Xtream Codes ``player_api.php`` client, plus title cleaning and M3U fallback parsing.

Everything network-touching is async (httpx). The pure helpers — ``clean_title``,
``parse_m3u``, ``build_movie_url``, ``build_episode_url`` — have no I/O and are
unit-tested directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

from app.config import get_settings

# ---------------------------------------------------------------------------
# Title / year cleaning
# ---------------------------------------------------------------------------

_YEAR_RE = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
_BRACKETS_RE = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_DOT_SEP_RE = re.compile(r"[._]+")
_QUALITY_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|4k|uhd|hdr10\+?|hdr|sdr|10bit|8bit|"
    r"web[-\s]?dl|web[-\s]?rip|bluray|blu-ray|bdrip|brrip|hdrip|dvdrip|hdtv|cam|ts|"
    r"x264|x265|h\.?264|h\.?265|hevc|avc|xvid|aac|ac3|eac3|dts(?:-hd)?|ddp?5\.1|dd\+?|"
    r"multi|dual|subbed|dubbed|vostfr|remux|imax|extended|uncut|unrated|"
    r"director'?s\s?cut)\b",
    re.IGNORECASE,
)
_LANG_PREFIX_RE = re.compile(r"^\s*(?:[A-Z]{2,4}\s*[-|:]\s*)+")
_SXXEXX_RE = re.compile(r"[Ss](\d{1,2})[\s._x-]*[Ee](\d{1,3})")
_MULTISPACE_RE = re.compile(r"\s{2,}")


def clean_title(name: str) -> tuple[str, int | None]:
    """Best-effort split of a raw VOD name into ``(title, year)``.

    Handles ``Title (1999)``, ``Title.1999.1080p``, ``[EN] Title 4K``, etc.
    Returns the original name if nothing survives the scrub.
    """
    raw = (name or "").strip()
    year: int | None = None

    for match in _BRACKETS_RE.finditer(raw):
        found = _YEAR_RE.search(match.group(0))
        if found:
            year = int(found.group(1))
            break

    text = _BRACKETS_RE.sub(" ", raw)
    text = _DOT_SEP_RE.sub(" ", text)

    if year is None:
        found = _YEAR_RE.search(text)
        if found:
            year = int(found.group(1))
    text = _YEAR_RE.sub(" ", text)

    text = _QUALITY_RE.sub(" ", text)
    text = _LANG_PREFIX_RE.sub("", text)
    text = _MULTISPACE_RE.sub(" ", text).strip(" -–—:·|_")

    return (text or raw, year)


# ---------------------------------------------------------------------------
# M3U fallback parsing
# ---------------------------------------------------------------------------

_EXTINF_ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')


def parse_m3u(text: str) -> list[dict]:
    """Parse an ``m3u_plus`` playlist into VOD entries.

    Only ``/movie/`` and ``/series/`` URLs are kept (live channels are dropped).
    Series entries get ``season``/``episode`` when an ``SxxExx`` token is present
    in the name.
    """
    items: list[dict] = []
    pending: dict | None = None

    for rawline in text.splitlines():
        line = rawline.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(_EXTINF_ATTR_RE.findall(line))
            display = line.split(",", 1)[1].strip() if "," in line else ""
            pending = {
                "name": attrs.get("tvg-name") or display,
                "logo": attrs.get("tvg-logo") or None,
                "group": attrs.get("group-title") or None,
            }
            continue
        if line.startswith("#"):
            continue
        if pending is None:
            continue

        url = line
        if "/movie/" in url:
            kind = "movie"
        elif "/series/" in url:
            kind = "series"
        else:
            pending = None
            continue

        segment = url.rstrip("/").rsplit("/", 1)[-1]
        stream_id, _, ext = segment.partition(".")
        entry = {
            "kind": kind,
            "url": url,
            "stream_id": stream_id,
            "container_extension": ext or "mp4",
            "name": pending["name"],
            "logo": pending["logo"],
            "group": pending["group"],
        }
        if kind == "series":
            se = _SXXEXX_RE.search(pending["name"] or "")
            if se:
                entry["season"] = int(se.group(1))
                entry["episode"] = int(se.group(2))
        items.append(entry)
        pending = None

    return items


# ---------------------------------------------------------------------------
# Stream URL builders (no I/O)
# ---------------------------------------------------------------------------


def build_movie_url(
    *, base_url: str, username: str, password: str, xc_stream_id: str, ext: str
) -> str:
    return f"{base_url}/movie/{username}/{password}/{xc_stream_id}.{ext or 'mp4'}"


def build_episode_url(
    *, base_url: str, username: str, password: str, xc_episode_id: str, ext: str
) -> str:
    return f"{base_url}/series/{username}/{password}/{xc_episode_id}.{ext or 'mp4'}"


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class XtreamError(RuntimeError):
    """Transport / protocol failure talking to a panel."""


class XtreamAuthError(XtreamError):
    """Panel reached, but credentials rejected or account not usable."""


@dataclass
class XtreamAccount:
    status: str
    is_active: bool
    expires_at: datetime | None
    max_connections: int | None
    raw: dict = field(repr=False, default_factory=dict)


class XtreamClient:
    """Thin async wrapper over one panel's ``player_api.php``."""

    def __init__(
        self,
        *,
        scheme: str,
        host: str,
        port: int,
        username: str,
        password: str,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.scheme = scheme
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=timeout or get_settings().http_timeout,
            follow_redirects=True,
            headers={"User-Agent": "StrmVert/0.1 (+https://github.com/)"},
        )

    @classmethod
    def from_server(cls, server, **kwargs) -> XtreamClient:
        from app.crypto import decrypt

        return cls(
            scheme=server.scheme,
            host=server.host,
            port=server.port,
            username=server.username,
            password=decrypt(server.password_enc),
            **kwargs,
        )

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    async def __aenter__(self) -> XtreamClient:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _call(self, action: str | None = None, **params):
        query = {"username": self.username, "password": self.password}
        if action:
            query["action"] = action
        query.update({k: v for k, v in params.items() if v is not None})
        try:
            resp = await self._client.get(f"{self.base_url}/player_api.php", params=query)
        except httpx.HTTPError as exc:
            raise XtreamError(f"Cannot reach {self.host}: {exc}") from exc
        if resp.status_code != 200:
            raise XtreamError(f"{self.host} returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise XtreamError(f"{self.host} returned a non-JSON response") from exc

    async def _list(self, action: str, **params) -> list[dict]:
        data = await self._call(action, **params)
        return data if isinstance(data, list) else []

    async def authenticate(self) -> XtreamAccount:
        data = await self._call()
        if not isinstance(data, dict) or "user_info" not in data:
            raise XtreamAuthError("Unexpected response — double-check the host and port.")
        info = data.get("user_info") or {}
        if str(info.get("auth", 0)) != "1":
            raise XtreamAuthError("Authentication failed — wrong username or password.")
        status = str(info.get("status") or "").strip()
        raw_exp = info.get("exp_date")
        expires_at: datetime | None = None
        if raw_exp not in (None, "", "null", "0"):
            try:
                expires_at = datetime.fromtimestamp(int(raw_exp), tz=UTC)
            except (ValueError, OverflowError, OSError):
                expires_at = None
        raw_mc = str(info.get("max_connections") or "")
        return XtreamAccount(
            status=status or "Unknown",
            is_active=status.lower() == "active",
            expires_at=expires_at,
            max_connections=int(raw_mc) if raw_mc.isdigit() else None,
            raw=data,
        )

    async def get_vod_categories(self) -> list[dict]:
        return await self._list("get_vod_categories")

    async def get_vod_streams(self, category_id: str | None = None) -> list[dict]:
        return await self._list("get_vod_streams", category_id=category_id)

    async def get_vod_info(self, vod_id: str) -> dict:
        data = await self._call("get_vod_info", vod_id=vod_id)
        return data if isinstance(data, dict) else {}

    async def get_series_categories(self) -> list[dict]:
        return await self._list("get_series_categories")

    async def get_series(self, category_id: str | None = None) -> list[dict]:
        return await self._list("get_series", category_id=category_id)

    async def get_series_info(self, series_id: str) -> dict:
        data = await self._call("get_series_info", series_id=series_id)
        return data if isinstance(data, dict) else {}

    async def fetch_m3u(self) -> str:
        query = {
            "username": self.username,
            "password": self.password,
            "type": "m3u_plus",
            "output": "ts",
        }
        try:
            resp = await self._client.get(f"{self.base_url}/get.php", params=query)
        except httpx.HTTPError as exc:
            raise XtreamError(f"Cannot fetch M3U from {self.host}: {exc}") from exc
        if resp.status_code != 200:
            raise XtreamError(f"{self.host} returned HTTP {resp.status_code} for get.php")
        return resp.text
