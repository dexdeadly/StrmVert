"""DB-backed overrides for a subset of config, edited on the Settings page.

Env vars are the defaults; a row in the ``setting`` table overrides one field.
``effective()`` returns a :class:`~app.config.Settings` with the overrides applied
— pass it wherever code currently takes a ``settings`` argument.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import Setting


@dataclass(frozen=True)
class Field:
    key: str
    type: str  # "str" | "bool" | "int"
    group: str
    label: str
    help: str = ""


# Order here is the render order on the Settings page.
FIELDS: tuple[Field, ...] = (
    Field("movies_dir", "str", "Library layout", "Movies folder",
          "Sub-folder of the media root for movies."),
    Field("tv_dir", "str", "Library layout", "TV Shows folder",
          "Sub-folder of the media root for series."),
    Field("write_nfo", "bool", "Library layout", "Write .nfo sidecars",
          "Emit Kodi/Jellyfin .nfo files next to each .strm."),
    Field("movie_folder_template", "str", "Filename templates", "Movie folder",
          "Placeholders: {title} {year}"),
    Field("movie_file_template", "str", "Filename templates", "Movie file",
          "Placeholders: {title} {year}"),
    Field("tv_show_folder_template", "str", "Filename templates", "Show folder",
          "Placeholders: {show} {year}"),
    Field("tv_season_template", "str", "Filename templates", "Season folder",
          "Placeholders: {season:02d} {show} {year}"),
    Field("tv_episode_template", "str", "Filename templates", "Episode file",
          "Placeholders: {show} {season:02d} {episode:02d} {year}"),
    Field("sync_interval_hours", "int", "Sync", "Auto-sync interval (hours)",
          "Re-sync every active server this often. 0 disables it. Applied "
          "immediately when you save — no restart."),
    Field("tmdb_enabled", "bool", "Metadata source — TMDB", "Use TMDB",
          "Pull posters, plots, genres and IDs from The Movie Database."),
    Field("tmdb_api_key", "str", "Metadata source — TMDB", "TMDB API key or v4 token",
          "themoviedb.org → Settings → API. A v3 key or a v4 read-access token both work."),
    Field("tmdb_language", "str", "Metadata source — TMDB", "Language",
          "e.g. en-US, de-DE, fr-FR."),
)

_BY_KEY = {f.key: f for f in FIELDS}
_KEYS = tuple(f.key for f in FIELDS)


def _coerce(field: Field, raw: str):
    if field.type == "bool":
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if field.type == "int":
        try:
            return int(str(raw).strip())
        except (TypeError, ValueError):
            return getattr(get_settings(), field.key)
    return raw


def load() -> dict[str, str]:
    with SessionLocal() as session:
        return {
            row.key: row.value
            for row in session.scalars(select(Setting))
            if row.key in _BY_KEY
        }


def effective() -> Settings:
    base = get_settings()
    stored = load()
    if not stored:
        return base
    updates = {}
    for key, raw in stored.items():
        field = _BY_KEY[key]
        if field.type == "str" and raw == "":
            continue  # empty string = "use the env/default value"
        updates[key] = _coerce(field, raw)
    return base.model_copy(update=updates) if updates else base


def current_values() -> dict[str, str]:
    """What the Settings form should show: stored value, else the effective default."""
    stored = load()
    eff = get_settings()
    out: dict[str, str] = {}
    for field in FIELDS:
        if field.key in stored:
            out[field.key] = stored[field.key]
        else:
            val = getattr(eff, field.key)
            out[field.key] = "" if field.type == "str" and not val else str(val)
    return out


def save(form: dict[str, str]) -> None:
    with SessionLocal() as session:
        existing = {row.key: row for row in session.scalars(select(Setting))}
        for field in FIELDS:
            if field.key not in form:
                # unchecked checkbox -> not in form data -> store "false"
                raw = "false" if field.type == "bool" else None
                if raw is None:
                    continue
            else:
                raw = str(form[field.key]).strip()
            row = existing.get(field.key)
            if row is None:
                session.add(Setting(key=field.key, value=raw))
            else:
                row.value = raw
        session.commit()


def groups() -> list[tuple[str, list[Field]]]:
    ordered: list[tuple[str, list[Field]]] = []
    for field in FIELDS:
        if not ordered or ordered[-1][0] != field.group:
            ordered.append((field.group, []))
        ordered[-1][1].append(field)
    return ordered
