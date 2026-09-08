"""Turn catalog rows into ``.strm`` / ``.nfo`` files in a Jellyfin/Emby layout.

This module only deals with paths and the filesystem. Building the actual stream
URL from a server's credentials lives in :mod:`app.xtream`; wiring it together
and recording :class:`app.models.Export` rows lives in the exports router.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.config import Settings

_ILLEGAL_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_EMPTY_GROUP_RE = re.compile(r"\(\s*\)|\[\s*\]|\{\s*\}")
_MULTISPACE_RE = re.compile(r"\s{2,}")


def sanitize_component(name: str) -> str:
    """Make a single path segment safe on Linux/macOS/Windows filesystems."""
    cleaned = _ILLEGAL_RE.sub("", name or "").strip().rstrip(". ")
    cleaned = _MULTISPACE_RE.sub(" ", cleaned)
    return cleaned or "Unknown"


def _fmt(template: str, **values) -> str:
    safe = {key: ("" if value is None else value) for key, value in values.items()}
    try:
        out = template.format(**safe)
    except (KeyError, ValueError, IndexError):
        out = str(safe.get("title") or safe.get("show") or "Unknown")
    out = _EMPTY_GROUP_RE.sub("", out)
    out = _MULTISPACE_RE.sub(" ", out).strip(" -._")
    return out


@dataclass(frozen=True)
class MovieTargets:
    strm_rel: PurePosixPath
    nfo_rel: PurePosixPath


@dataclass(frozen=True)
class EpisodeTargets:
    strm_rel: PurePosixPath
    nfo_rel: PurePosixPath
    show_nfo_rel: PurePosixPath


def movie_targets(settings: Settings, *, title: str, year: int | None) -> MovieTargets:
    folder = sanitize_component(_fmt(settings.movie_folder_template, title=title, year=year))
    stem = sanitize_component(_fmt(settings.movie_file_template, title=title, year=year))
    strm_rel = PurePosixPath(settings.movies_dir) / folder / f"{stem}.strm"
    return MovieTargets(strm_rel=strm_rel, nfo_rel=strm_rel.with_suffix(".nfo"))


def episode_targets(
    settings: Settings, *, show: str, year: int | None, season: int, episode: int
) -> EpisodeTargets:
    show_folder = sanitize_component(
        _fmt(settings.tv_show_folder_template, show=show, year=year)
    )
    season_folder = sanitize_component(
        _fmt(settings.tv_season_template, show=show, year=year, season=season, episode=episode)
    )
    stem = sanitize_component(
        _fmt(settings.tv_episode_template, show=show, year=year, season=season, episode=episode)
    )
    show_root = PurePosixPath(settings.tv_dir) / show_folder
    strm_rel = show_root / season_folder / f"{stem}.strm"
    return EpisodeTargets(
        strm_rel=strm_rel,
        nfo_rel=strm_rel.with_suffix(".nfo"),
        show_nfo_rel=show_root / "tvshow.nfo",
    )


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


def resolve_within(root: Path, rel: PurePosixPath | str) -> Path:
    """Resolve ``rel`` under ``root``, refusing anything that escapes it."""
    root = root.resolve()
    candidate = (root / Path(str(rel))).resolve()
    if candidate != root and not candidate.is_relative_to(root):
        raise ValueError(f"path escapes MEDIA_ROOT: {rel}")
    return candidate


def write_file(root: Path, rel: PurePosixPath | str, content: str) -> Path:
    dest = resolve_within(root, rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    return dest


def write_strm(root: Path, rel: PurePosixPath | str, url: str) -> Path:
    return write_file(root, rel, url.strip())


def delete_files(root: Path, rels: Iterable[PurePosixPath | str | None]) -> None:
    """Delete the given files then prune directories left empty, up to ``root``."""
    root = root.resolve()
    touched: set[Path] = set()
    for rel in rels:
        if not rel:
            continue
        dest = resolve_within(root, rel)
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        touched.add(dest.parent)
    for directory in sorted(touched, key=lambda p: len(p.parts), reverse=True):
        _prune_up(directory, root)


def _prune_up(start: Path, root: Path) -> None:
    current = start
    while current != root and current.is_relative_to(root) and current.is_dir():
        if any(current.iterdir()):
            return
        parent = current.parent
        current.rmdir()
        current = parent
