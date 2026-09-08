"""Runtime configuration, loaded from environment / .env via pydantic-settings."""

from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_WEAK_KEYS = {"", "changeme", "change-me", "secret", "please-change-me"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- security -----------------------------------------------------------
    secret_key: str = ""
    app_password: str = ""
    dev: bool = False

    # --- storage ----------------------------------------------------------
    data_dir: Path = Path("/app/data")
    # Root for generated .strm / .nfo. In Docker this is /VODS — mount your
    # media library volume there.
    media_root: Path = Path("/VODS")

    # --- library layout -------------------------------------------------
    movies_dir: str = "Movies"
    tv_dir: str = "TV Shows"
    write_nfo: bool = True

    movie_folder_template: str = "{title} ({year})"
    movie_file_template: str = "{title} ({year})"
    tv_show_folder_template: str = "{show} ({year})"
    tv_season_template: str = "Season {season:02d}"
    tv_episode_template: str = "{show} S{season:02d}E{episode:02d}"

    # --- sync / http ----------------------------------------------------
    sync_interval_hours: int = 0
    http_timeout: int = 30
    xc_max_concurrency: int = 5

    # --- metadata (TMDB) ----------------------------------------------
    # Env values are the defaults; the Settings page can override them (stored
    # in the DB). See app/settings_store.py.
    tmdb_enabled: bool = False
    tmdb_api_key: str = ""
    tmdb_language: str = "en-US"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "strmvert.db"

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_password)

    def validate_runtime(self) -> None:
        """Fail fast on an unsafe config unless DEV=1."""
        if self.dev:
            return
        if self.secret_key.strip().lower() in _WEAK_KEYS or len(self.secret_key) < 16:
            sys.exit(
                "SECRET_KEY is missing or too weak. Generate one with:\n"
                '  python -c "import secrets; print(secrets.token_urlsafe(32))"\n'
                "or set DEV=1 for local development."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
