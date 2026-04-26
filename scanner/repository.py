from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlmodel import Session, select

from data.database import get_engine
from data.schema import (
    Alert,
    Position,
    Wallet,
    WalletMetrics,
    WalletRanking,
    WatchedWallet,
)

logger = logging.getLogger(__name__)


def _session() -> Session:
    return Session(get_engine(), expire_on_commit=False)


# ── Wallet ─────────────────────────────────────────────────────────────────────

def upsert_wallets(addresses: list[str]) -> None:
    """Insert addresses that don't exist yet; leave existing rows untouched."""
    with _session() as s:
        existing = {w.address for w in s.exec(select(Wallet)).all()}
        new_wallets = [
            Wallet(address=addr)
            for addr in addresses
            if addr not in existing
        ]
        if new_wallets:
            s.add_all(new_wallets)
            s.commit()
            logger.debug("Inserted %d new wallet rows", len(new_wallets))


def mark_wallet_scanned(address: str) -> None:
    with _session() as s:
        wallet = s.get(Wallet, address)
        if wallet is None:
            wallet = Wallet(address=address)
            s.add(wallet)
        wallet.last_scanned = datetime.utcnow()
        s.add(wallet)
        s.commit()


def get_stale_wallets(older_than_hours: int = 24) -> list[Wallet]:
    """Return wallets whose last_scanned is older than the threshold, or never scanned."""
    cutoff = datetime.utcnow() - timedelta(hours=older_than_hours)
    with _session() as s:
        stmt = select(Wallet).where(
            (Wallet.last_scanned == None) | (Wallet.last_scanned < cutoff)  # noqa: E711
        )
        return list(s.exec(stmt).all())


def get_all_wallets() -> list[Wallet]:
    with _session() as s:
        return list(s.exec(select(Wallet)).all())


# ── Positions ─────────────────────────────────────────────────────────────────

def upsert_positions(positions: list[Position]) -> None:
    """Replace all positions for a wallet with the freshly fetched set."""
    if not positions:
        return
    address = positions[0].wallet_address
    with _session() as s:
        # Delete all existing positions for this wallet before reinserting
        existing = s.exec(select(Position).where(Position.wallet_address == address)).all()
        for row in existing:
            s.delete(row)
        s.flush()
        s.add_all(positions)
        s.commit()
        logger.debug("Stored %d positions for %s", len(positions), address)


def get_positions_for_wallet(address: str) -> list[Position]:
    with _session() as s:
        stmt = select(Position).where(Position.wallet_address == address)
        return list(s.exec(stmt).all())


# ── Metrics ───────────────────────────────────────────────────────────────────

def upsert_metrics(metrics: WalletMetrics) -> None:
    with _session() as s:
        existing = s.get(WalletMetrics, metrics.wallet_address)
        if existing:
            for field, val in metrics.model_dump(exclude={"wallet_address"}).items():
                setattr(existing, field, val)
            s.add(existing)
        else:
            s.add(metrics)
        s.commit()


def get_all_metrics() -> list[WalletMetrics]:
    with _session() as s:
        return list(s.exec(select(WalletMetrics)).all())


def get_metrics_for_wallet(address: str) -> WalletMetrics | None:
    with _session() as s:
        return s.get(WalletMetrics, address)


# ── Rankings ──────────────────────────────────────────────────────────────────

def upsert_ranking(ranking: WalletRanking) -> None:
    with _session() as s:
        existing = s.get(WalletRanking, ranking.wallet_address)
        if existing:
            # Preserve Claude review fields if already populated
            for field in ("skill_signal", "edge_hypothesis", "claude_red_flags",
                          "claude_notes", "reviewed_at"):
                if getattr(ranking, field) is None:
                    setattr(ranking, field, getattr(existing, field))
            s.merge(ranking)
        else:
            s.add(ranking)
        s.commit()


def upsert_rankings(rankings: list[WalletRanking]) -> None:
    for r in rankings:
        upsert_ranking(r)


def update_heuristic_flags(address: str, flags: list[str]) -> None:
    with _session() as s:
        existing = s.get(WalletRanking, address)
        if existing is None:
            return
        existing.heuristic_red_flags = json.dumps(flags)
        s.add(existing)
        s.commit()


def update_claude_review(
    address: str,
    skill_signal: float | None,
    edge_hypothesis: str | None,
    red_flags: list[str],
    notes: str | None,
) -> None:
    with _session() as s:
        existing = s.get(WalletRanking, address)
        if existing is None:
            logger.warning("update_claude_review: no ranking row for %s", address)
            return
        existing.skill_signal = skill_signal
        existing.edge_hypothesis = edge_hypothesis
        existing.claude_red_flags = json.dumps(red_flags)
        existing.claude_notes = notes
        existing.reviewed_at = datetime.utcnow()
        s.add(existing)
        s.commit()


def get_rankings_count() -> int:
    """Return the total number of ranked wallets."""
    with _session() as s:
        result = s.exec(select(func.count()).select_from(WalletRanking)).one()
        return result


def get_top_rankings(limit: int = 50) -> list[WalletRanking]:
    with _session() as s:
        stmt = select(WalletRanking).order_by(WalletRanking.rank).limit(limit)
        return list(s.exec(stmt).all())


def get_ranking_for_wallet(address: str) -> WalletRanking | None:
    with _session() as s:
        return s.get(WalletRanking, address)


def get_rankings_for_wallets(addresses: list[str]) -> dict[str, WalletRanking]:
    """Return a mapping of address → WalletRanking for the given addresses."""
    with _session() as s:
        stmt = select(WalletRanking).where(WalletRanking.wallet_address.in_(addresses))
        return {r.wallet_address: r for r in s.exec(stmt).all()}


# ── Watched wallets ───────────────────────────────────────────────────────────

def add_to_watchlist(address: str) -> bool:
    """Return True if newly added, False if already watched."""
    with _session() as s:
        existing = s.get(WatchedWallet, address)
        if existing:
            return False
        s.add(WatchedWallet(wallet_address=address))
        # Mirror the flag on the Wallet row too
        wallet = s.get(Wallet, address)
        if wallet is None:
            wallet = Wallet(address=address)
            s.add(wallet)
        wallet.is_watched = True
        s.add(wallet)
        s.commit()
        return True


def get_watched_wallets() -> list[WatchedWallet]:
    with _session() as s:
        return list(s.exec(select(WatchedWallet)).all())


def update_watched_positions(address: str, positions_json: str) -> None:
    with _session() as s:
        w = s.get(WatchedWallet, address)
        if w is None:
            return
        w.known_positions = positions_json
        w.last_position_check = datetime.utcnow()
        s.add(w)
        s.commit()


# ── Alerts ────────────────────────────────────────────────────────────────────

def save_alert(alert: Alert) -> None:
    with _session() as s:
        s.add(alert)
        s.commit()


def get_recent_alerts(limit: int = 100) -> list[Alert]:
    with _session() as s:
        stmt = select(Alert).order_by(Alert.alerted_at.desc()).limit(limit)
        return list(s.exec(stmt).all())
