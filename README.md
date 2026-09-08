# StrmVert

Turn one or more **Xtream Codes** IPTV VOD catalogs into a **Jellyfin/Emby `.strm`
library**. Add your panel logins, sync the catalog, browse Movies and TV Shows in
separate tabs, tick what you want, and StrmVert writes `.strm` + `.nfo` files in the
standard media-server folder layout on a mounted volume.

- **Stack:** FastAPI + HTMX + Jinja + SQLite (one container, no Node, no external DB)
- **Catalog source:** the Xtream `player_api.php` (rich metadata + real season/episode
  structure); falls back to parsing `get.php` M3U only if a panel returns nothing.
- **Output:** `.strm` (one stream URL per file) plus a Kodi/Jellyfin `.nfo` sidecar.

---

## Quick start (Docker)

```bash
cp .env.example .env

# generate a key and paste it into .env as SECRET_KEY=
python -c "import secrets; print(secrets.token_urlsafe(32))"

# point MEDIA_ROOT at the library folder Jellyfin/Emby scans (mounted at /VODS).
# leave it as ./VODS to use a folder next to the compose file.
#   MEDIA_ROOT=/srv/media/vods

docker compose up --build
```

Open **http://localhost:8787**.

0. **First run** sends you to a setup screen — create the admin username + password
   (or *Skip* to run with no login on a trusted LAN). Change the password later under
   **Settings → Admin account**. Setting `APP_PASSWORD` in the env skips the wizard
   and uses that single password instead.
1. **Settings → XC Servers → New server** — name, host, port, username, password.
   **Test connection** shows the account status and expiry. Save, then **Sync**
   (a live progress line shows what it's importing).
2. **Movies** / **TV Shows** — browse, search, filter by server/category, and switch
   between **Posters** and **List** view (remembered per tab). Tick titles (on TV,
   expand a show to pick seasons or single episodes). Hit **Create .strm files**.
3. **Exports** — every file StrmVert has written, with **Re-write**, **Delete**
   (removes the file and prunes empty folders) and **Verify on disk**.
4. Point Jellyfin/Emby at `MEDIA_ROOT` and scan. `.strm` items play like normal files.

### Settings page

Everything below `MEDIA_ROOT` is env-seeded but editable at runtime on the **Settings**
tab (stored in the DB, no restart needed):

- **Library layout** — movies / TV folder names, whether to write `.nfo`.
- **Filename templates** — folder and file name patterns.
- **Sync** — auto-sync interval (hours); the recurring job is rescheduled the moment
  you save.
- **Metadata source — TMDB** — enable [The Movie Database](https://www.themoviedb.org/)
  and paste a **v3 API key or a v4 read-access token**. When enabled, opening a
  movie's *info* drawer (or a show's *episodes* drawer) fills in any missing plot,
  genre, rating, poster and IMDb id from TMDB. **Test TMDB key** validates it.

---

## Configuration (`.env`)

| Variable | Default | Notes |
|---|---|---|
| `SECRET_KEY` | — | **Required.** Signs the login cookie and derives the key that encrypts stored XC passwords. Changing it logs everyone out and makes stored passwords unreadable (just re-enter them). |
| `APP_PASSWORD` | *(empty)* | Optional. If set, it's the single login password and the first-run wizard is skipped. If empty, the first run creates an admin account (username + password, hashed, changeable in Settings) — or you can skip it for an open app. |
| `MEDIA_ROOT` | `/VODS` | Where `.strm`/`.nfo` are written. In Docker this is fixed at `/VODS`; the compose file maps your host path (or a named volume) onto it. |
| `MOVIES_DIR` / `TV_DIR` | `Movies` / `TV Shows` | Sub-folders under `MEDIA_ROOT`. |
| `WRITE_NFO` | `true` | Write `.nfo` sidecars next to each `.strm`. |
| `SYNC_INTERVAL_HOURS` | `0` | `0` = manual only. `>0` = re-sync every active server on that interval. |
| `HTTP_TIMEOUT` | `30` | Per-request timeout for panel calls. |
| `XC_MAX_CONCURRENCY` | `5` | Parallel `get_series_info` calls during a **Deep sync**. |
| `MOVIE_FOLDER_TEMPLATE` etc. | see `.env.example` | Path/filename templates. Placeholders: `{title}` `{show}` `{year}` `{season:02d}` `{episode:02d}`. Empty `()` is stripped when there is no year. |
| `TMDB_ENABLED` / `TMDB_API_KEY` / `TMDB_LANGUAGE` | `false` / — / `en-US` | Default TMDB config; override on the Settings page. |
| `PORT` | `8787` | Host port in `docker-compose.yml`. |
| `DEV` | `0` | `1` allows booting without a strong `SECRET_KEY` (local dev only). |

Every row except `SECRET_KEY`, `APP_PASSWORD`, `MEDIA_ROOT`, `DATA_DIR`, `PORT` and
`DEV` can also be changed on the **Settings** page (DB value wins over the env value).

---

## How sync works

- **Sync** (per server, or on the interval) pulls **VOD categories, every movie, and
  every series** in a handful of API calls. It does *not* fetch episode lists here —
  that is one call per series.
- **Episodes** load on demand the first time you expand a show on the TV tab (cached for
  24 h), and always just before exporting a series/season that has none cached.
- **Deep sync** (per server) fetches episode lists for *every* series up front,
  rate-limited by `XC_MAX_CONCURRENCY`. Use it if you want the whole TV catalog
  browsable offline.
- Titles disappearing from a panel are marked **stale**, not deleted — your existing
  exports keep working. Re-adding brings them back.

## Media layout produced

```
MEDIA_ROOT/
├── Movies/
│   └── The Matrix (1999)/
│       ├── The Matrix (1999).strm      # → http://host:port/movie/user/pass/<id>.mkv
│       └── The Matrix (1999).nfo
└── TV Shows/
    └── Cool Show (2020)/
        ├── tvshow.nfo
        └── Season 01/
            ├── Cool Show S01E01.strm   # → http://host:port/series/user/pass/<id>.mkv
            └── Cool Show S01E01.nfo
```

## Security notes

- Panel passwords are stored **Fernet-encrypted** (key derived from `SECRET_KEY`), so the
  SQLite file alone doesn't leak them.
- **A `.strm` file still contains the panel URL with username and password in clear text**
  — that is how `.strm` playback works. Treat `MEDIA_ROOT` as sensitive, and if you
  rotate a panel password, StrmVert marks that server's exports **stale** so you can
  **Re-write stale** on the Exports tab.
- Set `APP_PASSWORD` if the port is reachable by anyone you don't trust.

## Releases & container image

The version lives in the top-level **`VERSION`** file. On every push to `main`,
[`.github/workflows/docker-publish.yml`](.github/workflows/docker-publish.yml)
builds the image and pushes it to **GitHub Container Registry**:

```
ghcr.io/dexdeadly/strmvert:<VERSION>   # e.g. 0.1.0
ghcr.io/dexdeadly/strmvert:latest
ghcr.io/dexdeadly/strmvert:sha-<short>
```

It also creates a `v<VERSION>` git tag + GitHub Release the first time a version is
seen. To cut a release: bump `VERSION`, commit to `main`. To run the published
image instead of building, point `docker-compose.yml` at
`image: ghcr.io/dexdeadly/strmvert:latest` and drop `build: .`.

## Local development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"

export SECRET_KEY=dev-only-key-please-change DEV=1
export DATA_DIR=./data MEDIA_ROOT=./VODS
uvicorn app.main:app --reload --port 8787

ruff check .
pytest
```

## Layout

```
app/
  main.py            FastAPI app, lifespan (db init + optional scheduler), error handling
  config.py          env-driven settings
  settings_store.py  DB overrides for a subset of config; effective() merges env + DB
  db.py              SQLAlchemy engine/session (SQLite, WAL) + additive column shims
  models.py          XCServer, Category, Movie, Series, Episode, Export, Setting
  crypto.py          Fernet encrypt/decrypt for stored panel passwords
  xtream.py          player_api.php client + clean_title() + parse_m3u() + URL builders
  tmdb.py            TMDB client + movie/series enrichment
  sync.py            shallow sync (+ live progress), on-demand episode fetch, deep sync
  strm.py            template→path, .strm/.nfo writers, traversal guard, empty-dir prune
  nfo.py             movie / tvshow / episode NFO XML
  exporter.py        selection tokens → files + Export rows; rewrite / delete / verify
  routers/           auth, settings (+ XC servers), servers (actions), library, exports, health
  templates/         Jinja + HTMX (grid + list partials per tab)
  static/            app.css (black/white/red, dark only), app.js, vendored htmx + alpine
tests/               pytest (+ respx for panel/TMDB mocking)
```
