"""StrmVert admin command line — ``python -m app <command>``.

The one command so far is ``reset-password``: the out-of-band recovery path for
a locked-out admin. There is no email on a self-hosted box, so a forgotten
password is fixed from the server shell:

    docker compose exec strmvert python -m app reset-password

It prompts for the new password (or takes ``--password`` / a value piped on
stdin), then writes the new hash straight into the admin account. If no admin
account exists yet it creates one.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app import __version__
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.security import MIN_PASSWORD_LEN, create_admin, get_admin, hash_password


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


def _prompt_new_password() -> str:
    while True:
        pw = getpass.getpass("New password: ")
        if len(pw) < MIN_PASSWORD_LEN:
            _err(f"  too short — at least {MIN_PASSWORD_LEN} characters")
            continue
        if pw != getpass.getpass("Confirm password: "):
            _err("  the two entries don't match")
            continue
        return pw


def _resolve_password(given: str | None) -> str | None:
    """--password wins; otherwise read stdin if it's piped, else prompt on a tty."""
    if given is not None:
        return given
    if not sys.stdin.isatty():
        piped = sys.stdin.readline().strip()
        return piped or None
    return _prompt_new_password()


def reset_password(args: argparse.Namespace) -> int:
    if get_settings().app_password:
        _err(
            "Login is set by the APP_PASSWORD environment variable, so there is no\n"
            "stored account to reset. Change APP_PASSWORD where you set it\n"
            "(docker-compose.yml / .env) and restart."
        )
        return 2

    init_db()  # make sure the tables exist (fresh data volume)

    password = _resolve_password(args.password)
    if not password:
        _err("No password given (expected a prompt, --password, or a value on stdin).")
        return 2
    if len(password) < MIN_PASSWORD_LEN:
        _err(f"Password must be at least {MIN_PASSWORD_LEN} characters.")
        return 2

    wanted_username = (args.username or "").strip()
    if wanted_username and len(wanted_username) < 3:
        _err("Username must be at least 3 characters.")
        return 2

    with SessionLocal() as session:
        admin = get_admin(session)
        if admin is None:
            username = wanted_username or "admin"
            create_admin(session, username, password)
            print(f"No admin account existed — created '{username}' with the new password.")
            return 0

        admin.password_hash = hash_password(password)
        if wanted_username and wanted_username != admin.username:
            print(f"Renamed admin '{admin.username}' -> '{wanted_username}'.")
            admin.username = wanted_username
        session.commit()
        print(f"Password reset for admin '{admin.username}'. Sign in with the new password.")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app", description="StrmVert admin command line."
    )
    parser.add_argument("--version", action="version", version=f"StrmVert {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    rp = sub.add_parser(
        "reset-password",
        help="Reset the admin password (recovery for a locked-out admin).",
        description=(
            "Set a new password for the admin account, straight in the database — "
            "use it when you're locked out. With no --password it prompts; it also "
            "accepts a password piped on stdin. Creates the admin account if none exists."
        ),
    )
    rp.add_argument(
        "-u", "--username",
        help="Also set this username (default: keep the current one, or 'admin' if none).",
    )
    rp.add_argument(
        "-p", "--password",
        help="The new password (omit to be prompted — keeps it out of shell history).",
    )
    rp.set_defaults(func=reset_password)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
