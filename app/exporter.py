"""Write / rewrite / delete / verify ``.strm`` + ``.nfo`` exports.

The library router hands us selection tokens; we resolve them to Movie/Episode
rows (fetching series episodes on demand), write files under ``MEDIA_ROOT`` and
keep an :class:`Export` row per file so the Exports tab can manage them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.crypto import decrypt
from app.models import Episode, Export, Movie
from app.nfo import episode_nfo, movie_nfo, tvshow_nfo
from app.settings_store import effective as effective_settings
from app.strm import (
    _prune_up,
    delete_files,
    episode_targets,
    movie_targets,
    resolve_within,
    write_file,
    write_strm,
)
from app.sync import ensure_series_episodes
from app.xtream import build_episode_url, build_movie_url

log = logging.getLogger("strmvert.exporter")


@dataclass
class ExportOutcome:
    created: int = 0
    updated: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def written(self) -> int:
        return self.created + self.updated


# ---------------------------------------------------------------------------
# selection resolution
# ---------------------------------------------------------------------------


async def resolve_selections(
    session: Session, tokens: list[str]
) -> tuple[list[Movie], list[Episode], list[str]]:
    movie_ids: set[int] = set()
    episode_ids: set[int] = set()
    series_all: set[int] = set()
    series_season: set[tuple[int, int]] = set()
    errors: list[str] = []

    for token in tokens:
        head, _, rest = token.partition(":")
        if head == "m":
            movie_ids.add(int(rest))
        elif head == "e":
            episode_ids.add(int(rest))
        elif head == "s":
            sid, _, season = rest.partition(":")
            if season:
                series_season.add((int(sid), int(season)))
            else:
                series_all.add(int(sid))

    # Make sure episodes are cached for every series we were asked to expand.
    for sid in {s for s, _ in series_season} | series_all:
        try:
            await ensure_series_episodes(sid)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"series {sid}: {exc}")

    for sid in series_all:
        episode_ids.update(
            session.scalars(select(Episode.id).where(Episode.series_id == sid))
        )
    for sid, season in series_season:
        episode_ids.update(
            session.scalars(
                select(Episode.id).where(Episode.series_id == sid, Episode.season == season)
            )
        )

    movies = (
        list(session.scalars(select(Movie).where(Movie.id.in_(movie_ids)))) if movie_ids else []
    )
    episodes = (
        list(session.scalars(select(Episode).where(Episode.id.in_(episode_ids))))
        if episode_ids
        else []
    )
    return movies, episodes, errors


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _movie_display(movie: Movie) -> str:
    return f"{movie.title_clean or movie.name} ({movie.year})" if movie.year else (
        movie.title_clean or movie.name
    )


def _episode_display(ep: Episode) -> str:
    show = ep.series.title_clean or ep.series.name
    return f"{show} S{ep.season:02d}E{ep.episode:02d}"


def _upsert_export(
    session: Session,
    *,
    kind: str,
    server_id: int,
    title: str,
    target_rel: str,
    nfo_rel: str | None,
    url: str,
    movie_id: int | None = None,
    episode_id: int | None = None,
) -> bool:
    """Returns True if a new row was created.

    Flushes the insert immediately: two selections in the same batch that
    collide on ``target_path`` (e.g. the same title from two XC servers) must
    upsert onto one row rather than both hitting the unique constraint at the
    end-of-request commit.
    """
    row = session.scalar(select(Export).where(Export.target_path == target_rel))
    if row is None:
        session.add(
            Export(
                kind=kind,
                server_id=server_id,
                movie_id=movie_id,
                episode_id=episode_id,
                title=title,
                target_path=target_rel,
                nfo_path=nfo_rel,
                stream_url_snapshot=url,
                status="written",
            )
        )
        session.flush()
        return True
    row.kind = kind
    row.server_id = server_id
    row.movie_id = movie_id
    row.episode_id = episode_id
    row.title = title
    row.nfo_path = nfo_rel
    row.stream_url_snapshot = url
    row.status = "written"
    row.error = None
    return False


def export_movie(session: Session, settings: Settings, movie: Movie) -> bool:
    server = movie.server
    password = decrypt(server.password_enc)
    title = movie.title_clean or movie.name
    targets = movie_targets(settings, title=title, year=movie.year)
    url = build_movie_url(
        base_url=server.base_url,
        username=server.username,
        password=password,
        xc_stream_id=movie.xc_stream_id,
        ext=movie.container_ext,
    )
    write_strm(settings.media_root, targets.strm_rel, url)
    nfo_rel: str | None = None
    if settings.write_nfo:
        write_file(
            settings.media_root,
            targets.nfo_rel,
            movie_nfo(
                title=title,
                year=movie.year,
                plot=movie.plot,
                genre=movie.genre,
                rating=movie.rating,
                tmdb_id=movie.tmdb_id,
                imdb_id=movie.imdb_id,
            ),
        )
        nfo_rel = str(targets.nfo_rel)
    return _upsert_export(
        session,
        kind="movie",
        server_id=server.id,
        movie_id=movie.id,
        title=_movie_display(movie),
        target_rel=str(targets.strm_rel),
        nfo_rel=nfo_rel,
        url=url,
    )


def export_episode(session: Session, settings: Settings, episode: Episode) -> bool:
    series = episode.series
    server = series.server
    password = decrypt(server.password_enc)
    show = series.title_clean or series.name
    targets = episode_targets(
        settings, show=show, year=series.year, season=episode.season, episode=episode.episode
    )
    url = build_episode_url(
        base_url=server.base_url,
        username=server.username,
        password=password,
        xc_episode_id=episode.xc_episode_id,
        ext=episode.container_ext,
    )
    write_strm(settings.media_root, targets.strm_rel, url)
    nfo_rel: str | None = None
    if settings.write_nfo:
        write_file(
            settings.media_root,
            targets.nfo_rel,
            episode_nfo(
                title=episode.title,
                season=episode.season,
                episode=episode.episode,
                plot=episode.plot,
                rating=episode.rating,
            ),
        )
        write_file(
            settings.media_root,
            targets.show_nfo_rel,
            tvshow_nfo(
                title=show,
                year=series.year,
                plot=series.plot,
                genre=series.genre,
                rating=series.rating,
                tmdb_id=series.tmdb_id,
                imdb_id=series.imdb_id,
            ),
        )
        nfo_rel = str(targets.nfo_rel)
    return _upsert_export(
        session,
        kind="episode",
        server_id=server.id,
        episode_id=episode.id,
        title=_episode_display(episode),
        target_rel=str(targets.strm_rel),
        nfo_rel=nfo_rel,
        url=url,
    )


def export_many(
    session: Session, movies: list[Movie], episodes: list[Episode], settings: Settings | None = None
) -> ExportOutcome:
    settings = settings or effective_settings()
    outcome = ExportOutcome()
    for movie in movies:
        try:
            created = export_movie(session, settings, movie)
            outcome.created += int(created)
            outcome.updated += int(not created)
        except Exception as exc:  # noqa: BLE001
            outcome.failed += 1
            outcome.errors.append(f"{_movie_display(movie)}: {exc}")
            log.exception("movie export failed: %s", movie.id)
    for episode in episodes:
        try:
            created = export_episode(session, settings, episode)
            outcome.created += int(created)
            outcome.updated += int(not created)
        except Exception as exc:  # noqa: BLE001
            outcome.failed += 1
            outcome.errors.append(f"{_episode_display(episode)}: {exc}")
            log.exception("episode export failed: %s", episode.id)
    session.commit()
    return outcome


# ---------------------------------------------------------------------------
# manage existing exports
# ---------------------------------------------------------------------------


def rewrite_export(session: Session, export: Export, settings: Settings | None = None) -> None:
    settings = settings or effective_settings()
    if export.kind == "movie" and export.movie is not None:
        export_movie(session, settings, export.movie)
    elif export.kind == "episode" and export.episode is not None:
        export_episode(session, settings, export.episode)
    else:
        export.status = "error"
        export.error = "source catalog row is gone — re-sync the server"
    session.commit()


def delete_export(session: Session, export: Export, settings: Settings | None = None) -> None:
    settings = settings or effective_settings()
    delete_files(settings.media_root, [export.target_path, export.nfo_path])
    if export.kind == "episode":
        _prune_orphan_show_nfo(settings, export)
    session.delete(export)
    session.commit()


def delete_exports_bulk(
    session: Session, exports: list[Export], settings: Settings | None = None
) -> int:
    """Delete many export rows + their files in one pass (one commit)."""
    settings = settings or effective_settings()
    show_dirs: set[Path] = set()
    for export in exports:
        delete_files(settings.media_root, [export.target_path, export.nfo_path])
        if export.kind == "episode":
            try:
                strm_abs = resolve_within(settings.media_root, export.target_path)
                show_dirs.add(strm_abs.parent.parent)
            except ValueError:
                pass
        session.delete(export)
    session.commit()
    for show_dir in show_dirs:
        _prune_show_dir(settings, show_dir)
    return len(exports)


def delete_series_exports(
    session: Session, series_id: int, settings: Settings | None = None
) -> int:
    """Delete every export (all episodes) belonging to one series in one pass."""
    exports = list(
        session.scalars(
            select(Export)
            .join(Episode, Export.episode_id == Episode.id)
            .where(Episode.series_id == series_id)
        )
    )
    return delete_exports_bulk(session, exports, settings)


def _prune_show_dir(settings: Settings, show_dir: Path) -> None:
    """If deleting episodes left only a stray tvshow.nfo behind, drop it too."""
    if not show_dir.is_dir():
        return
    survivors = [p for p in show_dir.rglob("*") if p.is_file()]
    if survivors and all(p.name == "tvshow.nfo" for p in survivors):
        for p in survivors:
            p.unlink(missing_ok=True)
        _prune_up(show_dir, settings.media_root.resolve())


def _prune_orphan_show_nfo(settings: Settings, export: Export) -> None:
    """If deleting the last episode left only a stray tvshow.nfo, drop it too."""
    try:
        strm_abs = resolve_within(settings.media_root, export.target_path)
    except ValueError:
        return
    _prune_show_dir(settings, strm_abs.parent.parent)  # …/Show (Year)/Season NN/file → Show (Year)


def verify_exports(session: Session, settings: Settings | None = None) -> dict[str, int]:
    settings = settings or effective_settings()
    stats = {"written": 0, "missing": 0}
    for export in session.scalars(select(Export)):
        try:
            exists = resolve_within(settings.media_root, export.target_path).is_file()
        except ValueError:
            exists = False
        if exists:
            if export.status == "missing":
                export.status = "written"
            stats["written"] += 1
        else:
            export.status = "missing"
            stats["missing"] += 1
    session.commit()
    return stats


def mark_server_exports_stale(session: Session, server_id: int) -> int:
    rows = list(session.scalars(select(Export).where(Export.server_id == server_id)))
    for row in rows:
        if row.status == "written":
            row.status = "stale"
    session.commit()
    return len(rows)
