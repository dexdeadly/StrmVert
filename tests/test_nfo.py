import xml.etree.ElementTree as ET

from app.nfo import episode_nfo, movie_nfo, tvshow_nfo


def test_movie_nfo_is_well_formed_with_ids():
    xml = movie_nfo(
        title="The Matrix", year=1999, plot="A hacker learns the truth.",
        genre="Action, Sci-Fi", rating="8.7", tmdb_id="603", imdb_id="tt0133093",
    )
    root = ET.fromstring(xml)
    assert root.tag == "movie"
    assert root.findtext("title") == "The Matrix"
    assert root.findtext("year") == "1999"
    assert {e.get("type"): e.text for e in root.findall("uniqueid")} == {
        "tmdb": "603",
        "imdb": "tt0133093",
    }
    assert [e.text for e in root.findall("genre")] == ["Action", "Sci-Fi"]


def test_movie_nfo_omits_absent_fields():
    root = ET.fromstring(movie_nfo(title="Bare"))
    assert root.findtext("title") == "Bare"
    assert root.find("year") is None
    assert root.find("uniqueid") is None


def test_tvshow_and_episode_nfo():
    show = ET.fromstring(tvshow_nfo(title="Cool Show", year=2020, tmdb_id="42"))
    assert show.tag == "tvshow"
    assert show.findtext("title") == "Cool Show"

    ep = ET.fromstring(episode_nfo(title="Pilot", season=1, episode=1, plot="It begins."))
    assert ep.tag == "episodedetails"
    assert ep.findtext("season") == "1"
    assert ep.findtext("episode") == "1"
    assert ep.findtext("title") == "Pilot"
