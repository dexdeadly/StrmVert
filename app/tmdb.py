"""The Movie Database (TMDB) client + enrichment helpers.

Used on demand when the Settings page has TMDB enabled and a key set — fills in
plot / genre / rating / poster / IMDb id that the IPTV panel didn't provide.
Accepts either a v3 API key (sent as ``?api_key=``) or a v4 read-access token
(sent as a Bearer header).
"""

from __future__ import annotations

import httpx

_BASE = "https://api.themoviedb.org/3"
_IMG = "https://image.tmdb.org/t/p/w500"


class TMDBError(RuntimeError):
    pass


def poster_url(path: str | None) -> str | None:
    return f"{_IMG}{path}" if path else None


def _looks_like_v4_token(key: str) -> bool:
    return key.count(".") == 2 and len(key) > 40


class TMDBClient:
    def __init__(
        self,
        api_key: str,
        *,
        language: str = "en-US",
        timeout: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.language = language or "en-US"
        headers = {"Accept": "application/json"}
        self._auth_param: dict[str, str] = {}
        if _looks_like_v4_token(self.api_key):
            headers["Authorization"] = f"Bearer {self.api_key}"
        else:
            self._auth_param = {"api_key": self.api_key}
        self._owns = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, headers=headers)

    async def __aenter__(self) -> TMDBClient:
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns:
            await self._client.aclose()

    async def _get(self, path: str, **params) -> dict:
        query = {**self._auth_param, "language": self.language, **params}
        try:
            resp = await self._client.get(f"{_BASE}{path}", params=query)
        except httpx.HTTPError as exc:
            raise TMDBError(f"TMDB request failed: {exc}") from exc
        if resp.status_code == 401:
            raise TMDBError("TMDB rejected the API key / token.")
        if resp.status_code != 200:
            raise TMDBError(f"TMDB returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise TMDBError("TMDB returned a non-JSON response") from exc

    async def validate(self) -> None:
        """Raise TMDBError if the credentials are not usable."""
        await self._get("/configuration")

    async def find(self, kind: str, title: str, year: int | None) -> dict | None:
        endpoint = "/search/movie" if kind == "movie" else "/search/tv"
        params: dict[str, str | int] = {"query": title, "include_adult": "false"}
        if year:
            params["year" if kind == "movie" else "first_air_date_year"] = year
        results = (await self._get(endpoint, **params)).get("results") or []
        return results[0] if results else None

    async def details(self, kind: str, tmdb_id: str | int) -> dict:
        base = "/movie/" if kind == "movie" else "/tv/"
        return await self._get(f"{base}{tmdb_id}", append_to_response="external_ids")


def _genres_to_str(details: dict) -> str | None:
    names = [g.get("name") for g in details.get("genres") or [] if g.get("name")]
    return ", ".join(names) or None


def _imdb_from(details: dict) -> str | None:
    imdb = details.get("imdb_id") or (details.get("external_ids") or {}).get("imdb_id")
    return imdb if imdb and str(imdb).startswith("tt") else None


def apply_movie(movie, details: dict) -> None:
    movie.tmdb_id = movie.tmdb_id or (str(details["id"]) if details.get("id") else None)
    movie.imdb_id = movie.imdb_id or _imdb_from(details)
    movie.plot = movie.plot or (details.get("overview") or None)
    movie.genre = movie.genre or _genres_to_str(details)
    if details.get("vote_average"):
        movie.rating = movie.rating or f"{details['vote_average']:.1f}"
    movie.poster_url = movie.poster_url or poster_url(details.get("poster_path"))
    date = details.get("release_date") or ""
    if not movie.year and len(date) >= 4 and date[:4].isdigit():
        movie.year = int(date[:4])


def apply_series(series, details: dict) -> None:
    series.tmdb_id = series.tmdb_id or (str(details["id"]) if details.get("id") else None)
    series.imdb_id = series.imdb_id or _imdb_from(details)
    series.plot = series.plot or (details.get("overview") or None)
    series.genre = series.genre or _genres_to_str(details)
    if details.get("vote_average"):
        series.rating = series.rating or f"{details['vote_average']:.1f}"
    series.poster_url = series.poster_url or poster_url(details.get("poster_path"))
    date = details.get("first_air_date") or ""
    if not series.year and len(date) >= 4 and date[:4].isdigit():
        series.year = int(date[:4])


async def enrich(kind: str, row, api_key: str, language: str) -> bool:
    """Fetch TMDB details for a Movie/Series row and fill in the blanks.

    Returns True if anything network-y ran (the caller should commit).
    """
    async with TMDBClient(api_key, language=language) as client:
        tmdb_id = row.tmdb_id
        if not tmdb_id:
            hit = await client.find(kind, row.title_clean or row.name, row.year)
            if not hit:
                return False
            tmdb_id = hit.get("id")
        details = await client.details(kind, tmdb_id)
    (apply_movie if kind == "movie" else apply_series)(row, details)
    return True
