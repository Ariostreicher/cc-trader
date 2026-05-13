"use client";

import { use } from "react";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";
import type { ChartPayload } from "@/lib/setups";
import { SetupRow } from "@/components/setups/SetupRow";
import { fmtMoney } from "@/lib/format";

export default function SymbolPage({
  params,
}: {
  params: Promise<{ symbol: string }>;
}) {
  const { symbol } = use(params);
  return (
    <AppShell>
      <Inner symbol={symbol.toUpperCase()} />
    </AppShell>
  );
}

function Inner({ symbol }: { symbol: string }) {
  const { data, isLoading } = useQuery<ChartPayload>({
    queryKey: ["chart", symbol],
    queryFn: async () => (await api.get(`/setups/chart/${symbol}?days=180&timeframe=1d`)).data,
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-5 max-w-6xl">
      <header>
        <h1 className="text-2xl font-bold">{symbol}</h1>
        <p className="text-muted text-sm">Live CC analysis · daily bars · last 180 days</p>
      </header>

      {isLoading && (
        <Card>
          <CardBody className="text-muted text-sm">Loading bars…</CardBody>
        </Card>
      )}

      {data && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Chart</CardTitle>
              <CardSubtitle>
                Price + Chart Champions overlays (EMAs, S/R clusters, Fibonacci levels). The
                horizontal lines are levels the detectors are watching.
              </CardSubtitle>
            </CardHeader>
            <CardBody>
              <SVGChart payload={data} />
            </CardBody>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                Active setups{" "}
                <span className="text-xs font-normal text-muted ml-2">
                  ({data.setups.length})
                </span>
              </CardTitle>
            </CardHeader>
            <CardBody className="overflow-x-auto p-0">
              {data.setups.length === 0 ? (
                <div className="p-6 text-sm text-muted">
                  No setups firing on this symbol right now. The chart overlays still show the CC
                  context (EMAs, S/R, fib).
                </div>
              ) : (
                <table className="w-full text-sm">
                  <thead className="text-muted text-xs uppercase">
                    <tr>
                      <th className="text-left py-2 pl-3">Symbol</th>
                      <th className="text-left">Setup</th>
                      <th className="text-right">Price</th>
                      <th className="text-right">Entry</th>
                      <th className="text-right">Stop</th>
                      <th className="text-right">Targets</th>
                      <th className="text-right">R / move</th>
                      <th className="text-right">Conv</th>
                      <th className="text-right pr-3">Cites</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.setups.map((s, i) => (
                      <SetupRow key={i} s={s} />
                    ))}
                  </tbody>
                </table>
              )}
            </CardBody>
          </Card>
        </>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Lightweight SVG candlestick chart with horizontal level overlays.
// Not a full TradingView clone — enough to see the levels in context.
// ----------------------------------------------------------------------
function SVGChart({ payload }: { payload: ChartPayload }) {
  if (!payload.bars.length) return <div className="text-muted text-sm">No bars.</div>;
  const W = 1000;
  const H = 420;
  const PAD_L = 56;
  const PAD_R = 12;
  const PAD_T = 12;
  const PAD_B = 26;

  const bars = payload.bars;
  const yVals = bars.flatMap((b) => [b.h, b.l]);
  // include level values too so they're in view
  payload.levels.forEach((l) => yVals.push(l.value));
  const yMin = Math.min(...yVals);
  const yMax = Math.max(...yVals);
  const yPad = (yMax - yMin) * 0.04 || 1;
  const lo = yMin - yPad;
  const hi = yMax + yPad;

  const xStep = (W - PAD_L - PAD_R) / Math.max(1, bars.length - 1);
  const candleW = Math.max(1.5, xStep * 0.7);

  const yFor = (v: number) =>
    PAD_T + ((hi - v) / (hi - lo)) * (H - PAD_T - PAD_B);
  const xFor = (i: number) => PAD_L + i * xStep;

  const levelColor = (k: string) => {
    switch (k) {
      case "support":
        return "rgb(34 197 94 / 0.75)";
      case "resistance":
        return "rgb(239 68 68 / 0.75)";
      case "ema":
        return "rgb(148 163 184 / 0.85)";
      case "fib":
        return "rgb(250 204 21 / 0.6)";
      default:
        return "rgb(255 255 255 / 0.4)";
    }
  };

  // Pick a few y-ticks
  const yTicks: number[] = [];
  const nTicks = 5;
  for (let i = 0; i <= nTicks; i++) {
    yTicks.push(lo + ((hi - lo) * i) / nTicks);
  }

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full">
      {/* background */}
      <rect x={0} y={0} width={W} height={H} fill="transparent" />

      {/* y grid + labels */}
      {yTicks.map((v, i) => (
        <g key={i}>
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={yFor(v)}
            y2={yFor(v)}
            stroke="rgb(255 255 255 / 0.06)"
          />
          <text
            x={PAD_L - 6}
            y={yFor(v) + 3}
            textAnchor="end"
            fontSize={10}
            fill="rgb(148 163 184)"
          >
            {fmtMoney(v).replace("$", "")}
          </text>
        </g>
      ))}

      {/* candles */}
      {bars.map((b, i) => {
        const cx = xFor(i);
        const up = b.c >= b.o;
        const color = up ? "rgb(34 197 94)" : "rgb(239 68 68)";
        return (
          <g key={i}>
            <line x1={cx} x2={cx} y1={yFor(b.h)} y2={yFor(b.l)} stroke={color} strokeWidth={1} />
            <rect
              x={cx - candleW / 2}
              y={yFor(Math.max(b.o, b.c))}
              width={candleW}
              height={Math.max(1, Math.abs(yFor(b.o) - yFor(b.c)))}
              fill={color}
              opacity={up ? 0.9 : 0.95}
            />
          </g>
        );
      })}

      {/* levels */}
      {payload.levels.map((l, i) => (
        <g key={`lvl-${i}`}>
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={yFor(l.value)}
            y2={yFor(l.value)}
            stroke={levelColor(l.kind)}
            strokeWidth={1}
            strokeDasharray={l.kind === "fib" ? "3 3" : "4 2"}
          />
          <text
            x={W - PAD_R - 4}
            y={yFor(l.value) - 2}
            textAnchor="end"
            fontSize={9}
            fill={levelColor(l.kind)}
          >
            {l.label} {fmtMoney(l.value)}
          </text>
        </g>
      ))}

      {/* setup entry/stop/target rails */}
      {payload.setups.slice(0, 1).map((s, i) => (
        <g key={`setup-${i}`}>
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={yFor(s.entry)}
            y2={yFor(s.entry)}
            stroke="white"
            strokeWidth={1.5}
            strokeDasharray="5 3"
          />
          <text x={PAD_L + 4} y={yFor(s.entry) - 3} fontSize={10} fill="white">
            Entry {fmtMoney(s.entry)}
          </text>
          <line
            x1={PAD_L}
            x2={W - PAD_R}
            y1={yFor(s.stop_loss)}
            y2={yFor(s.stop_loss)}
            stroke="rgb(239 68 68)"
            strokeWidth={1.5}
            strokeDasharray="5 3"
          />
          <text x={PAD_L + 4} y={yFor(s.stop_loss) + 12} fontSize={10} fill="rgb(239 68 68)">
            Stop {fmtMoney(s.stop_loss)}
          </text>
          {s.targets.map((t, ti) => (
            <g key={ti}>
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={yFor(t)}
                y2={yFor(t)}
                stroke="rgb(34 197 94)"
                strokeWidth={1}
                strokeDasharray="4 4"
              />
              <text x={PAD_L + 4} y={yFor(t) - 3} fontSize={10} fill="rgb(34 197 94)">
                Target {ti + 1} {fmtMoney(t)}
              </text>
            </g>
          ))}
        </g>
      ))}
    </svg>
  );
}
