"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { Input } from "@/components/ui/Input";
import { Button } from "@/components/ui/Button";
import { fmtScore, CONVICTION_LABEL, CONVICTION_TONE } from "@/lib/format";
import { api } from "@/lib/api";
import type { EquityReportSummary } from "@/lib/types";

export default function EquityIndex() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const router = useRouter();
  const [ticker, setTicker] = useState("");

  const { data: reports } = useQuery<EquityReportSummary[]>({
    queryKey: ["reports"],
    queryFn: async () => (await api.get("/equity/reports")).data,
  });

  return (
    <div className="space-y-6 max-w-5xl">
      <header>
        <h1 className="text-2xl font-bold">Structured Equity Model</h1>
        <p className="text-muted text-sm mt-1">
          Run the Chart Champions 9-step framework on any ticker. The model uses your uploaded
          methodology if present, plus live fundamentals via yfinance.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Run an analysis</CardTitle>
          <CardSubtitle>Input is just the ticker, per the methodology.</CardSubtitle>
        </CardHeader>
        <CardBody>
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              const t = ticker.trim().toUpperCase();
              if (t) router.push(`/equity/${t}`);
            }}
          >
            <Input
              placeholder="GOOGL"
              value={ticker}
              onChange={(e) => setTicker(e.target.value)}
              required
            />
            <Button type="submit">Open</Button>
          </form>
        </CardBody>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your reports</CardTitle>
        </CardHeader>
        <CardBody>
          {reports && reports.length > 0 ? (
            <table className="w-full text-sm">
              <thead className="text-muted text-xs uppercase">
                <tr>
                  <th className="text-left py-2">Ticker</th>
                  <th className="text-left">Company</th>
                  <th className="text-right">Composite</th>
                  <th className="text-left pl-4">Conviction</th>
                  <th className="text-left">Stance</th>
                  <th className="text-left">When</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => (
                  <tr key={r.id} className="border-t border-border">
                    <td className="py-2">
                      <Link
                        href={`/equity/${r.ticker}?id=${r.id}`}
                        className="font-medium hover:text-accent"
                      >
                        {r.ticker}
                      </Link>
                    </td>
                    <td>{r.company_name ?? "—"}</td>
                    <td className="text-right font-mono">{fmtScore(r.composite_score)}</td>
                    <td className={`pl-4 ${CONVICTION_TONE[r.conviction]}`}>
                      {CONVICTION_LABEL[r.conviction]}
                    </td>
                    <td>{r.investment_stance ?? "—"}</td>
                    <td className="text-muted text-xs">
                      {new Date(r.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-muted">No reports yet.</p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}
