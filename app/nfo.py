"""Kodi/Jellyfin/Emby ``.nfo`` sidecar generation.

Only the fields an Xtream panel realistically provides are emitted; the media
server fills in the rest from its own metadata providers, keyed off the title,
year and any ``<uniqueid>`` we can supply.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def _text(parent: ET.Element, tag: str, value) -> None:
    if value is None:
        return
    value = str(value).strip()
    if not value:
        return
    ET.SubElement(parent, tag).text = value


def _uniqueids(parent: ET.Element, tmdb_id: str | None, imdb_id: str | None) -> None:
    if tmdb_id:
        el = ET.SubElement(parent, "uniqueid", {"type": "tmdb", "default": "true"})
        el.text = str(tmdb_id)
    if imdb_id:
        el = ET.SubElement(parent, "uniqueid", {"type": "imdb"})
        el.text = str(imdb_id)


def _render(root: ET.Element) -> str:
    ET.indent(root, space="  ")
    return _DECL + ET.tostring(root, encoding="unicode") + "\n"


def movie_nfo(
    *,
    title: str,
    year: int | None = None,
    plot: str | None = None,
    genre: str | None = None,
    rating: str | None = None,
    tmdb_id: str | None = None,
    imdb_id: str | None = None,
) -> str:
    root = ET.Element("movie")
    _text(root, "title", title)
    _text(root, "year", year)
    _text(root, "plot", plot)
    for name in _split_genres(genre):
        _text(root, "genre", name)
    _text(root, "rating", rating)
    _uniqueids(root, tmdb_id, imdb_id)
    return _render(root)


def tvshow_nfo(
    *,
    title: str,
    year: int | None = None,
    plot: str | None = None,
    genre: str | None = None,
    rating: str | None = None,
    tmdb_id: str | None = None,
    imdb_id: str | None = None,
) -> str:
    root = ET.Element("tvshow")
    _text(root, "title", title)
    _text(root, "year", year)
    _text(root, "plot", plot)
    for name in _split_genres(genre):
        _text(root, "genre", name)
    _text(root, "rating", rating)
    _uniqueids(root, tmdb_id, imdb_id)
    return _render(root)


def episode_nfo(
    *,
    title: str | None,
    season: int,
    episode: int,
    plot: str | None = None,
    rating: str | None = None,
) -> str:
    root = ET.Element("episodedetails")
    _text(root, "title", title or f"Episode {episode}")
    _text(root, "season", season)
    _text(root, "episode", episode)
    _text(root, "plot", plot)
    _text(root, "rating", rating)
    return _render(root)


def _split_genres(genre: str | None) -> list[str]:
    if not genre:
        return []
    parts = [p.strip() for chunk in genre.split(",") for p in chunk.split("/")]
    return [p for p in parts if p]
