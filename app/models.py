"""Database models."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _utcnow() -> datetime:
    """Naive UTC. SQLite has no tz storage, so the whole app stays naive-UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


class XCServer(Base):
    __tablename__ = "xc_server"
    __table_args__ = (UniqueConstraint("host", "port", "username", name="uq_server_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    scheme: Mapped[str] = mapped_column(String(8), default="http")
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=80)
    username: Mapped[str] = mapped_column(String(255))
    password_enc: Mapped[str] = mapped_column(String(512))

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_syncing: Mapped[bool] = mapped_column(Boolean, default=False)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_sync_status: Mapped[str | None] = mapped_column(String(16))  # ok | error | running
    last_sync_error: Mapped[str | None] = mapped_column(Text)
    last_sync_message: Mapped[str | None] = mapped_column(String(255))  # live progress line
    movie_count: Mapped[int] = mapped_column(Integer, default=0)
    series_count: Mapped[int] = mapped_column(Integer, default=0)
    account_expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    movies: Mapped[list[Movie]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    series: Mapped[list[Series]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )
    categories: Mapped[list[Category]] = relationship(
        back_populates="server", cascade="all, delete-orphan"
    )

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class Category(Base):
    __tablename__ = "category"
    __table_args__ = (
        UniqueConstraint("server_id", "kind", "xc_category_id", name="uq_category_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("xc_server.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(8))  # movie | series
    xc_category_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))

    server: Mapped[XCServer] = relationship(back_populates="categories")


class Movie(Base):
    __tablename__ = "movie"
    __table_args__ = (
        UniqueConstraint("server_id", "xc_stream_id", name="uq_movie_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("xc_server.id", ondelete="CASCADE"), index=True
    )
    xc_stream_id: Mapped[str] = mapped_column(String(64))

    name: Mapped[str] = mapped_column(String(500))
    title_clean: Mapped[str] = mapped_column(String(500), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    tmdb_id: Mapped[str | None] = mapped_column(String(32))
    imdb_id: Mapped[str | None] = mapped_column(String(32))
    plot: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[str | None] = mapped_column(String(16))
    poster_url: Mapped[str | None] = mapped_column(String(1000))
    container_ext: Mapped[str] = mapped_column(String(16), default="mp4")
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    category_name: Mapped[str | None] = mapped_column(String(255), index=True)
    added_at: Mapped[datetime | None] = mapped_column(DateTime)

    raw_json: Mapped[str | None] = mapped_column(Text)
    enriched: Mapped[bool] = mapped_column(Boolean, default=False)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    server: Mapped[XCServer] = relationship(back_populates="movies")
    exports: Mapped[list[Export]] = relationship(
        back_populates="movie", cascade="all, delete-orphan"
    )


class Series(Base):
    __tablename__ = "series"
    __table_args__ = (
        UniqueConstraint("server_id", "xc_series_id", name="uq_series_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    server_id: Mapped[int] = mapped_column(
        ForeignKey("xc_server.id", ondelete="CASCADE"), index=True
    )
    xc_series_id: Mapped[str] = mapped_column(String(64))

    name: Mapped[str] = mapped_column(String(500))
    title_clean: Mapped[str] = mapped_column(String(500), index=True)
    year: Mapped[int | None] = mapped_column(Integer, index=True)
    tmdb_id: Mapped[str | None] = mapped_column(String(32))
    imdb_id: Mapped[str | None] = mapped_column(String(32))
    plot: Mapped[str | None] = mapped_column(Text)
    genre: Mapped[str | None] = mapped_column(String(255))
    rating: Mapped[str | None] = mapped_column(String(16))
    poster_url: Mapped[str | None] = mapped_column(String(1000))
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    category_name: Mapped[str | None] = mapped_column(String(255), index=True)
    last_modified: Mapped[datetime | None] = mapped_column(DateTime)

    raw_json: Mapped[str | None] = mapped_column(Text)
    episodes_synced_at: Mapped[datetime | None] = mapped_column(DateTime)
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    server: Mapped[XCServer] = relationship(back_populates="series")
    episodes: Mapped[list[Episode]] = relationship(
        back_populates="series",
        cascade="all, delete-orphan",
        order_by="Episode.season, Episode.episode",
    )


class Episode(Base):
    __tablename__ = "episode"
    __table_args__ = (
        UniqueConstraint("series_id", "season", "episode", name="uq_episode_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("series.id", ondelete="CASCADE"), index=True)
    xc_episode_id: Mapped[str] = mapped_column(String(64))

    season: Mapped[int] = mapped_column(Integer)
    episode: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(500))
    plot: Mapped[str | None] = mapped_column(Text)
    rating: Mapped[str | None] = mapped_column(String(16))
    container_ext: Mapped[str] = mapped_column(String(16), default="mp4")
    added_at: Mapped[datetime | None] = mapped_column(DateTime)
    raw_json: Mapped[str | None] = mapped_column(Text)

    series: Mapped[Series] = relationship(back_populates="episodes")
    exports: Mapped[list[Export]] = relationship(
        back_populates="episode", cascade="all, delete-orphan"
    )


class Export(Base):
    __tablename__ = "export"
    __table_args__ = (
        UniqueConstraint("target_path", name="uq_export_path"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # movie | episode
    server_id: Mapped[int] = mapped_column(ForeignKey("xc_server.id", ondelete="CASCADE"))
    movie_id: Mapped[int | None] = mapped_column(
        ForeignKey("movie.id", ondelete="CASCADE"), index=True
    )
    episode_id: Mapped[int | None] = mapped_column(
        ForeignKey("episode.id", ondelete="CASCADE"), index=True
    )

    title: Mapped[str] = mapped_column(String(500))  # denormalised for the Exports table
    target_path: Mapped[str] = mapped_column(String(2000))  # relative to MEDIA_ROOT
    nfo_path: Mapped[str | None] = mapped_column(String(2000))
    stream_url_snapshot: Mapped[str] = mapped_column(String(2000))
    status: Mapped[str] = mapped_column(String(12), default="written", index=True)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    movie: Mapped[Movie | None] = relationship(back_populates="exports")
    episode: Mapped[Episode | None] = relationship(back_populates="exports")
    server: Mapped[XCServer | None] = relationship(viewonly=True)


class Setting(Base):
    """Key/value overrides for a subset of config, editable on the Settings page."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class AdminUser(Base):
    """The single admin account created by the first-run setup wizard."""

    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


__all__ = [
    "XCServer", "Category", "Movie", "Series", "Episode", "Export", "Setting", "AdminUser",
]
