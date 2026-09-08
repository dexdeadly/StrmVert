"""StrmVert — Xtream Codes VOD catalogs into Jellyfin/Emby .strm libraries."""

from __future__ import annotations

import os
from pathlib import Path


def _detect_version() -> str:
    # 1. explicit build-time override (set by the Docker image)
    env = os.environ.get("STRMVERT_VERSION")
    if env:
        return env.strip()
    # 2. the VERSION file, running from a source checkout
    for candidate in (
        Path.cwd() / "VERSION",
        Path(__file__).resolve().parent.parent / "VERSION",
    ):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
            if text:
                return text
        except OSError:
            pass
    # 3. installed package metadata (baked from VERSION at build time)
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("strmvert")
    except (ImportError, PackageNotFoundError):
        return "0.0.0+unknown"


__version__ = _detect_version()
