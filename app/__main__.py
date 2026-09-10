"""``python -m app`` → the admin command line (see app/cli.py)."""

from app.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
