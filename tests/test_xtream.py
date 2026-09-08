from app.xtream import build_episode_url, build_movie_url, clean_title, parse_m3u


class TestCleanTitle:
    def test_parenthetical_year(self):
        assert clean_title("The Matrix (1999)") == ("The Matrix", 1999)

    def test_dotted_with_quality(self):
        assert clean_title("The.Matrix.1999.1080p.BluRay.x264") == ("The Matrix", 1999)

    def test_bracket_year_and_lang_prefix(self):
        assert clean_title("EN - Blade Runner [1982] 4K") == ("Blade Runner", 1982)

    def test_no_year(self):
        title, year = clean_title("Random Documentary")
        assert title == "Random Documentary"
        assert year is None

    def test_keeps_hyphenated_names(self):
        assert clean_title("Spider-Man (2002)") == ("Spider-Man", 2002)

    def test_falls_back_to_raw_when_scrubbed_empty(self):
        assert clean_title("2019")[0] == "2019"


class TestBuildUrls:
    def test_movie_url(self):
        url = build_movie_url(
            base_url="http://box.tv:8080", username="u", password="p",
            xc_stream_id="123", ext="mkv",
        )
        assert url == "http://box.tv:8080/movie/u/p/123.mkv"

    def test_episode_url_defaults_ext(self):
        url = build_episode_url(
            base_url="https://box.tv:443", username="u", password="p",
            xc_episode_id="55", ext="",
        )
        assert url == "https://box.tv:443/series/u/p/55.mp4"


class TestParseM3U:
    SAMPLE = "\n".join(
        [
            "#EXTM3U",
            '#EXTINF:-1 tvg-name="Live News" group-title="News",Live News',
            "http://box.tv:8080/u/p/9001",
            '#EXTINF:-1 tvg-name="The Matrix (1999)" tvg-logo="http://img/mtx.jpg"'
            ' group-title="Movies",The Matrix (1999)',
            "http://box.tv:8080/movie/u/p/123.mkv",
            '#EXTINF:-1 tvg-name="Some Show S01E02" group-title="Series",Some Show S01E02',
            "http://box.tv:8080/series/u/p/456.mp4",
        ]
    )

    def test_drops_live_keeps_vod(self):
        items = parse_m3u(self.SAMPLE)
        kinds = sorted(i["kind"] for i in items)
        assert kinds == ["movie", "series"]

    def test_movie_fields(self):
        movie = next(i for i in parse_m3u(self.SAMPLE) if i["kind"] == "movie")
        assert movie["stream_id"] == "123"
        assert movie["container_extension"] == "mkv"
        assert movie["name"] == "The Matrix (1999)"
        assert movie["logo"] == "http://img/mtx.jpg"

    def test_series_season_episode(self):
        ep = next(i for i in parse_m3u(self.SAMPLE) if i["kind"] == "series")
        assert ep["stream_id"] == "456"
        assert ep["season"] == 1
        assert ep["episode"] == 2
