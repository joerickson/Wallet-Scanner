import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { apiFetch } from '../lib/api';
import type { WatchlistEntry } from '../types';

function shortAddr(a: string): string {
  return a && a.length >= 10 ? a.slice(0, 6) + '…' + a.slice(-4) : a || '–';
}

function fmtDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function Watchlist() {
  const navigate = useNavigate();
  const [entries, setEntries] = useState<WatchlistEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!toastMsg) return;
    const t = setTimeout(() => setToastMsg(null), 2000);
    return () => clearTimeout(t);
  }, [toastMsg]);

  useEffect(() => {
    apiFetch<WatchlistEntry[]>('/api/watchlist')
      .then((data) => {
        setEntries(data);
        setLoading(false);
      })
      .catch((err) => {
        if (err && (err as { status?: number }).status === 401) {
          navigate('/login', { replace: true });
          return;
        }
        setError('Failed to load watchlist.');
        setLoading(false);
      });
  }, [navigate]);

  function handleRemove(addr: string) {
    apiFetch(`/api/watchlist/${addr}`, { method: 'DELETE' })
      .then(() => {
        setEntries((prev) => prev.filter((e) => e.wallet_address !== addr));
        setToastMsg('Removed from watchlist');
      })
      .catch(() => setToastMsg('Failed to remove'));
  }

  function copyAddr(addr: string) {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(addr).then(() => setToastMsg('Copied!'));
    } else {
      setToastMsg('Copied!');
    }
  }

  return (
    <div className="watchlist-page">
      <div className="watchlist-header">
        <h1>Watchlist</h1>
        <button className="action-link" onClick={() => navigate('/')}>
          ← Back to leaderboard
        </button>
      </div>

      {loading && <div className="state-msg">Loading…</div>}
      {error && <div className="state-msg">{error}</div>}

      {!loading && !error && entries.length === 0 && (
        <div className="state-msg">
          No wallets in your watchlist yet.{' '}
          <button className="action-link" onClick={() => navigate('/')}>
            Go to leaderboard
          </button>{' '}
          to star wallets.
        </div>
      )}

      {entries.map((e) => (
        <div key={e.wallet_address} className="watchlist-entry">
          <button
            className="we-addr"
            title={e.wallet_address}
            onClick={() => copyAddr(e.wallet_address)}
          >
            {shortAddr(e.wallet_address)}
          </button>
          <a
            href={`https://polymarket.com/profile/${e.wallet_address}`}
            target="_blank"
            rel="noopener noreferrer"
            className="action-link"
            onClick={(ev) => ev.stopPropagation()}
          >
            Polymarket ↗
          </a>
          <span className="we-added">Added {fmtDate(e.added_at)}</span>
          <button className="we-remove" onClick={() => handleRemove(e.wallet_address)}>
            Remove
          </button>
        </div>
      ))}

      {toastMsg && <div className="toast show">{toastMsg}</div>}
    </div>
  );
}
