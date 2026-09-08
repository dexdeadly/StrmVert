"""Symmetric encryption for Xtream Codes passwords stored in the database.

The Fernet key is derived from SECRET_KEY, so the DB file on its own does not
expose panel credentials. Note that generated .strm files still contain the
credentials in the URL — that is inherent to how .strm playback works.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_PREFIX = "enc:v1:"


def _fernet() -> Fernet:
    digest = hashlib.sha256(get_settings().secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt a stored value. Plain (unprefixed) values are returned as-is so
    a hand-seeded row still works; an undecryptable value raises ValueError."""
    if not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX) :].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:  # wrong SECRET_KEY, or corrupted row
        raise ValueError(
            "Cannot decrypt a stored password — SECRET_KEY likely changed. "
            "Re-enter the server credentials."
        ) from exc
