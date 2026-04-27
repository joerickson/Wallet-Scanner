import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../lib/api';
import { getSession, signOut } from '../lib/auth';
import type { Wallet, LeaderboardData, StrategyAnalysis, RegenState, PaperTest, PaperTrade } from '../types';

// ── Constants ─────────────────────────────────────────────────────────────────

const FLAG_SEV: Record<string, string> = {
  single_event_luck: 'r',
  survivorship: 'r',
  market_concentration: 'y',
  recency_cliff: 'y',
  data_artefact: 'k',
};

const FLAG_LABEL: Record<string, string> = {
  single_event_luck: 'single event',
  survivorship: 'survivorship',
  market_concentration: 'concentration',
  recency_cliff: 'recency cliff',
  data_artefact: 'data artefact',
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function fmtPnl(v?: number | null): string {
  if (v == null) return '–';
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '';
  return sign + '$' + (abs >= 1000 ? Math.round(abs).toLocaleString('en-US') : abs.toFixed(2));
}

function fmtVol(v?: number | null): string {
  if (v == null) return '–';
  if (v >= 1e9) return '$' + (v / 1e9).toFixed(1) + 'B';
  if (v >= 1e6) return '$' + (v / 1e6).toFixed(1) + 'M';
  if (v >= 1e3) return '$' + (v / 1e3).toFixed(1) + 'K';
  return '$' + v.toFixed(0);
}

function fmtUsd(v?: number | null): string {
  return v == null ? '–' : '$' + Math.round(Math.abs(v)).toLocaleString('en-US');
}

function fmtPct(v?: number | null): string {
  return v == null ? '–' : (v * 100).toFixed(1) + '%';
}

function fmtPctD(v?: number | null): string {
  return v == null ? '–' : (v * 100).toFixed(0) + '%';
}

function shortAddr(a: string): string {
  return a && a.length >= 10 ? a.slice(0, 6) + '…' + a.slice(-4) : a || '–';
}

function timeAgo(iso?: string | null): string {
  if (!iso) return '';
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' hr ago';
  return Math.floor(s / 86400) + ' days ago';
}

function badgeClass(s?: number | null): string {
  if (s == null) return 'bk';
  return s >= 0.85 ? 'bg' : s >= 0.70 ? 'bb' : 'bk';
}

function normFlag(f: string): string {
  return String(f).toLowerCase().replace(/[\s-]+/g, '_').split(':')[0].trim();
}

function pillClass(key: string, force?: string): string {
  const sev = force || FLAG_SEV[key] || 'k';
  return `pill p${sev}`;
}

function pillLabel(key: string, original: string): string {
  return FLAG_LABEL[key] || original;
}

function copyToClipboard(text: string, cb: () => void) {
  if (navigator.clipboard) {
    navigator.clipboard.writeText(text).then(cb).catch(() => fallbackCopy(text, cb));
  } else {
    fallbackCopy(text, cb);
  }
}

function fallbackCopy(text: string, cb: () => void) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.opacity = '0';
  document.body.appendChild(ta);
  ta.focus();
  ta.select();
  document.execCommand('copy');
  document.body.removeChild(ta);
  cb();
}

// ── Sub-components ────────────────────────────────────────────────────────────

function FlagPills({ flags, forceClass }: { flags: string[]; forceClass?: string }) {
  if (!flags.length) return <span className="neu" style={{ fontSize: 12 }}>–</span>;
  return (
    <>
      {flags.map((f, i) => {
        const key = normFlag(f);
        return (
          <span key={i} className={pillClass(key, forceClass)}>
            {pillLabel(key, f)}
          </span>
        );
      })}
    </>
  );
}

function DeepSkeleton() {
  return (
    <div className="da-skeleton" role="status" aria-label="Loading strategy analysis">
      <div className="da-header">
        <div className="da-skel-block" style={{ width: 130, height: 14 }} />
        <div className="da-skel-block" style={{ width: 90, height: 11, marginLeft: 8 }} />
      </div>
      <div className="da-cards">
        <div className="da-skel-block" style={{ height: 60 }} />
        <div className="da-skel-block" style={{ height: 60 }} />
        <div className="da-skel-block" style={{ height: 60 }} />
      </div>
      <div className="da-skel-block" style={{ height: 80, marginBottom: 10 }} />
      <div className="da-skel-block" style={{ height: 50 }} />
    </div>
  );
}

// ── PaperTestPanel ────────────────────────────────────────────────────────────

function fmtTimeRemaining(endsAt: string): string {
  const ms = new Date(endsAt).getTime() - Date.now();
  if (ms <= 0) return 'Ended';
  const hours = Math.floor(ms / 3600000);
  const days = Math.floor(hours / 24);
  if (days > 0) return `${days}d ${hours % 24}h remaining`;
  const mins = Math.floor((ms % 3600000) / 60000);
  return hours > 0 ? `${hours}h ${mins}m remaining` : `${mins}m remaining`;
}

function fmtPnlSign(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : '+';
  return sign + '$' + (abs >= 1000 ? Math.round(abs).toLocaleString('en-US') : abs.toFixed(2));
}

interface PaperTestPanelProps {
  paperTestId: string;
  onClose: () => void;
}

function PaperTestPanel({ paperTestId, onClose }: PaperTestPanelProps) {
  const [test, setTest] = useState<PaperTest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchTest = useCallback(() => {
    apiFetch<PaperTest>(`/api/paper-tests/${paperTestId}`)
      .then((data) => {
        setTest(data);
        setLoading(false);
        if (data.status !== 'running' && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      })
      .catch((err) => {
        setError((err as Error).message || 'Failed to load paper test');
        setLoading(false);
      });
  }, [paperTestId]);

  useEffect(() => {
    fetchTest();
    pollRef.current = setInterval(fetchTest, 30000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchTest]);

  async function handleCancel() {
    if (!window.confirm('Cancel this paper test? Open trades will be closed at current prices.')) return;
    try {
      const updated = await apiFetch<PaperTest>(`/api/paper-tests/${paperTestId}/cancel`, { method: 'POST' });
      setTest(updated);
    } catch (e) {
      alert('Failed to cancel: ' + (e as Error).message);
    }
  }

  if (loading) return <div className="pt-panel"><div className="pt-loading">Loading paper test…</div></div>;
  if (error) return <div className="pt-panel"><div className="pt-error">{error}</div></div>;
  if (!test) return null;

  const trades: PaperTrade[] = test.trades || [];
  const totalPnl = test.realized_pnl + test.unrealized_pnl;

  return (
    <div className="pt-panel">
      <div className="pt-header">
        <span className="pt-title">Paper Test</span>
        <span className={`pt-status pt-status-${test.status}`}>{test.status}</span>
        {test.status === 'running' && (
          <span className="pt-time-remaining">{fmtTimeRemaining(test.ends_at)}</span>
        )}
        <button className="pt-close-btn" onClick={onClose} title="Close">×</button>
      </div>

      <div className="pt-metrics">
        <div className="pt-metric">
          <div className="pt-metric-label">Capital</div>
          <div className="pt-metric-value">${test.capital_allocated.toLocaleString('en-US')}</div>
        </div>
        <div className="pt-metric">
          <div className="pt-metric-label">Realized P&L</div>
          <div className={`pt-metric-value ${test.realized_pnl >= 0 ? 'pos' : 'neg'}`}>
            {fmtPnlSign(test.realized_pnl)}
          </div>
        </div>
        <div className="pt-metric">
          <div className="pt-metric-label">Unrealized P&L</div>
          <div className={`pt-metric-value ${test.unrealized_pnl >= 0 ? 'pos' : 'neg'}`}>
            {fmtPnlSign(test.unrealized_pnl)}
          </div>
        </div>
        <div className="pt-metric">
          <div className="pt-metric-label">Total P&L</div>
          <div className={`pt-metric-value ${totalPnl >= 0 ? 'pos' : 'neg'}`}>
            {fmtPnlSign(totalPnl)}
          </div>
        </div>
      </div>

      {trades.length > 0 && (
        <div className="pt-trades">
          <div className="pt-trades-title">Trades</div>
          <div className="pt-trades-table-wrap">
            <table className="pt-trades-table">
              <thead>
                <tr>
                  <th>Market</th>
                  <th>Outcome</th>
                  <th>Entry</th>
                  <th>Current/Exit</th>
                  <th>P&L</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((t) => {
                  const currentOrExit = t.exit_price ?? null;
                  const pnl = t.realized_pnl ?? null;
                  return (
                    <tr key={t.id}>
                      <td className="pt-trade-question" title={t.market_question}>
                        {t.market_question.length > 60 ? t.market_question.slice(0, 60) + '…' : t.market_question}
                      </td>
                      <td>{t.outcome_name}</td>
                      <td>{(t.entry_price * 100).toFixed(1)}¢</td>
                      <td>{currentOrExit != null ? (currentOrExit * 100).toFixed(1) + '¢' : '–'}</td>
                      <td className={pnl != null ? (pnl >= 0 ? 'pos' : 'neg') : ''}>
                        {pnl != null ? fmtPnlSign(pnl) : '–'}
                      </td>
                      <td>{t.status}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {trades.length === 0 && (
        <div className="pt-no-trades">No trades yet — waiting for entry conditions to be met.</div>
      )}

      {test.status === 'running' && (
        <div className="pt-actions">
          <button className="pt-cancel-btn" onClick={handleCancel}>Cancel test</button>
        </div>
      )}
    </div>
  );
}

interface StrategySectionProps {
  addr: string;
  rank: number;
  strategyData: Record<string, StrategyAnalysis | null>;
  strategyLoading: Set<string>;
  strategyError: Record<string, string>;
  regenState: Record<string, RegenState>;
  historyData: Record<string, StrategyAnalysis[]>;
  historyLoading: Set<string>;
  historyError: Record<string, string>;
  historyExpanded: Set<string>;
  onRegen: (addr: string) => void;
  onToggleHistory: (addr: string) => void;
  activePaperTests: Record<string, string>;
  onStartPaperTest: (addr: string, strategyId: string, capitalAllocated: number) => void;
  onClosePaperTestPanel: (addr: string) => void;
}

function StrategySection({
  addr,
  rank,
  strategyData,
  strategyLoading,
  strategyError,
  regenState,
  historyData,
  historyLoading,
  historyError,
  historyExpanded,
  onRegen,
  onToggleHistory,
  activePaperTests,
  onStartPaperTest,
  onClosePaperTestPanel,
}: StrategySectionProps) {
  if (rank > 10) {
    return (
      <div className="da-section">
        <p className="da-placeholder-msg" role="note">
          Deep strategy analysis available for top-10 wallets only. This wallet ranks #{rank}.
        </p>
      </div>
    );
  }

  const loading = strategyLoading.has(addr);
  const err = strategyError[addr];
  const data = strategyData[addr];
  const regen = regenState[addr];

  function RegenControls() {
    if (regen?.rateLimited) {
      return (
        <span className="da-rate-limit" role="alert" aria-live="polite">
          You've hit the daily regeneration limit (5 per day). Resets at midnight UTC.
        </span>
      );
    }
    if (regen?.loading) {
      return (
        <span className="da-inline-msg" role="status" aria-live="polite">
          Analyzing… this may take 30–60 seconds
        </span>
      );
    }
    if (regen?.error) {
      return <span className="da-rate-limit" role="alert">{regen.error}</span>;
    }
    return (
      <button className="da-regen-btn" onClick={() => onRegen(addr)}>
        Regenerate
      </button>
    );
  }

  if (loading || data === undefined) {
    return (
      <div className="da-section">
        <DeepSkeleton />
      </div>
    );
  }

  if (err) {
    return (
      <div className="da-section">
        <div className="da-header">
          <h2 className="da-title">Strategy Analysis</h2>
        </div>
        <p className="da-error-msg" role="alert">Failed to load strategy analysis.</p>
      </div>
    );
  }

  if (data === null) {
    const isLoading = regen?.loading;
    return (
      <div className="da-section">
        <div className="da-header">
          <h2 className="da-title">Strategy Analysis</h2>
          <RegenControls />
        </div>
        <p className="da-empty">Strategy analysis hasn't been generated yet for this wallet.</p>
        {!isLoading && (
          <button className="da-generate-btn" onClick={() => onRegen(addr)}>
            Generate Now
          </button>
        )}
      </div>
    );
  }

  const d = data;
  const failures = Array.isArray(d.failure_modes) ? d.failure_modes : [];
  const risks = Array.isArray(d.risk_factors) ? d.risk_factors : [];
  const histExp = historyExpanded.has(addr);
  const histItems = historyData[addr];

  return (
    <section className="da-section" aria-label="Deep strategy analysis">
      <div className="da-header">
        <h2 className="da-title">Strategy Analysis</h2>
        <span className="da-timestamp">Last analyzed: {timeAgo(d.generated_at)}</span>
        <RegenControls />
      </div>

      <div className="da-cards" aria-label="Strategy at a glance">
        <div className="da-card">
          <div className="da-card-label">Replicable?</div>
          <div className={`da-card-value ${d.is_replicable ? 'da-replicable-yes' : 'da-replicable-no'}`}>
            {d.is_replicable ? 'YES' : 'NO'}
            {d.replicability_confidence != null && (
              <span className="da-conf">{fmtPctD(d.replicability_confidence)}</span>
            )}
          </div>
        </div>
        <div className="da-card">
          <div className="da-card-label">Strategy Type</div>
          <div className="da-card-value" style={{ fontSize: 14, paddingTop: 3 }}>
            <span className="da-strategy-badge">{d.strategy_type || '–'}</span>
            {d.strategy_subtype && (
              <span className="da-conf">{d.strategy_subtype}</span>
            )}
          </div>
        </div>
        <div className="da-card">
          <div className="da-card-label">Min Capital</div>
          <div className="da-card-value" style={{ fontSize: 16 }}>
            {fmtUsd(d.capital_required_min_usd)}
          </div>
        </div>
      </div>

      <div className="da-subsection">
        <div className="da-subsection-title">Replication Blueprint</div>
        <dl className="da-blueprint">
          <dt>Entry Signal</dt><dd>{d.entry_signal || '–'}</dd>
          <dt>Exit Signal</dt><dd>{d.exit_signal || '–'}</dd>
          <dt>Position Sizing</dt><dd>{d.position_sizing_rule || '–'}</dd>
          <dt>Market Selection</dt><dd>{d.market_selection_criteria || '–'}</dd>
          <dt>Infrastructure</dt><dd>{d.infrastructure_required || '–'}</dd>
        </dl>
      </div>

      {(d.estimated_hit_rate != null || d.estimated_avg_hold_time_hours != null || d.estimated_sharpe_proxy != null) && (
        <div className="da-subsection">
          <div className="da-subsection-title">Performance Estimates</div>
          <div className="da-perf">
            {d.estimated_hit_rate != null && (
              <div className="da-perf-item">
                <div className="da-perf-label">Hit Rate</div>
                <div className="da-perf-value">{fmtPctD(d.estimated_hit_rate)}</div>
              </div>
            )}
            {d.estimated_avg_hold_time_hours != null && (
              <div className="da-perf-item">
                <div className="da-perf-label">Avg Hold Time</div>
                <div className="da-perf-value">{d.estimated_avg_hold_time_hours.toFixed(1)} hr</div>
              </div>
            )}
            {d.estimated_sharpe_proxy != null && (
              <div className="da-perf-item">
                <div className="da-perf-label">Sharpe Proxy</div>
                <div className="da-perf-value">{d.estimated_sharpe_proxy.toFixed(2)}</div>
              </div>
            )}
          </div>
        </div>
      )}

      {(failures.length > 0 || risks.length > 0) && (
        <details className="da-risks">
          <summary>Risks</summary>
          <div className="da-risks-body">
            {failures.length > 0 && (
              <>
                <h3>Failure Modes</h3>
                <ul>{failures.map((f, i) => <li key={i}>{f}</li>)}</ul>
              </>
            )}
            {risks.length > 0 && (
              <>
                <h3>Risk Factors</h3>
                <ul>{risks.map((r, i) => <li key={i}>{r}</li>)}</ul>
              </>
            )}
          </div>
        </details>
      )}

      {d.full_thesis && (
        <div className="da-subsection">
          <div className="da-subsection-title">Full Thesis</div>
          <div className="da-thesis">
            {d.full_thesis.split(/\n\n+/).map((p, i) => <p key={i}>{p.trim()}</p>)}
          </div>
        </div>
      )}

      {d.paper_trade_recommendation && (
        <div className="da-callout" role="note">
          <div className="da-callout-label">Paper Trade Recommendation</div>
          <div className="da-callout-body">{d.paper_trade_recommendation}</div>
        </div>
      )}

      <div className="da-paper-test-row">
        {activePaperTests[addr] ? (
          <button
            className="da-paper-test-btn da-paper-test-btn-active"
            onClick={() => onClosePaperTestPanel(addr)}
          >
            Hide paper test
          </button>
        ) : (
          <button
            className="da-paper-test-btn"
            disabled={!d.paper_test_filter}
            title={d.paper_test_filter ? 'Start a paper test for this strategy' : 'No paper_test_filter available for this strategy'}
            onClick={() => {
              if (!d.paper_test_filter) return;
              const filter = d.paper_test_filter as Record<string, unknown>;
              const durationDays = typeof filter.duration_days === 'number' ? filter.duration_days : 7;
              const msg = `Start a paper test for this strategy?\n\nDuration: ${durationDays} days\nCapital allocated: $10,000`;
              if (!window.confirm(msg)) return;
              onStartPaperTest(addr, String(d.id), 10000);
            }}
          >
            Paper test this strategy
          </button>
        )}
      </div>

      {activePaperTests[addr] && (
        <PaperTestPanel
          paperTestId={activePaperTests[addr]}
          onClose={() => onClosePaperTestPanel(addr)}
        />
      )}

      <button className="da-history-link" onClick={() => onToggleHistory(addr)}>
        {histExp ? 'Hide analysis history' : 'View analysis history'}
      </button>

      {histExp && (
        <div className="da-history-list" aria-label="Previous analyses">
          {historyLoading.has(addr) ? (
            <div className="da-history-item" role="status">Loading history…</div>
          ) : historyError[addr] ? (
            <div className="da-history-item" style={{ color: 'var(--red)' }}>Failed to load history.</div>
          ) : !histItems || histItems.length === 0 ? (
            <div className="da-history-item">No previous analyses found.</div>
          ) : (
            histItems.map((h, i) => (
              <div className="da-history-item" key={i}>
                <strong>{timeAgo(h.generated_at)}</strong> &mdash;{' '}
                {h.strategy_type || '–'} · {h.is_replicable ? 'Replicable' : 'Not replicable'}
                {h.replicability_confidence != null && ` (${fmtPctD(h.replicability_confidence)} confidence)`}
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}

// ── Main Leaderboard component ────────────────────────────────────────────────

type ViewFilter = 'all' | 'watchlist' | 'new_activity' | 'strategies';

export default function Leaderboard() {
  const navigate = useNavigate();

  // Core data
  const [wallets, setWallets] = useState<Wallet[]>([]);
  const [dataLoading, setDataLoading] = useState(true);
  const [dataError, setDataError] = useState<string | null>(null);
  const [meta, setMeta] = useState<{ total: number; last_ranked_at?: string } | null>(null);

  // UI state
  const [sortKey, setSortKey] = useState('rank');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [openAddr, setOpenAddr] = useState<string | null>(null);
  const [viewFilter, setViewFilter] = useState<ViewFilter>('all');
  const [toastMsg, setToastMsg] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<{ id: string; email: string } | null>(null);

  // Strategy data — undefined means not fetched yet, null means 404
  const [strategyData, setStrategyData] = useState<Record<string, StrategyAnalysis | null>>({});
  const [strategyLoading, setStrategyLoading] = useState<Set<string>>(new Set());
  const [strategyError, setStrategyError] = useState<Record<string, string>>({});

  // Regen state
  const [regenState, setRegenState] = useState<Record<string, RegenState>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  // History
  const [historyData, setHistoryData] = useState<Record<string, StrategyAnalysis[]>>({});
  const [historyLoading, setHistoryLoading] = useState<Set<string>>(new Set());
  const [historyError, setHistoryError] = useState<Record<string, string>>({});
  const [historyExpanded, setHistoryExpanded] = useState<Set<string>>(new Set());

  // Strategies tab
  const [stratTabFetched, setStratTabFetched] = useState(false);
  const [stratTypeFilter, setStratTypeFilter] = useState('all');
  const [stratReplFilter, setStratReplFilter] = useState('all');

  // Paper tests: addr -> paper_test_id (for showing panel)
  const [activePaperTests, setActivePaperTests] = useState<Record<string, string>>({});

  // Cleanup timers on unmount
  useEffect(() => {
    return () => {
      Object.values(pollTimers.current).forEach(clearInterval);
    };
  }, []);

  // Toast auto-clear
  useEffect(() => {
    if (!toastMsg) return;
    const t = setTimeout(() => setToastMsg(null), 2000);
    return () => clearTimeout(t);
  }, [toastMsg]);

  // Load user from session
  useEffect(() => {
    const session = getSession();
    if (session) {
      setCurrentUser({ id: session.user.id, email: session.user.email });
    }
  }, []);

  // Load leaderboard data
  useEffect(() => {
    apiFetch<LeaderboardData>('/api/leaderboard?limit=50')
      .then((data) => {
        const w = Array.isArray(data) ? (data as unknown as Wallet[]) : (data.wallets || []);
        setWallets(w);
        if (!Array.isArray(data) && data.meta) setMeta(data.meta);
        setDataLoading(false);
      })
      .catch((err) => {
        if ((err as { status?: number })?.status === 401) return; // apiFetch handles redirect
        setDataError('Failed to load leaderboard. Please refresh.');
        setDataLoading(false);
      });
  }, []);

  // ── Strategy fetching ─────────────────────────────────────────────────────

  const fetchStrategy = useCallback((addr: string) => {
    setStrategyLoading((prev) => new Set(prev).add(addr));
    apiFetch<StrategyAnalysis>(`/api/wallet/${addr}/strategy`)
      .then((d) => {
        setStrategyData((prev) => ({ ...prev, [addr]: d }));
      })
      .catch((err) => {
        if (err && (err as { status?: number }).status === 404) {
          setStrategyData((prev) => ({ ...prev, [addr]: null }));
        } else {
          setStrategyError((prev) => ({ ...prev, [addr]: err?.message || 'Failed to load' }));
        }
      })
      .finally(() => {
        setStrategyLoading((prev) => {
          const next = new Set(prev);
          next.delete(addr);
          return next;
        });
      });
  }, []);

  // ── Strategies tab loading ─────────────────────────────────────────────────

  const loadStrategiesTab = useCallback(() => {
    setStratTabFetched(true);
    const top10 = wallets.filter((w) => w.rank <= 10);
    top10.forEach((w) => {
      if (strategyData[w.address] === undefined && !strategyLoading.has(w.address)) {
        fetchStrategy(w.address);
      }
    });
  }, [wallets, strategyData, strategyLoading, fetchStrategy]);

  // ── Regen ─────────────────────────────────────────────────────────────────

  function pollRegenJob(addr: string, jobId: string) {
    apiFetch<{ status: string; result?: StrategyAnalysis; error?: string }>(`/api/jobs/${jobId}`)
      .then((data) => {
        if (data.status === 'complete') {
          clearInterval(pollTimers.current[addr]);
          delete pollTimers.current[addr];
          if (data.result) {
            setStrategyData((prev) => ({ ...prev, [addr]: data.result! }));
          }
          setRegenState((prev) => ({ ...prev, [addr]: { loading: false, rateLimited: false } }));
        } else if (data.status === 'error') {
          clearInterval(pollTimers.current[addr]);
          delete pollTimers.current[addr];
          setRegenState((prev) => ({
            ...prev,
            [addr]: { loading: false, rateLimited: false, error: data.error || 'Analysis failed' },
          }));
        }
      })
      .catch(() => {});
  }

  function startRegen(addr: string) {
    const existing = regenState[addr];
    if (existing?.loading) return;
    setRegenState((prev) => ({ ...prev, [addr]: { loading: true, rateLimited: false } }));
    apiFetch<{ job_id: string; status: string }>(`/api/wallet/${addr}/strategy/regenerate`, {
      method: 'POST',
    })
      .then((data) => {
        const jobId = data.job_id;
        const timerId = setInterval(() => pollRegenJob(addr, jobId), 5000);
        pollTimers.current[addr] = timerId;
        setRegenState((prev) => ({ ...prev, [addr]: { loading: true, jobId, rateLimited: false } }));
      })
      .catch((err) => {
        if (err && (err as { status?: number }).status === 429) {
          setRegenState((prev) => ({ ...prev, [addr]: { loading: false, rateLimited: true } }));
        } else {
          setRegenState((prev) => ({
            ...prev,
            [addr]: { loading: false, rateLimited: false, error: err?.message || 'Request failed' },
          }));
        }
      });
  }

  // ── History ───────────────────────────────────────────────────────────────

  function toggleHistory(addr: string) {
    setHistoryExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(addr)) {
        next.delete(addr);
        return next;
      }
      next.add(addr);
      return next;
    });

    if (!historyData[addr] && !historyLoading.has(addr)) {
      setHistoryLoading((prev) => new Set(prev).add(addr));
      apiFetch<StrategyAnalysis[]>(`/api/wallet/${addr}/strategy/history`)
        .then((data) => {
          setHistoryData((prev) => ({ ...prev, [addr]: data }));
        })
        .catch((err) => {
          setHistoryError((prev) => ({ ...prev, [addr]: String(err) }));
        })
        .finally(() => {
          setHistoryLoading((prev) => {
            const next = new Set(prev);
            next.delete(addr);
            return next;
          });
        });
    }
  }

  // ── Row toggle ────────────────────────────────────────────────────────────

  function toggle(addr: string) {
    const wasOpen = openAddr === addr;
    if (wasOpen) {
      setOpenAddr(null);
      setStrategyData((prev) => {
        const next = { ...prev };
        delete next[addr];
        return next;
      });
      setStrategyError((prev) => {
        const next = { ...prev };
        delete next[addr];
        return next;
      });
      setRegenState((prev) => {
        const next = { ...prev };
        delete next[addr];
        return next;
      });
      setHistoryExpanded((prev) => {
        const next = new Set(prev);
        next.delete(addr);
        return next;
      });
      return;
    }

    setOpenAddr(addr);
    const w = wallets.find((x) => x.address === addr);

    // Mark as seen if watched with new activity
    if (w && w.is_watched && w.new_activity_count > 0) {
      apiFetch(`/api/watchlist/${addr}/seen`, { method: 'POST' })
        .then(() => {
          setWallets((prev) =>
            prev.map((x) => (x.address === addr ? { ...x, new_activity_count: 0 } : x)),
          );
        })
        .catch(() => {});
    }

    // Fetch strategy for top-10
    if (w && w.rank <= 10 && strategyData[addr] === undefined && !strategyLoading.has(addr)) {
      fetchStrategy(addr);
    }
  }

  // ── Watch toggle ──────────────────────────────────────────────────────────

  function watchToggle(e: React.MouseEvent, addr: string) {
    e.stopPropagation();
    const w = wallets.find((x) => x.address === addr);
    if (!w) return;
    if (w.is_watched) {
      apiFetch(`/api/watchlist/${addr}`, { method: 'DELETE' })
        .then(() => {
          setWallets((prev) =>
            prev.map((x) =>
              x.address === addr ? { ...x, is_watched: false, new_activity_count: 0 } : x,
            ),
          );
        })
        .catch(() => setToastMsg('Watchlist update failed'));
    } else {
      apiFetch('/api/watchlist', {
        method: 'POST',
        body: JSON.stringify({ wallet_address: addr }),
      })
        .then(() => {
          setWallets((prev) =>
            prev.map((x) => (x.address === addr ? { ...x, is_watched: true } : x)),
          );
        })
        .catch(() => setToastMsg('Watchlist update failed'));
    }
  }

  // ── Paper test ────────────────────────────────────────────────────────────

  function startPaperTest(addr: string, strategyId: string, capitalAllocated: number) {
    apiFetch<PaperTest>('/api/paper-tests', {
      method: 'POST',
      body: JSON.stringify({
        wallet_address: addr,
        strategy_analysis_id: strategyId,
        capital_allocated: capitalAllocated,
      }),
    })
      .then((pt) => {
        setActivePaperTests((prev) => ({ ...prev, [addr]: pt.id }));
        setToastMsg('Paper test started!');
      })
      .catch((err) => {
        setToastMsg('Failed to start paper test: ' + (err as Error).message);
      });
  }

  function closePaperTestPanel(addr: string) {
    setActivePaperTests((prev) => {
      const next = { ...prev };
      delete next[addr];
      return next;
    });
  }

  // ── Sort ─────────────────────────────────────────────────────────────────

  function handleSort(key: string) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir(key === 'rank' ? 'asc' : 'desc');
    }
  }

  function sortVal(w: Wallet, key: string): number {
    const m = w.metrics || {};
    switch (key) {
      case 'rank': return w.rank ?? Infinity;
      case 'pnl': return m.total_pnl ?? -Infinity;
      case 'volume': return m.total_volume ?? 0;
      case 'score': return w.composite_score ?? 0;
      case 'skill': return w.skill_signal ?? 0;
      default: return 0;
    }
  }

  // ── Filtering and sorting ─────────────────────────────────────────────────

  function getFiltered(): Wallet[] {
    if (viewFilter === 'watchlist') return wallets.filter((w) => w.is_watched);
    if (viewFilter === 'new_activity') return wallets.filter((w) => w.is_watched && w.new_activity_count > 0);
    if (viewFilter === 'strategies') return wallets.filter((w) => w.rank <= 10);
    return wallets;
  }

  function getSorted(): Wallet[] {
    return [...getFiltered()].sort((a, b) => {
      const d = sortVal(a, sortKey) - sortVal(b, sortKey);
      return sortDir === 'asc' ? d : -d;
    });
  }

  // ── Watchlist summary ─────────────────────────────────────────────────────

  const watched = wallets.filter((w) => w.is_watched);
  const withActivity = watched.filter((w) => w.new_activity_count > 0);
  const totalNew = withActivity.reduce((s, w) => s + w.new_activity_count, 0);

  // ── Sign out ──────────────────────────────────────────────────────────────

  function handleSignOut(e: React.MouseEvent) {
    e.preventDefault();
    signOut();
    navigate('/login', { replace: true });
  }

  // ── Handle filter change ──────────────────────────────────────────────────

  function handleSetFilter(f: ViewFilter) {
    setViewFilter(f);
    if (f === 'strategies' && !stratTabFetched) {
      loadStrategiesTab();
    }
  }

  // ── Strategies tab view ───────────────────────────────────────────────────

  function getStrategyTypes(): string[] {
    const types = new Set<string>();
    Object.values(strategyData).forEach((d) => {
      if (d && d.strategy_type) types.add(d.strategy_type);
    });
    return ['all', ...Array.from(types).sort()];
  }

  // ── Render ────────────────────────────────────────────────────────────────

  if (dataLoading) {
    return (
      <div id="app">
        <div className="state-msg">Loading…</div>
      </div>
    );
  }

  if (dataError) {
    return (
      <div id="app">
        <div className="state-msg">{dataError}</div>
      </div>
    );
  }

  const sorted = getSorted();

  const deepIconSvg = (
    <span
      className="deep-icon"
      title="Deep strategy analysis available — tap to view"
      role="img"
      aria-label="Deep strategy analysis available"
    >
      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true" focusable={false}>
        <circle cx="4" cy="3" r="2" fill="#58a6ff" />
        <rect x="2.5" y="5" width="3" height="1" rx="0.5" fill="#58a6ff" />
        <rect x="3.5" y="6" width="1" height="1.5" rx="0.5" fill="#58a6ff" />
      </svg>
    </span>
  );

  function renderStrategiesView() {
    const types = getStrategyTypes();
    const top10 = wallets.filter((w) => w.rank <= 10).sort((a, b) => a.rank - b.rank);
    const filtered = top10.filter((w) => {
      const d = strategyData[w.address];
      if (stratTypeFilter !== 'all' && d && d.strategy_type !== stratTypeFilter) return false;
      if (stratReplFilter === 'replicable' && d && !d.is_replicable) return false;
      if (stratReplFilter === 'not_replicable' && d && d.is_replicable) return false;
      return true;
    });

    return (
      <>
        <div className="strat-filters" aria-label="Strategy filters">
          <div className="strat-filter-group">
            <span className="strat-filter-label">Type:</span>
            {types.map((t) => (
              <button
                key={t}
                className={`strat-pill${stratTypeFilter === t ? ' active' : ''}`}
                onClick={(e) => { e.stopPropagation(); setStratTypeFilter(t); }}
              >
                {t === 'all' ? 'All Types' : t}
              </button>
            ))}
          </div>
          <div className="strat-filter-group">
            <span className="strat-filter-label">Replicable:</span>
            {(['all', 'replicable', 'not_replicable'] as const).map((r) => (
              <button
                key={r}
                className={`strat-pill${stratReplFilter === r ? ' active' : ''}`}
                onClick={(e) => { e.stopPropagation(); setStratReplFilter(r); }}
              >
                {r === 'all' ? 'All' : r === 'replicable' ? 'Replicable' : 'Not Replicable'}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="state-msg">No wallets match the selected filters.</div>
        ) : (
          <div className="strategies-list" role="list">
            {filtered.map((w) => {
              const m = w.metrics || {};
              const pnl = m.total_pnl;
              const pc = pnl == null ? 'neu' : pnl > 0 ? 'pos' : 'neg';
              return (
                <article
                  key={w.address}
                  className="strategy-card"
                  aria-label={`Wallet ${shortAddr(w.address)} strategy`}
                >
                  <div className="sc-header">
                    <span className="sc-rank">#{w.rank}</span>
                    <button
                      className="sc-addr"
                      title={w.address}
                      onClick={(e) => {
                        e.stopPropagation();
                        copyToClipboard(w.address, () => setToastMsg('Copied!'));
                      }}
                    >
                      {shortAddr(w.address)}
                    </button>
                    <span className={`sc-pnl ${pc}`}>{fmtPnl(pnl)}</span>
                  </div>
                  <div className="sc-body">
                    <StrategySection
                      addr={w.address}
                      rank={w.rank}
                      strategyData={strategyData}
                      strategyLoading={strategyLoading}
                      strategyError={strategyError}
                      regenState={regenState}
                      historyData={historyData}
                      historyLoading={historyLoading}
                      historyError={historyError}
                      historyExpanded={historyExpanded}
                      onRegen={startRegen}
                      onToggleHistory={toggleHistory}
                      activePaperTests={activePaperTests}
                      onStartPaperTest={startPaperTest}
                      onClosePaperTestPanel={closePaperTestPanel}
                    />
                  </div>
                </article>
              );
            })}
          </div>
        )}
      </>
    );
  }

  function renderTable() {
    if (!sorted.length) {
      let msg = '';
      if (viewFilter === 'watchlist') msg = "You haven't watched any wallets yet. Star a wallet to add it here.";
      else if (viewFilter === 'new_activity') msg = 'No new activity on your watched wallets.';
      else msg = 'No data yet — first scan in progress. Check back in an hour.';
      return <div className="state-msg">{msg}</div>;
    }

    return (
      <div className="table-wrap" role="region" aria-label="Wallet leaderboard">
        <table>
          <thead>
            <tr>
              {[
                { key: 'rank', label: 'Rank', sort: true },
                { key: 'watch', label: '', sort: false, cls: 'col-watch' },
                { key: 'addr', label: 'Wallet', sort: false },
                { key: 'pnl', label: 'P&L', sort: true },
                { key: 'volume', label: 'Volume', sort: true, cls: 'col-vol' },
                { key: 'score', label: 'Score', sort: true },
                { key: 'skill', label: 'Skill', sort: true, cls: 'col-skill' },
                { key: 'edge', label: 'Edge Hypothesis', sort: false },
                { key: 'flags', label: 'Flags', sort: false },
              ].map((col) => {
                const sortCls = col.sort
                  ? ` sort${sortKey === col.key ? ` ${sortDir}` : ''}`
                  : '';
                return (
                  <th
                    key={col.key}
                    className={`${col.cls || ''}${sortCls}`}
                    onClick={col.sort ? () => handleSort(col.key) : undefined}
                  >
                    {col.label}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((w) => {
              const m = w.metrics || {};
              const pnl = m.total_pnl;
              const pc = pnl == null ? 'neu' : pnl > 0 ? 'pos' : 'neg';
              const s = w.composite_score;
              const sk = w.skill_signal;
              const edge = w.edge_hypothesis || '';
              const edgeTrunc = edge.length > 80 ? edge.slice(0, 80) + '…' : edge;
              const isOpen = openAddr === w.address;

              return [
                <tr
                  key={w.address}
                  className={`row${isOpen ? ' open' : ''}`}
                  onClick={() => toggle(w.address)}
                >
                  <td>
                    <span className={`rank${w.rank <= 10 ? ' bold' : ''}`}>
                      #{w.rank}
                      {w.rank <= 10 && deepIconSvg}
                    </span>
                  </td>
                  <td className="col-watch">
                    <div className="watch-cell">
                      <button
                        className={`star-btn${w.is_watched ? ' watching' : ''}`}
                        title={w.is_watched ? 'Remove from watchlist' : 'Add to watchlist'}
                        onClick={(e) => watchToggle(e, w.address)}
                      >
                        {w.is_watched ? '★' : '☆'}
                      </button>
                      {w.new_activity_count > 0 && (
                        <span
                          className="activity-dot"
                          title={`${w.new_activity_count} new position${w.new_activity_count === 1 ? '' : 's'} since you last viewed`}
                        />
                      )}
                    </div>
                  </td>
                  <td>
                    <button
                      className="addr-btn"
                      title={w.address}
                      onClick={(e) => {
                        e.stopPropagation();
                        copyToClipboard(w.address, () => setToastMsg('Copied!'));
                      }}
                    >
                      {shortAddr(w.address)}
                    </button>
                  </td>
                  <td><span className={pc}>{fmtPnl(pnl)}</span></td>
                  <td className="col-vol"><span className="vol">{fmtVol(m.total_volume)}</span></td>
                  <td>
                    {s != null ? (
                      <span className={`badge ${badgeClass(s)}`}>{s.toFixed(3)}</span>
                    ) : (
                      <span className="neu">–</span>
                    )}
                  </td>
                  <td className="col-skill">
                    {sk != null ? (
                      <div className="skill-wrap">
                        <div className="skill-track">
                          <div className="skill-fill" style={{ width: `${Math.round(sk * 100)}%` }} />
                        </div>
                        <span className="skill-num">{sk.toFixed(2)}</span>
                      </div>
                    ) : (
                      <span className="neu">–</span>
                    )}
                  </td>
                  <td>
                    <span className="edge" title={edge}>
                      {edgeTrunc || <span className="neu">–</span>}
                    </span>
                  </td>
                  <td>
                    <div className="flags">
                      <FlagPills flags={w.red_flags || []} />
                    </div>
                  </td>
                </tr>,

                isOpen && (
                  <tr key={`${w.address}-detail`} className="detail-row">
                    <td colSpan={9} className="detail-cell">
                      <div className="detail-inner">
                        <div className="detail-metrics">
                          {[
                            ['Trade Count', String(m.trade_count ?? '–')],
                            ['Total P&L', fmtPnl(m.total_pnl)],
                            ['Total Volume', fmtUsd(m.total_volume)],
                            ['Market Count', String(m.market_count ?? '–')],
                            ['Realized Pos.', String(m.realized_position_count ?? '–')],
                            ['Unresolved Pos.', String(m.unresolved_position_count ?? '–')],
                            ['P&L from Top 3', fmtPct(m.pct_pnl_from_top_3_positions)],
                            ['Portfolio Value', m.portfolio_value != null ? fmtUsd(m.portfolio_value) : '–'],
                          ].map(([label, val]) => (
                            <div key={label}>
                              <div className="dm-label">{label}</div>
                              <div className="dm-value">{val}</div>
                            </div>
                          ))}
                        </div>

                        {w.edge_hypothesis && (
                          <div className="detail-sec">
                            <div className="detail-sec-label">Edge Hypothesis</div>
                            <div className="detail-sec-body">{w.edge_hypothesis}</div>
                          </div>
                        )}

                        {w.claude_notes && (
                          <div className="detail-sec">
                            <div className="detail-sec-label">Claude Notes</div>
                            <div className="detail-sec-body">{w.claude_notes}</div>
                          </div>
                        )}

                        {((w.heuristic_red_flags?.length || 0) > 0 || (w.claude_red_flags?.length || 0) > 0) && (
                          <div className="detail-sec">
                            <div className="detail-sec-label">Red Flags</div>
                            <div className="detail-flags">
                              <FlagPills flags={w.heuristic_red_flags || []} forceClass="k" />
                              <FlagPills flags={w.claude_red_flags || []} />
                            </div>
                          </div>
                        )}

                        <StrategySection
                          addr={w.address}
                          rank={w.rank}
                          strategyData={strategyData}
                          strategyLoading={strategyLoading}
                          strategyError={strategyError}
                          regenState={regenState}
                          historyData={historyData}
                          historyLoading={historyLoading}
                          historyError={historyError}
                          historyExpanded={historyExpanded}
                          onRegen={startRegen}
                          onToggleHistory={toggleHistory}
                          activePaperTests={activePaperTests}
                          onStartPaperTest={startPaperTest}
                          onClosePaperTestPanel={closePaperTestPanel}
                        />

                        <div className="detail-actions">
                          <a
                            className="action-link"
                            href={`https://polymarket.com/profile/${w.address}`}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            View on Polymarket ↗
                          </a>
                          <button
                            className="action-link"
                            onClick={(e) => {
                              e.stopPropagation();
                              copyToClipboard(w.address, () => setToastMsg('Copied!'));
                            }}
                          >
                            Copy full address
                          </button>
                          <button
                            className={`star-btn${w.is_watched ? ' watching' : ''}`}
                            style={{ fontSize: 13 }}
                            onClick={(e) => watchToggle(e, w.address)}
                          >
                            {w.is_watched ? '★ Watching' : '☆ Watch'}
                          </button>
                        </div>
                      </div>
                    </td>
                  </tr>
                ),
              ];
            })}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div id="app">
      <header>
        <div className="header-top">
          <h1>Wallet Scanner</h1>
          <div className="header-user">
            {currentUser && (
              <>
                <span>Signed in as {currentUser.email}</span>
                <a href="/paper-tests">Paper Tests</a>
                <a href="#" onClick={handleSignOut}>Sign out</a>
              </>
            )}
          </div>
        </div>
        <div className="header-meta">
          {meta?.last_ranked_at && <span>Updated {timeAgo(meta.last_ranked_at)}</span>}
          {meta?.total && (
            <span>Showing top {wallets.length} of {meta.total} ranked wallets</span>
          )}
        </div>
        {watched.length > 0 && (
          <div className="watchlist-summary">
            👁 Watching {watched.length === 1 ? '1 wallet' : `${watched.length} wallets`}
            {withActivity.length === 0 ? (
              ' · all caught up'
            ) : (
              <>
                {' · '}
                <a onClick={() => handleSetFilter('new_activity')} href="#">
                  {withActivity.length === 1 ? '1 with new activity' : `${withActivity.length} with new activity`}
                  {' '}({totalNew === 1 ? '1 new position' : `${totalNew} new positions`})
                </a>
              </>
            )}
          </div>
        )}
      </header>

      <div className="filter-tabs" role="tablist">
        {([
          { key: 'all', label: 'All Wallets' },
          { key: 'watchlist', label: 'Watchlist' },
          { key: 'new_activity', label: 'New Activity' },
          { key: 'strategies', label: 'Strategies' },
        ] as { key: ViewFilter; label: string }[]).map((t) => (
          <button
            key={t.key}
            role="tab"
            aria-selected={viewFilter === t.key}
            className={`tab-btn${viewFilter === t.key ? ' active' : ''}`}
            onClick={() => handleSetFilter(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <main>
        {viewFilter === 'strategies' ? renderStrategiesView() : renderTable()}
      </main>

      {toastMsg && <div className={`toast show`}>{toastMsg}</div>}
    </div>
  );
}
