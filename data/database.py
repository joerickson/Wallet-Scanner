from __future__ import annotations

from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

from config import DATA_DIR, DATABASE_URL, TURSO_AUTH_TOKEN, TURSO_DATABASE_URL

_turso_connection = None

if TURSO_DATABASE_URL and TURSO_AUTH_TOKEN:
    try:
        import libsql_experimental as libsql  # type: ignore[import]
    except ImportError as exc:
        raise RuntimeError(
            "TURSO_DATABASE_URL is set but libsql-experimental is not installed. "
            "Run: pip install libsql-experimental"
        ) from exc

    from sqlalchemy.pool import StaticPool

    _local_replica = str(DATA_DIR / "turso_replica.db")
    _turso_connection = libsql.connect(
        _local_replica, sync_url=TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN
    )
    _turso_connection.sync()  # Pull latest from Turso before operating
    _engine = create_engine(
        "sqlite+pysqlite://",
        creator=lambda: _turso_connection,
        poolclass=StaticPool,
    )
else:
    _engine = create_engine(
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
    with Session(_engine) as session:
        yield session


def sync_to_turso() -> None:
    """Push local replica writes to Turso. No-op when using local SQLite."""
    if _turso_connection is not None:
        _turso_connection.sync()
