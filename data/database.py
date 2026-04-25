from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine as _sa_create_engine
from sqlmodel import Session, SQLModel

from config import DATABASE_URL

_is_postgres = DATABASE_URL.startswith("postgresql://") or DATABASE_URL.startswith("postgres://")

if _is_postgres:
    _engine = _sa_create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
    )
else:
    _engine = _sa_create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call repeatedly."""
    import data.schema  # noqa: F401

    SQLModel.metadata.create_all(_engine)


def get_engine():
    return _engine


def get_session() -> Generator[Session, None, None]:
    with Session(_engine, expire_on_commit=False) as session:
        yield session
