from __future__ import annotations

import asyncio
import csv
import json
import sys
from datetime import datetime

import click
from rich.console import Console
from rich.table import Table

from config import setup_logging
from data.database import init_db

console = Console()


# ── CLI root ──────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.2.0", prog_name="wallet-scanner")
def cli() -> None:
    """Polymarket wallet research tool — read-only, no trading, no keys."""
    setup_logging()
    init_db()


# ── scan ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.option(
    "--incremental",
    is_flag=True,
    default=False,
    help="Only refresh wallets not scanned in the last 24 hours.",
)
@click.option(
    "--max-wallets",
    default=10_000,
    show_default=True,
    type=int,
    help="Maximum number of wallets to discover and process.",
)
def scan(incremental: bool, max_wallets: int) -> None:
    """Full wallet discovery + scoring pass.

    Sweeps the Polymarket leaderboard, fetches positions per wallet,
    computes metrics, ranks wallets, runs Claude qualitative review on
    top 200, and saves results to the database.
    """
    from scanner.scanner import run_scan

    console.print(
        f"[bold]Starting {'incremental' if incremental else 'full'} scan "
        f"(max {max_wallets:,} wallets)…[/bold]"
    )
    rankings = asyncio.run(run_scan(incremental=incremental, max_wallets=max_wallets))
    console.print(
        f"[green]Scan complete — {len(rankings)} wallets ranked.[/green]"
    )
    if rankings:
        console.print(
            f"Top wallet: [bold]{rankings[0].wallet_address}[/bold] "
            f"score={rankings[0].composite_score:.4f}"
        )


# ── leaderboard ───────────────────────────────────────────────────────────────

@cli.command()
@click.option("--top", default=50, show_default=True, type=int, help="Number of wallets to show.")
@click.option(
    "--export",
    "export_fmt",
    type=click.Choice(["csv", "json"]),
    default=None,
    help="Export format instead of printing to terminal.",
)
@click.option("--output", default=None, help="Output file path (defaults to stdout for json/csv).")
def leaderboard(top: int, export_fmt: str | None, output: str | None) -> None:
    """Print or export the current leaderboard.

    Re-running this command is safe and idempotent — it only reads from the DB.
    """
    from scanner import repository as repo

    rankings = repo.get_top_rankings(limit=top)
    if not rankings:
        console.print("[yellow]No rankings found. Run: python main.py scan[/yellow]")
        return

    rows = []
    for r in rankings:
        m = repo.get_metrics_for_wallet(r.wallet_address)
        flags: list[str] = []
        for field in (r.heuristic_red_flags, r.claude_red_flags):
            if field:
                try:
                    flags += json.loads(field)
                except json.JSONDecodeError:
                    pass

        rows.append({
            "rank": r.rank,
            "address": r.wallet_address,
            "composite_score": round(r.composite_score, 4),
            "total_pnl": round(m.total_pnl, 2) if m and m.total_pnl is not None else None,
            "total_volume": round(m.total_volume, 2) if m and m.total_volume is not None else None,
            "portfolio_value": round(m.portfolio_value, 2) if m and m.portfolio_value is not None else None,
            "positions": m.trade_count if m else None,
            "realized": m.realized_position_count if m else None,
            "skill_signal": round(r.skill_signal, 2) if r.skill_signal is not None else None,
            "edge_hypothesis": r.edge_hypothesis or "",
            "red_flags": flags,
            "ranked_at": r.ranked_at.isoformat() if isinstance(r.ranked_at, datetime) else str(r.ranked_at),
        })

    if export_fmt == "json":
        out = json.dumps(rows, indent=2)
        if output:
            with open(output, "w") as f:
                f.write(out)
            console.print(f"[green]Exported {len(rows)} wallets to {output}[/green]")
        else:
            print(out)
        return

    if export_fmt == "csv":
        dest = open(output, "w", newline="") if output else sys.stdout
        writer = csv.DictWriter(dest, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows:
            row["red_flags"] = "|".join(row["red_flags"])  # type: ignore[arg-type]
            writer.writerow(row)
        if output:
            dest.close()  # type: ignore[union-attr]
            console.print(f"[green]Exported {len(rows)} wallets to {output}[/green]")
        return

    # Terminal table
    table = Table(title=f"Top {len(rows)} Wallets", show_lines=False)
    table.add_column("Rank", justify="right", style="dim", width=5)
    table.add_column("Address", style="cyan", no_wrap=True)
    table.add_column("Score", justify="right")
    table.add_column("P&L", justify="right")
    table.add_column("Volume", justify="right")
    table.add_column("Portfolio", justify="right")
    table.add_column("Positions", justify="right")
    table.add_column("Resolved", justify="right")
    table.add_column("Skill", justify="right")
    table.add_column("Flags")

    for r in rows:
        addr = f"{r['address'][:8]}…{r['address'][-6:]}"
        pnl = f"${r['total_pnl']:,.0f}" if r["total_pnl"] is not None else "–"
        vol = f"${r['total_volume']:,.0f}" if r["total_volume"] is not None else "–"
        portfolio = f"${r['portfolio_value']:,.0f}" if r["portfolio_value"] is not None else "–"
        positions = str(r["positions"]) if r["positions"] is not None else "–"
        realized = str(r["realized"]) if r["realized"] is not None else "–"
        skill = f"{r['skill_signal']:.2f}" if r["skill_signal"] is not None else "–"
        flags_str = " ".join(f"[red]⚑{f}[/red]" for f in r["red_flags"]) if r["red_flags"] else "[green]✓[/green]"

        table.add_row(
            str(r["rank"]),
            addr,
            str(r["composite_score"]),
            pnl,
            vol,
            portfolio,
            positions,
            realized,
            skill,
            flags_str,
        )

    console.print(table)


# ── wallet ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("address")
def wallet(address: str) -> None:
    """Deep-dive on a single wallet address.

    Shows leaderboard rank, total P&L, top 10 positions, red flags, and Claude review.
    """
    from scanner import repository as repo

    address = address.lower()
    m = repo.get_metrics_for_wallet(address)
    r = repo.get_ranking_for_wallet(address)
    positions = repo.get_positions_for_wallet(address)

    if not m and not r:
        console.print(f"[yellow]No data for {address}. Run a scan first.[/yellow]")
        return

    console.rule(f"[bold]{address}[/bold]")

    if m:
        metrics_table = Table.grid(padding=(0, 2))
        metrics_table.add_column(style="dim")
        metrics_table.add_column()
        pairs = [
            ("Total P&L", f"${m.total_pnl:,.2f}" if m.total_pnl is not None else "–"),
            ("Total volume", f"${m.total_volume:,.2f}" if m.total_volume is not None else "–"),
            ("Portfolio value", f"${m.portfolio_value:,.2f}" if m.portfolio_value is not None else "–"),
            ("Positions", str(m.trade_count)),
            ("Resolved", str(m.realized_position_count)),
            ("Unresolved", str(m.unresolved_position_count)),
            ("Markets traded", str(m.market_count)),
            ("Avg position size", f"${m.avg_position_size:,.2f}" if m.avg_position_size is not None else "–"),
            ("Max position size", f"${m.max_position_size_usd:,.2f}" if m.max_position_size_usd is not None else "–"),
            ("P&L from top 3", f"{m.pct_pnl_from_top_3_positions:.1%}" if m.pct_pnl_from_top_3_positions is not None else "–"),
        ]
        for label, val in pairs:
            metrics_table.add_row(label, val)
        console.print(metrics_table)

    if r:
        console.rule("Ranking & Review")
        console.print(f"Rank: [bold]#{r.rank}[/bold]   Score: {r.composite_score:.4f}")
        if r.skill_signal is not None:
            console.print(f"Skill signal (Claude): {r.skill_signal:.2f}")
        if r.edge_hypothesis:
            console.print(f"Edge hypothesis: [italic]{r.edge_hypothesis}[/italic]")
        if r.claude_notes:
            console.print(f"Notes: {r.claude_notes}")

        all_flags: list[str] = []
        for field_val in (r.heuristic_red_flags, r.claude_red_flags):
            if field_val:
                try:
                    all_flags += json.loads(field_val)
                except json.JSONDecodeError:
                    pass
        if all_flags:
            console.print(f"[red]Red flags: {', '.join(all_flags)}[/red]")

    if positions:
        # Sort by absolute cash_pnl descending; show top 10
        sorted_positions = sorted(
            positions,
            key=lambda p: abs(p.cash_pnl) if p.cash_pnl is not None else 0.0,
            reverse=True,
        )
        display = sorted_positions[:10]
        console.rule(f"Top Positions ({len(display)} of {len(positions)})")
        p_table = Table(show_header=True)
        p_table.add_column("Status", style="dim")
        p_table.add_column("Outcome")
        p_table.add_column("Cash P&L", justify="right")
        p_table.add_column("Size", justify="right")
        p_table.add_column("Avg Price", justify="right")
        p_table.add_column("Market")

        for pos in display:
            status = "[green]RESOLVED[/green]" if pos.redeemable else "open"
            pnl_val = pos.cash_pnl or 0.0
            pnl_str = f"${pnl_val:,.2f}"
            pnl_style = "green" if pnl_val > 0 else "red" if pnl_val < 0 else ""
            size_str = f"${pos.size:,.2f}" if pos.size is not None else "–"
            price_str = f"{pos.avg_price:.3f}" if pos.avg_price is not None else "–"
            title = (pos.title or pos.condition_id)[:45]
            outcome = pos.outcome or "?"

            p_table.add_row(
                status,
                outcome,
                f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
                size_str,
                price_str,
                title,
            )
        console.print(p_table)


# ── watch ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("address", required=False, default=None)
@click.option("--top", type=int, default=None, help="Auto-add top N wallets from the leaderboard.")
def watch(address: str | None, top: int | None) -> None:
    """Add wallet(s) to the watch list for live position monitoring.

    \b
    Examples:
      python main.py watch 0xabc...          # add a specific wallet
      python main.py watch --top 25          # add top 25 from leaderboard
    """
    from scanner import repository as repo

    if top is not None:
        rankings = repo.get_top_rankings(limit=top)
        if not rankings:
            console.print("[yellow]No rankings found. Run: python main.py scan[/yellow]")
            return
        added = 0
        for r in rankings:
            if repo.add_to_watchlist(r.wallet_address):
                added += 1
        console.print(f"[green]Added {added} new wallets to watchlist (already tracked: {len(rankings)-added})[/green]")
        return

    if address:
        address = address.lower()
        if repo.add_to_watchlist(address):
            console.print(f"[green]Watching {address}[/green]")
        else:
            console.print(f"[yellow]{address} is already on the watchlist[/yellow]")
        return

    # No args — show current watchlist
    watched = repo.get_watched_wallets()
    if not watched:
        console.print("[dim]No wallets on watchlist. Use: python main.py watch <address>[/dim]")
        return

    table = Table(title=f"Watchlist ({len(watched)} wallets)")
    table.add_column("Address", style="cyan")
    table.add_column("Added")
    table.add_column("Last checked", style="dim")
    for w in watched:
        last_check = w.last_position_check.strftime("%Y-%m-%d %H:%M") if w.last_position_check else "never"
        table.add_row(
            w.wallet_address,
            w.added_at.strftime("%Y-%m-%d"),
            last_check,
        )
    console.print(table)


# ── alerts ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--interval", default=300, show_default=True, type=int, help="Poll interval in seconds.")
def alerts(interval: int) -> None:
    """Live terminal feed of new positions from watched wallets.

    Polls every INTERVAL seconds until interrupted with Ctrl-C.
    Optionally sends Discord/Telegram webhooks if configured in .env.
    """
    from watch.poller import run_poll_loop

    asyncio.run(run_poll_loop(interval=interval))


# ── dashboard ─────────────────────────────────────────────────────────────────

@cli.command()
def dashboard() -> None:
    """Launch the full 4-panel rich/textual terminal dashboard."""
    from dashboard.app import WalletScannerApp

    WalletScannerApp().run()


if __name__ == "__main__":
    cli()
