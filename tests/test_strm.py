import pytest

from app.strm import (
    delete_files,
    episode_targets,
    movie_targets,
    resolve_within,
    sanitize_component,
    write_strm,
)


def test_sanitize_component_strips_illegal():
    assert sanitize_component('A/B: "C" ?') == "AB C"
    assert sanitize_component("   ") == "Unknown"
    assert sanitize_component("trailing dots...") == "trailing dots"


def test_movie_targets_with_year(settings):
    t = movie_targets(settings, title="The Matrix", year=1999)
    assert str(t.strm_rel) == "Movies/The Matrix (1999)/The Matrix (1999).strm"
    assert str(t.nfo_rel) == "Movies/The Matrix (1999)/The Matrix (1999).nfo"


def test_movie_targets_without_year_drops_empty_parens(settings):
    t = movie_targets(settings, title="Nameless Doc", year=None)
    assert str(t.strm_rel) == "Movies/Nameless Doc/Nameless Doc.strm"


def test_episode_targets_layout(settings):
    t = episode_targets(settings, show="Cool Show", year=2020, season=1, episode=4)
    assert str(t.strm_rel) == "TV Shows/Cool Show (2020)/Season 01/Cool Show S01E04.strm"
    assert str(t.show_nfo_rel) == "TV Shows/Cool Show (2020)/tvshow.nfo"


def test_resolve_within_blocks_traversal(settings, media_root):
    with pytest.raises(ValueError):
        resolve_within(media_root, "../escape.strm")
    with pytest.raises(ValueError):
        resolve_within(media_root, "Movies/../../etc/passwd")


def test_write_then_delete_prunes_empty_dirs(settings, media_root):
    t = movie_targets(settings, title="Prune Me", year=2001)
    dest = write_strm(media_root, t.strm_rel, "http://x/movie/u/p/1.mp4")
    assert dest.read_text().strip() == "http://x/movie/u/p/1.mp4"
    assert dest.parent.is_dir()

    delete_files(media_root, [t.strm_rel, t.nfo_rel])
    assert not dest.exists()
    assert not dest.parent.exists()  # "Prune Me (2001)" folder removed
    assert not (media_root / "Movies").exists()  # and the now-empty Movies dir
