"use client";

import { cn } from "@/lib/cn";

interface ScoreBarProps {
  score: number; // 1.0 – 5.0
  max?: number;
  className?: string;
}

export function ScoreBar({ score, max = 5, className }: ScoreBarProps) {
  const pct = Math.max(0, Math.min(100, (score / max) * 100));
  const tone =
    score >= 4.5
      ? "bg-accent"
      : score >= 4.0
      ? "bg-accent/80"
      : score >= 3.5
      ? "bg-amber-400"
      : score >= 3.0
      ? "bg-warn"
      : "bg-danger";
  return (
    <div className={cn("h-2 w-full rounded-full bg-muted/40 overflow-hidden", className)}>
      <div className={cn("h-full rounded-full transition-all", tone)} style={{ width: `${pct}%` }} />
    </div>
  );
}
