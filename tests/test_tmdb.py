from types import SimpleNamespace

from app.tmdb import _looks_like_v4_token, apply_movie, apply_series, poster_url


def test_poster_url():
    assert poster_url("/abc.jpg") == "https://image.tmdb.org/t/p/w500/abc.jpg"
    assert poster_url(None) is None


def test_token_detection():
    assert _looks_like_v4_token("eyJhbGciOi." + "x" * 60 + ".sig")
    assert not _looks_like_v4_token("0123456789abcdef0123456789abcdef")


def test_apply_movie_fills_blanks_only():
    m = SimpleNamespace(
        tmdb_id=None, imdb_id=None, plot=None, genre=None, rating="9.9",
        poster_url=None, year=None,
    )
    apply_movie(m, {
        "id": 603,
        "overview": "Neo learns the truth.",
        "genres": [{"name": "Action"}, {"name": "Science Fiction"}],
        "vote_average": 8.7,
        "poster_path": "/matrix.jpg",
        "release_date": "1999-03-31",
        "external_ids": {"imdb_id": "tt0133093"},
    })
    assert m.tmdb_id == "603"
    assert m.imdb_id == "tt0133093"
    assert m.plot == "Neo learns the truth."
    assert m.genre == "Action, Science Fiction"
    assert m.rating == "9.9"  # not overwritten
    assert m.poster_url == "https://image.tmdb.org/t/p/w500/matrix.jpg"
    assert m.year == 1999


def test_apply_series_uses_first_air_date():
    s = SimpleNamespace(
        tmdb_id=None, imdb_id=None, plot=None, genre=None, rating=None,
        poster_url="keep.jpg", year=None,
    )
    apply_series(s, {"id": 1, "first_air_date": "2015-06-01", "overview": "x"})
    assert s.year == 2015
    assert s.poster_url == "keep.jpg"  # not overwritten
