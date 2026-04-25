"""Migrate data from the local SQLite research.db to a Neon Postgres database.

Usage:
    DATABASE_URL=postgresql://... python scripts/migrate_local_to_postgres.py

The script is idempotent: existing rows in Postgres are skipped via ON CONFLICT DO NOTHING.
Progress is logged every 1000 rows. Expected runtime for ~10k wallets + 4M trades: 2–5 min.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session, SQLModel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

_BATCH_SIZE = 1000


def _get_engines():
    sqlite_path = Path(__file__).parent.parent / "data" / "research.db"
    if not sqlite_path.exists():
        raise FileNotFoundError(f"SQLite database not found: {sqlite_path}")

    db_url = os.environ.get("DATABASE_URL", "")
    if not (db_url.startswith("postgresql://") or db_url.startswith("postgres://")):
        raise ValueError(
            "DATABASE_URL must be set to a postgresql:// connection string. "
            "Example: DATABASE_URL=postgresql://user:pass@host/db?sslmode=require"
        )

    sqlite_engine = create_engine(
        f"sqlite:///{sqlite_path}",
        connect_args={"check_same_thread": False},
    )
    pg_engine = create_engine(db_url, pool_pre_ping=True, pool_recycle=300)
    return sqlite_engine, pg_engine


def _migrate_table(
    sqlite_engine,
    pg_engine,
    table_name: str,
    pk_col: str,
) -> None:
    with sqlite_engine.connect() as src:
        rows = src.execute(text(f"SELECT * FROM {table_name}")).mappings().all()

    if not rows:
        logger.info("%s: no rows in SQLite, skipping", table_name)
        return

    logger.info("%s: %d rows to migrate", table_name, len(rows))
    table = SQLModel.metadata.tables[table_name]

    inserted = 0
    skipped = 0
    with pg_engine.begin() as dst:
        for batch_start in range(0, len(rows), _BATCH_SIZE):
            batch = [dict(r) for r in rows[batch_start : batch_start + _BATCH_SIZE]]
            stmt = pg_insert(table).values(batch).on_conflict_do_nothing(index_elements=[pk_col])
            result = dst.execute(stmt)
            inserted += result.rowcount
            skipped += len(batch) - result.rowcount
            if (batch_start + _BATCH_SIZE) % (_BATCH_SIZE * 10) == 0 or batch_start + _BATCH_SIZE >= len(rows):
                logger.info(
                    "%s: %d/%d processed (%d inserted, %d skipped)",
                    table_name,
                    min(batch_start + _BATCH_SIZE, len(rows)),
                    len(rows),
                    inserted,
                    skipped,
                )

    logger.info("%s: done — %d inserted, %d already existed", table_name, inserted, skipped)


def main() -> None:
    import data.schema  # noqa: F401 — registers SQLModel metadata

    sqlite_engine, pg_engine = _get_engines()

    logger.info("Ensuring Postgres schema exists …")
    SQLModel.metadata.create_all(pg_engine)

    # Tables ordered to satisfy any implicit FK dependencies (metrics/rankings reference wallet)
    _migrate_table(sqlite_engine, pg_engine, "wallet", "address")
    _migrate_table(sqlite_engine, pg_engine, "trade", "id")
    _migrate_table(sqlite_engine, pg_engine, "walletmetrics", "wallet_address")
    _migrate_table(sqlite_engine, pg_engine, "walletranking", "wallet_address")
    _migrate_table(sqlite_engine, pg_engine, "watchedwallet", "wallet_address")
    _migrate_table(sqlite_engine, pg_engine, "alert", "id")

    logger.info("Migration complete.")


if __name__ == "__main__":
    main()
