"""SQLAlchemy engine / session wiring (SQLite)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

_settings = get_settings()

try:
    _settings.data_dir.mkdir(parents=True, exist_ok=True)
    _probe = _settings.data_dir / ".write-test"
    _probe.touch()
    _probe.unlink()
except OSError as exc:  # pragma: no cover - environment/perms specific
    raise SystemExit(
        f"StrmVert cannot write to DATA_DIR ({_settings.data_dir}): {exc}\n"
        "In Docker this usually means the mounted ./data directory is owned by a "
        "different user than the container. Fix its ownership on the host, or set "
        '`user: \"<uid>:<gid>\"` in docker-compose.yml to a uid that owns it.'
    ) from exc

engine = create_engine(
    _settings.database_url,
    connect_args={"check_same_thread": False},
    future=True,
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    """Create tables that don't exist yet. Called once on startup."""
    from app import models  # noqa: F401  (register mappers)

    Base.metadata.create_all(engine)
    _ensure_columns()


# Lightweight additive "migrations" (no Alembic). Add columns introduced after a
# user's database was first created. Column -> SQLite type.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "xc_server": {"last_sync_message": "VARCHAR(255)"},
}


def _ensure_columns() -> None:
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            present = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in present:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def get_session() -> Iterator[Session]:
    """FastAPI dependency: a request-scoped session."""
    with SessionLocal() as session:
        yield session
