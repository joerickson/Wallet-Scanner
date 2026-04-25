"""Migration script: drop the trade table and rebuild walletmetrics.

The /activity endpoint proved insufficient — it returns transaction events
without resolution data, so all 3.4M trade rows have pnl=NULL and
is_resolved=FALSE. This script drops the trade table and also drops
walletmetrics (which has all-NULL pnl) so they are recreated fresh
with the new schema on the next `python main.py scan` run.

Run once after merging the leaderboard+positions refactor:
    python scripts/drop_trade_table.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sure the project root is on sys.path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import text

from config import DATABASE_URL
from data.database import get_engine, init_db


def main() -> None:
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS trade"))
        conn.execute(text("DROP TABLE IF EXISTS walletmetrics"))
        conn.commit()
    print("Dropped tables: trade, walletmetrics")

    # Recreate tables under the new schema
    init_db()
    print("Recreated walletmetrics with new schema (position-based fields).")
    print("Done. Run `python main.py scan` to populate fresh data.")


if __name__ == "__main__":
    main()
