export function fmtMoney(n: number | null | undefined, currency = "USD"): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(n);
}

export function fmtNumber(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function fmtPercent(n: number | null | undefined, digits = 2): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(digits)}%`;
}

export function fmtScore(s: number | null | undefined): string {
  if (s === null || s === undefined || Number.isNaN(s)) return "—";
  return s.toFixed(2);
}

export const CONVICTION_LABEL: Record<string, string> = {
  very_high: "Very High Conviction",
  high: "High Conviction",
  moderate: "Moderate Conviction",
  selective: "Selective / Cautious",
  avoid: "Avoid / Monitor",
};

export const CONVICTION_TONE: Record<string, string> = {
  very_high: "text-accent",
  high: "text-accent/90",
  moderate: "text-amber-400",
  selective: "text-warn",
  avoid: "text-danger",
};
