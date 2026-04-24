from __future__ import annotations

import json
from datetime import datetime

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Label, RichLog, Static

from data.database import init_db
from scanner import repository as repo

# ── Layout CSS ────────────────────────────────────────────────────────────────

_CSS = """
Screen {
    layout: grid;
    grid-size: 2 2;
    grid-rows: 1fr 1fr;
    grid-columns: 2fr 1fr;
}

#panel-leaderboard {
    border: solid $accent;
    height: 100%;
}

#panel-status {
    border: solid $success;
    height: 100%;
}

#panel-alerts {
    border: solid $warning;
    height: 100%;
}

#panel-detail {
    border: solid $primary;
    height: 100%;
}

.panel-title {
    text-align: center;
    background: $boost;
    color: $text;
    padding: 0 1;
}

DataTable {
    height: 1fr;
}

RichLog {
    height: 1fr;
}
"""


class WalletScannerApp(App):
    """4-panel Textual dashboard: leaderboard, system status, alert feed, wallet detail."""

    CSS = _CSS
    TITLE = "Polymarket Wallet Scanner"

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("d", "toggle_dark", "Dark/Light"),
    ]

    selected_wallet: reactive[str | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Vertical(
            Label("Top Wallets", classes="panel-title"),
            DataTable(id="leaderboard", cursor_type="row"),
            id="panel-leaderboard",
        )
        yield Vertical(
            Label("System Status", classes="panel-title"),
            RichLog(id="status-log", markup=True, wrap=True),
            id="panel-status",
        )
        yield Vertical(
            Label("Recent Alerts", classes="panel-title"),
            RichLog(id="alerts-log", markup=True, wrap=True),
            id="panel-alerts",
        )
        yield Vertical(
            Label("Wallet Detail", classes="panel-title"),
            RichLog(id="detail-log", markup=True, wrap=True),
            id="panel-detail",
        )
        yield Footer()

    def on_mount(self) -> None:
        init_db()
        self._setup_leaderboard()
        self._populate_leaderboard()
        self._populate_alerts()
        self._populate_status()
        self.set_interval(60, self._auto_refresh)

    # ── Leaderboard ───────────────────────────────────────────────────────────

    def _setup_leaderboard(self) -> None:
        table = self.query_one("#leaderboard", DataTable)
        table.add_columns(
            "Rank", "Address", "Score", "Win%", "Sharpe", "P&L", "Flags"
        )

    def _populate_leaderboard(self) -> None:
        table = self.query_one("#leaderboard", DataTable)
        table.clear()

        rankings = repo.get_top_rankings(limit=100)
        for r in rankings:
            metrics = repo.get_metrics_for_wallet(r.wallet_address)
            addr_short = f"{r.wallet_address[:6]}…{r.wallet_address[-4:]}"

            win_pct = (
                f"{metrics.win_rate:.0%}" if metrics and metrics.win_rate is not None else "–"
            )
            sharpe = (
                f"{metrics.sharpe_ratio:.2f}" if metrics and metrics.sharpe_ratio is not None else "–"
            )
            pnl = (
                f"${metrics.total_pnl:,.0f}" if metrics and metrics.total_pnl is not None else "–"
            )

            flags = []
            if r.heuristic_red_flags:
                try:
                    flags = json.loads(r.heuristic_red_flags)
                except json.JSONDecodeError:
                    pass
            if r.claude_red_flags:
                try:
                    flags += json.loads(r.claude_red_flags)
                except json.JSONDecodeError:
                    pass
            flag_str = "⚑" * len(flags) if flags else "✓"

            table.add_row(
                str(r.rank),
                addr_short,
                f"{r.composite_score:.4f}",
                win_pct,
                sharpe,
                pnl,
                flag_str,
                key=r.wallet_address,
            )

    # ── Alert feed ────────────────────────────────────────────────────────────

    def _populate_alerts(self) -> None:
        log = self.query_one("#alerts-log", RichLog)
        log.clear()
        alerts = repo.get_recent_alerts(limit=50)
        if not alerts:
            log.write("[dim]No alerts yet — add wallets with: python main.py watch <addr>[/dim]")
            return
        for a in alerts:
            ts = a.alerted_at.strftime("%H:%M:%S")
            addr = f"{a.wallet_address[:6]}…{a.wallet_address[-4:]}"
            question = (a.market_question or a.market_id)[:50]
            size_str = f"${a.size:,.0f}" if a.size else "?"
            colour = {"new_position": "green", "closed_position": "yellow", "large_position": "magenta"}.get(
                a.alert_type, "cyan"
            )
            log.write(
                f"[dim]{ts}[/dim] [{colour}]{a.alert_type}[/{colour}] "
                f"[bold]{addr}[/bold] {size_str} | {question}"
            )

    # ── Status panel ──────────────────────────────────────────────────────────

    def _populate_status(self) -> None:
        log = self.query_one("#status-log", RichLog)
        log.clear()

        all_wallets = repo.get_all_wallets()
        watched = repo.get_watched_wallets()
        rankings = repo.get_top_rankings(limit=1)
        top_score = rankings[0].composite_score if rankings else None

        log.write(f"[bold]Wallet Scanner[/bold] — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
        log.write(f"Total wallets in DB: [cyan]{len(all_wallets)}[/cyan]")
        log.write(f"Watched wallets:     [green]{len(watched)}[/green]")
        log.write(f"Ranked wallets:      [cyan]{len(repo.get_top_rankings(limit=10_000))}[/cyan]")
        if top_score:
            log.write(f"Top composite score: [bold]{top_score:.4f}[/bold]")
        log.write("")
        log.write("[dim]Keybindings:[/dim]")
        log.write("[dim]  R — refresh   D — dark/light   Q — quit[/dim]")
        log.write("[dim]  Click a row to see wallet detail[/dim]")

    # ── Wallet detail on row selection ────────────────────────────────────────

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        address = str(event.row_key.value)
        self.selected_wallet = address
        self._show_wallet_detail(address)

    def _show_wallet_detail(self, address: str) -> None:
        log = self.query_one("#detail-log", RichLog)
        log.clear()

        metrics = repo.get_metrics_for_wallet(address)
        ranking = repo.get_ranking_for_wallet(address)

        log.write(f"[bold]{address}[/bold]")
        log.write(f"[link=https://polymarket.com/profile/{address}]View on Polymarket ↗[/link]")
        log.write("")

        if metrics:
            log.write("[bold underline]Metrics[/bold underline]")
            log.write(f"Trades:      {metrics.trade_count}")
            log.write(f"Win rate:    {metrics.win_rate:.1%}" if metrics.win_rate is not None else "Win rate:    –")
            log.write(f"Total P&L:   ${metrics.total_pnl:,.2f}" if metrics.total_pnl is not None else "Total P&L:   –")
            log.write(f"Volume:      ${metrics.total_volume:,.2f}" if metrics.total_volume is not None else "Volume:      –")
            log.write(f"Sharpe:      {metrics.sharpe_ratio:.3f}" if metrics.sharpe_ratio is not None else "Sharpe:      –")
            log.write(f"Prof factor: {metrics.profit_factor:.3f}" if metrics.profit_factor is not None else "Prof factor: –")
            log.write(f"Markets:     {metrics.market_count}")
            log.write(f"Avg hold:    {metrics.avg_hold_time_hours:.1f}h" if metrics.avg_hold_time_hours is not None else "Avg hold:    –")

        if ranking:
            log.write("")
            log.write("[bold underline]Ranking[/bold underline]")
            log.write(f"Rank:        #{ranking.rank}")
            log.write(f"Score:       {ranking.composite_score:.4f}")

            if ranking.skill_signal is not None:
                log.write(f"Skill signal:{ranking.skill_signal:.2f}")
            if ranking.edge_hypothesis:
                log.write(f"Edge:        {ranking.edge_hypothesis[:100]}")
            if ranking.claude_notes:
                log.write("")
                log.write(f"[italic]{ranking.claude_notes[:200]}[/italic]")

            all_flags: list[str] = []
            for field in (ranking.heuristic_red_flags, ranking.claude_red_flags):
                if field:
                    try:
                        all_flags += json.loads(field)
                    except json.JSONDecodeError:
                        pass
            if all_flags:
                log.write("")
                log.write("[bold red]Red Flags[/bold red]")
                for flag in all_flags:
                    log.write(f"  ⚑ [red]{flag}[/red]")

    # ── Refresh actions ───────────────────────────────────────────────────────

    def _auto_refresh(self) -> None:
        self._populate_leaderboard()
        self._populate_alerts()
        self._populate_status()

    def action_refresh(self) -> None:
        self._auto_refresh()
        if self.selected_wallet:
            self._show_wallet_detail(self.selected_wallet)
