"""Request payload validation."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

_SCHEME_RE = re.compile(r"^(https?)://", re.IGNORECASE)


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    scheme: Literal["http", "https"] = "http"
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=80, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=255)
    password: str | None = Field(default=None, max_length=255)
    is_active: bool = True

    @field_validator("name", "username", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("host", mode="before")
    @classmethod
    def _clean_host(cls, value):
        if not isinstance(value, str):
            return value
        return _SCHEME_RE.sub("", value.strip()).rstrip("/").split("/")[0]

    def normalized(self) -> ServerIn:
        """Pull a scheme:// prefix and :port out of a pasted host, if present."""
        raw = self.host
        scheme = self.scheme
        m = _SCHEME_RE.match(raw)
        if m:
            scheme = m.group(1).lower()
            raw = _SCHEME_RE.sub("", raw)
        host_part, _, port_part = raw.partition("/")[0].partition(":")
        port = self.port
        if port_part.isdigit():
            port = int(port_part)
        elif not m and self.port == 80 and scheme == "https":
            port = 443
        return self.model_copy(update={"scheme": scheme, "host": host_part, "port": port})


class ExportIn(BaseModel):
    """Selection tokens from the library UI.

    Accepted token shapes:
      ``m:<movie_id>``            one movie
      ``e:<episode_id>``          one episode
      ``s:<series_id>``           every (cached) episode of a series
      ``s:<series_id>:<season>``  every episode of one season
    """

    selections: list[str] = Field(min_length=1)

    @field_validator("selections")
    @classmethod
    def _validate_tokens(cls, values: list[str]) -> list[str]:
        ok = re.compile(r"^(m:\d+|e:\d+|s:\d+(:\d+)?)$")
        cleaned = [v.strip() for v in values if v and v.strip()]
        bad = [v for v in cleaned if not ok.match(v)]
        if bad:
            raise ValueError(f"malformed selection token(s): {', '.join(bad[:5])}")
        if not cleaned:
            raise ValueError("no selections")
        return cleaned


class RetargetEpisodeIn(BaseModel):
    """Re-point a single episode export at a different server's copy."""

    episode_id: int


class RetargetSeriesIn(BaseModel):
    """Re-point every exported episode of a series at a sibling series
    (the same title+year synced from a different XC server)."""

    target_series_id: int
