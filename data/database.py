from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from config import DATABASE_URL

# Single shared engine — SQLite is single-writer, so one engine is correct
_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)


def init_db() -> None:
    """Create all tables if they don't already exist. Safe to call repeatedly."""
    # Import schema so SQLModel registers all table metadata before create_all
    import data.schema  # noqa: F401

    SQLModel.metadata.create_all(_engine)


def get_engine():
    return _engine


def get_session() -> Generator[Session, None, None]:
    with Session(_engine) as session:
        yield session
