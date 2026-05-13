export interface Citation {
  document: string;
  page: number;
  snippet: string;
}

export interface Setup {
  symbol: string;
  timeframe: string;
  name: string;
  direction: "long" | "short";
  entry: number;
  stop_loss: number;
  targets: number[];
  current_price: number;
  conviction: number;
  risk_reward: number;
  reasoning: string;
  citations: Citation[];
  detected_at: string;
}

export interface ScanResponse {
  timeframe: string;
  setups: Setup[];
  scanned: number;
  skipped: number;
  duration_ms: number;
}

export interface ChartLevel {
  label: string;
  value: number;
  kind: "support" | "resistance" | "ema" | "fib" | "entry" | "stop" | "target";
}

export interface ChartPayload {
  symbol: string;
  timeframe: string;
  bars: { t: string; o: number; h: number; l: number; c: number; v: number }[];
  levels: ChartLevel[];
  setups: Setup[];
}
