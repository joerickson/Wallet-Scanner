export interface WalletMetrics {
  trade_count?: number;
  total_pnl?: number;
  total_volume?: number;
  portfolio_value?: number;
  realized_position_count?: number;
  unresolved_position_count?: number;
  market_count?: number;
  avg_position_size?: number;
  max_position_size_usd?: number;
  pct_pnl_from_top_3_positions?: number;
  computed_at?: string;
}

export interface Wallet {
  address: string;
  rank: number;
  composite_score?: number;
  skill_signal?: number;
  edge_hypothesis?: string;
  claude_notes?: string;
  heuristic_red_flags: string[];
  claude_red_flags: string[];
  red_flags: string[];
  is_watched: boolean;
  new_activity_count: number;
  metrics?: WalletMetrics;
  ranked_at: string;
}

export interface LeaderboardData {
  meta: {
    total: number;
    showing: number;
    last_ranked_at?: string;
  };
  wallets: Wallet[];
}

export interface StrategyAnalysis {
  id: string;
  wallet_address: string;
  is_replicable: boolean;
  replicability_confidence?: number;
  capital_required_min_usd?: number;
  strategy_type?: string;
  strategy_subtype?: string;
  entry_signal?: string;
  exit_signal?: string;
  position_sizing_rule?: string;
  market_selection_criteria?: string;
  infrastructure_required?: string;
  estimated_hit_rate?: number;
  estimated_avg_hold_time_hours?: number;
  estimated_sharpe_proxy?: number;
  failure_modes: string[];
  risk_factors: string[];
  prompt_version?: string;
  model_used?: string;
  generated_at: string;
  wallet_state_snapshot?: Record<string, unknown>;
  full_thesis?: string;
  paper_trade_recommendation?: string;
  paper_test_filter?: Record<string, unknown> | null;
}

export interface PaperTrade {
  id: string;
  paper_test_id: string;
  polymarket_condition_id: string;
  market_question: string;
  outcome_name: string;
  token_id: string;
  side: string;
  entry_price: number;
  entry_size_usd: number;
  entry_at: string;
  exit_price?: number | null;
  exit_at?: string | null;
  exit_reason?: string | null;
  realized_pnl?: number | null;
  status: string;
}

export interface PaperTest {
  id: string;
  wallet_address: string;
  strategy_analysis_id: number;
  user_id: string;
  capital_allocated: number;
  started_at: string;
  ends_at: string;
  status: string;
  realized_pnl: number;
  unrealized_pnl: number;
  last_evaluated_at?: string | null;
  filter_snapshot?: Record<string, unknown>;
  created_at: string;
  trades?: PaperTrade[];
}

export interface WatchlistEntry {
  wallet_address: string;
  added_at: string;
  notes?: string;
}

export interface RegenState {
  loading: boolean;
  jobId?: string;
  error?: string;
  rateLimited: boolean;
}

export interface CurrentUser {
  id: string;
  email: string;
  name?: string;
}
