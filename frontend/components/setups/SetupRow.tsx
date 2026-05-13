"use client";

import Link from "next/link";
import { TrendingUp, TrendingDown, BookText } from "lucide-react";
import type { Setup } from "@/lib/setups";
import { fmtMoney, fmtNumber } from "@/lib/format";
import { cn } from "@/lib/cn";

export function SetupRow({ s }: { s: Setup }) {
  const long = s.direction === "long";
  const tone = long ? "text-accent" : "text-danger";
  const Icon = long ? TrendingUp : TrendingDown;
  const movePct =
    ((long ? s.targets[0] - s.entry : s.entry - s.targets[0]) /
      s.entry) *
    100;
  return (
    <tr className="border-t border-border hover:bg-muted/10">
      <td className="py-3 pl-3">
        <Link
          href={`/setups/${s.symbol}`}
          className="font-semibold hover:text-accent"
        >
          {s.symbol}
        </Link>
        <div className="text-[10px] uppercase text-muted">{s.timeframe}</div>
      </td>
      <td className="py-3">
        <div className={cn("flex items-center gap-1.5 text-sm", tone)}>
          <Icon size={14} />
          <span>{s.name}</span>
        </div>
        <div className="text-xs text-muted line-clamp-1">{s.reasoning}</div>
      </td>
      <td className="py-3 text-right font-mono text-xs">{fmtMoney(s.current_price)}</td>
      <td className="py-3 text-right font-mono text-xs">{fmtMoney(s.entry)}</td>
      <td className="py-3 text-right font-mono text-xs text-danger">
        {fmtMoney(s.stop_loss)}
      </td>
      <td className="py-3 text-right font-mono text-xs text-accent">
        {s.targets.map((t, i) => (
          <div key={i}>{fmtMoney(t)}</div>
        ))}
      </td>
      <td className="py-3 text-right text-xs">
        <div className="font-mono">{fmtNumber(s.risk_reward, 2)}R</div>
        <div className="text-muted">{movePct >= 0 ? "+" : ""}{movePct.toFixed(1)}%</div>
      </td>
      <td className="py-3 text-right">
        <ConvictionPill v={s.conviction} />
      </td>
      <td className="py-3 pr-3 text-right">
        {s.citations.length > 0 && (
          <span
            className="inline-flex items-center gap-1 text-xs text-muted"
            title={s.citations.map((c) => `${c.document} p.${c.page}`).join("\n")}
          >
            <BookText size={12} />
            {s.citations.length}
          </span>
        )}
      </td>
    </tr>
  );
}

function ConvictionPill({ v }: { v: number }) {
  const pct = Math.round(v * 100);
  const tone =
    v >= 0.8
      ? "bg-accent text-black"
      : v >= 0.65
      ? "bg-accent/20 text-accent"
      : v >= 0.5
      ? "bg-amber-400/20 text-amber-400"
      : "bg-muted/30 text-muted";
  return (
    <span className={cn("inline-block rounded px-1.5 py-0.5 text-[10px] font-mono", tone)}>
      {pct}%
    </span>
  );
}
