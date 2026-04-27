import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../lib/api';
import type { PaperTest } from '../types';

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function fmtPnl(v: number): string {
  const abs = Math.abs(v);
  const sign = v < 0 ? '-' : v > 0 ? '+' : '';
  return sign + '$' + (abs >= 1000 ? Math.round(abs).toLocaleString('en-US') : abs.toFixed(2));
}

function shortAddr(a: string): string {
  return a && a.length >= 10 ? a.slice(0, 6) + '…' + a.slice(-4) : a || '–';
}

type SortKey = 'started_at' | 'status' | 'pnl';

export default function PaperTests() {
  const navigate = useNavigate();
  const [tests, setTests] = useState<PaperTest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>('started_at');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  useEffect(() => {
    apiFetch<PaperTest[]>('/api/paper-tests')
      .then((data) => {
        setTests(data);
        setLoading(false);
      })
      .catch((err) => {
        if ((err as { status?: number })?.status === 401) return;
        setError('Failed to load paper tests.');
        setLoading(false);
      });
  }, []);

  function handleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  }

  function getSorted(): PaperTest[] {
    return [...tests].sort((a, b) => {
      let av: number, bv: number;
      if (sortKey === 'started_at') {
        av = new Date(a.started_at).getTime();
        bv = new Date(b.started_at).getTime();
      } else if (sortKey === 'status') {
        av = a.status === 'running' ? 1 : 0;
        bv = b.status === 'running' ? 1 : 0;
      } else {
        av = a.realized_pnl + a.unrealized_pnl;
        bv = b.realized_pnl + b.unrealized_pnl;
      }
      return sortDir === 'desc' ? bv - av : av - bv;
    });
  }

  function thCls(key: SortKey) {
    return `sort${sortKey === key ? ` ${sortDir}` : ''}`;
  }

  return (
    <div id="app">
      <header>
        <div className="header-top">
          <h1>Paper Tests</h1>
          <div className="header-user">
            <a href="/" onClick={(e) => { e.preventDefault(); navigate('/'); }}>← Leaderboard</a>
          </div>
        </div>
      </header>

      <main>
        {loading && <div className="state-msg">Loading…</div>}
        {error && <div className="state-msg">{error}</div>}
        {!loading && !error && tests.length === 0 && (
          <div className="state-msg">
            No paper tests yet. Open a wallet strategy and click "Paper test this strategy" to get started.
          </div>
        )}
        {!loading && !error && tests.length > 0 && (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Wallet</th>
                  <th className={thCls('started_at')} onClick={() => handleSort('started_at')}>
                    Started
                  </th>
                  <th>Ends</th>
                  <th className={thCls('status')} onClick={() => handleSort('status')}>
                    Status
                  </th>
                  <th>Capital</th>
                  <th className={thCls('pnl')} onClick={() => handleSort('pnl')}>
                    P&L
                  </th>
                  <th>Trades</th>
                </tr>
              </thead>
              <tbody>
                {getSorted().map((t) => {
                  const totalPnl = t.realized_pnl + t.unrealized_pnl;
                  const pnlCls = totalPnl > 0 ? 'pos' : totalPnl < 0 ? 'neg' : 'neu';
                  const tradeCount = t.trades?.length ?? '–';
                  return (
                    <tr key={t.id}>
                      <td>
                        <span className="addr-btn" title={t.wallet_address}>
                          {shortAddr(t.wallet_address)}
                        </span>
                      </td>
                      <td>{fmtDate(t.started_at)}</td>
                      <td>{fmtDate(t.ends_at)}</td>
                      <td>
                        <span className={`pt-status pt-status-${t.status}`}>{t.status}</span>
                      </td>
                      <td>${t.capital_allocated.toLocaleString('en-US')}</td>
                      <td><span className={pnlCls}>{fmtPnl(totalPnl)}</span></td>
                      <td>{tradeCount}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </main>
    </div>
  );
}
