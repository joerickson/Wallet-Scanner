"""Add first_seen_at, last_seen_at, is_active columns to the position table.

Idempotent — safe to re-run. Uses ADD COLUMN IF NOT EXISTS (PostgreSQL 9.6+).

Usage:
    python scripts/migrate_position_history.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from data.database import get_engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def migrate() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("""
            ALTER TABLE position
              ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP DEFAULT NOW(),
              ADD COLUMN IF NOT EXISTS last_seen_at  TIMESTAMP DEFAULT NOW(),
              ADD COLUMN IF NOT EXISTS is_active     BOOLEAN   DEFAULT TRUE
        """))
        # Backfill any NULLs left by rows predating this migration
        conn.execute(text("""
            UPDATE position
               SET first_seen_at = NOW(),
                   last_seen_at  = NOW(),
                   is_active     = TRUE
             WHERE first_seen_at IS NULL
                OR last_seen_at  IS NULL
                OR is_active     IS NULL
        """))
        conn.commit()

    logger.info("Migration complete: position.first_seen_at / last_seen_at / is_active added.")


if __name__ == "__main__":
    migrate()
