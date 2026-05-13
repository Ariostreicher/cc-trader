"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Search, Filter } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { SetupRow } from "@/components/setups/SetupRow";
import { api } from "@/lib/api";
import type { ScanResponse } from "@/lib/setups";

const TIMEFRAMES = ["1d", "4h", "1h", "30m", "15m"];

export default function SetupsPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const qc = useQueryClient();
  const [timeframe, setTimeframe] = useState("1d");
  const [minConviction, setMinConviction] = useState(0.5);
  const [direction, setDirection] = useState<"all" | "long" | "short">("all");
  const [adhocSymbols, setAdhocSymbols] = useState("");

  const { data, isFetching, refetch } = useQuery<ScanResponse>({
    queryKey: ["setups", timeframe, minConviction, adhocSymbols],
    queryFn: async () => {
      const params = new URLSearchParams({
        timeframe,
        min_conviction: String(minConviction),
      });
      if (adhocSymbols.trim()) {
        params.set("symbols", adhocSymbols.toUpperCase().replace(/\s+/g, ""));
      }
      const { data } = await api.get<ScanResponse>(`/setups/scan?${params}`);
      return data;
    },
    refetchInterval: 60_000, // auto refresh every minute
  });

  const setups =
    data?.setups.filter((s) => (direction === "all" ? true : s.direction === direction)) ?? [];

  return (
    <div className="space-y-6 max-w-7xl">
      <header className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold">Live Setups</h1>
          <p className="text-muted text-sm mt-1">
            Chart Champions detectors running over your watchlist with real-time bars. Each row is
            an actionable trade with entry, stop, and target levels grounded in a cheatsheet rule.
          </p>
        </div>
        <Button onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw size={14} className={isFetching ? "animate-spin" : ""} />
          {isFetching ? "Scanning…" : "Refresh"}
        </Button>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Filter size={14} /> Filters
          </CardTitle>
        </CardHeader>
        <CardBody className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div>
            <label className="text-xs text-muted block mb-1">Timeframe</label>
            <select
              value={timeframe}
              onChange={(e) => setTimeframe(e.target.value)}
              className="h-10 w-full rounded-md bg-panel border border-border px-3 text-sm"
            >
              {TIMEFRAMES.map((tf) => (
                <option key={tf} value={tf}>
                  {tf}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">
              Min Conviction: {Math.round(minConviction * 100)}%
            </label>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={minConviction}
              onChange={(e) => setMinConviction(parseFloat(e.target.value))}
              className="w-full"
            />
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Direction</label>
            <select
              value={direction}
              onChange={(e) => setDirection(e.target.value as any)}
              className="h-10 w-full rounded-md bg-panel border border-border px-3 text-sm"
            >
              <option value="all">All</option>
              <option value="long">Long only</option>
              <option value="short">Short only</option>
            </select>
          </div>
          <div>
            <label className="text-xs text-muted block mb-1">Ad-hoc tickers</label>
            <div className="flex gap-2">
              <Input
                placeholder="e.g. BTCUSDT,AAPL,TSLA"
                value={adhocSymbols}
                onChange={(e) => setAdhocSymbols(e.target.value)}
              />
              <Button variant="secondary" size="sm" onClick={() => refetch()}>
                <Search size={14} />
              </Button>
            </div>
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>
            {setups.length} setup{setups.length === 1 ? "" : "s"}
            {data && (
              <span className="ml-3 text-xs font-normal text-muted">
                Scanned {data.scanned} tickers · {data.skipped} no data · {data.duration_ms} ms
              </span>
            )}
          </CardTitle>
          <CardSubtitle>
            Auto-refreshes every minute. Click any symbol to see the chart with levels overlaid.
          </CardSubtitle>
        </CardHeader>
        <CardBody className="overflow-x-auto p-0">
          {setups.length === 0 ? (
            <div className="p-6 text-sm text-muted">
              No setups matching current filters. Try lowering the conviction threshold, switching
              timeframe, or adding ad-hoc tickers above.
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
                {setups.map((s, i) => (
                  <SetupRow key={`${s.symbol}-${s.name}-${i}`} s={s} />
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
