// Shared types matching the FastAPI Pydantic schemas.

export type UserRole = "user" | "admin";
export type SubscriptionTier = "free" | "pro" | "enterprise";

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
  is_verified: boolean;
  tier: SubscriptionTier;
  created_at: string;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
}

export type ConvictionLevel =
  | "very_high"
  | "high"
  | "moderate"
  | "selective"
  | "avoid";

export interface CategoryScore {
  category: string;
  score: number;
  summary: string | null;
}

export interface EquityReport {
  id: string;
  ticker: string;
  company_name: string | null;
  sections: Record<string, { title: string; analysis: string; [k: string]: any }>;
  composite_score: number;
  conviction: ConvictionLevel;
  investment_stance: string | null;
  bull_target: number | null;
  base_target: number | null;
  bear_target: number | null;
  anchor_price: number | null;
  invalidation_triggers: string[] | null;
  category_scores: CategoryScore[];
  citations: Array<{ page: number; document_id: string; filename: string; score: number }> | null;
  model_used: string | null;
  tokens_used: number | null;
  created_at: string;
}

export interface EquityReportSummary {
  id: string;
  ticker: string;
  company_name: string | null;
  composite_score: number;
  conviction: ConvictionLevel;
  investment_stance: string | null;
  created_at: string;
}

export type AssetClass = "stock" | "etf" | "crypto" | "index" | "forex";

export interface WatchlistAsset {
  id: string;
  symbol: string;
  exchange: string | null;
  asset_class: AssetClass;
  sector: string | null;
  position: number;
  note: string | null;
  created_at: string;
}

export interface Watchlist {
  id: string;
  name: string;
  description: string | null;
  is_public: boolean;
  pinned: boolean;
  created_at: string;
  assets: WatchlistAsset[];
}

export type AlertTrigger =
  | "price_above"
  | "price_below"
  | "rsi_above"
  | "rsi_below"
  | "macd_cross_up"
  | "macd_cross_down"
  | "volume_spike"
  | "sr_break_above"
  | "sr_break_below"
  | "sr_bounce"
  | "ema_cross_up"
  | "ema_cross_down"
  | "ai_confidence_above"
  | "custom";

export interface Alert {
  id: string;
  symbol: string;
  trigger: AlertTrigger;
  params: Record<string, any>;
  cooldown_seconds: number;
  channels: string[];
  note: string | null;
  is_enabled: boolean;
  last_triggered_at: string | null;
  created_at: string;
}

export interface DocumentRecord {
  id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  status: "pending" | "extracting" | "chunking" | "embedding" | "ready" | "failed";
  page_count: number | null;
  extracted_text_len: number | null;
  error: string | null;
  created_at: string;
}

export interface Indicators {
  symbol: string;
  timeframe: string;
  rsi: number | null;
  macd: number | null;
  macd_signal: number | null;
  macd_hist: number | null;
  ema_55: number | null;
  ema_100: number | null;
  ema_200: number | null;
  vwap: number | null;
  atr_14: number | null;
  bollinger_mid: number | null;
  bollinger_upper: number | null;
  bollinger_lower: number | null;
  support: number[];
  resistance: number[];
}

export interface Quote {
  symbol: string;
  price: number;
  timestamp: string;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
}
