"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ArrowRight, TrendingUp, ListTree, Bell, FileText } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { api } from "@/lib/api";
import { fmtScore, CONVICTION_LABEL, CONVICTION_TONE } from "@/lib/format";
import type { EquityReportSummary, Watchlist, Alert, DocumentRecord } from "@/lib/types";

export default function DashboardPage() {
  return (
    <AppShell>
      <Inner />
    </AppShell>
  );
}

function Inner() {
  const reports = useQuery<EquityReportSummary[]>({
    queryKey: ["reports"],
    queryFn: async () => (await api.get("/equity/reports")).data,
  });
  const watchlists = useQuery<Watchlist[]>({
    queryKey: ["watchlists"],
    queryFn: async () => (await api.get("/watchlists")).data,
  });
  const alerts = useQuery<Alert[]>({
    queryKey: ["alerts"],
    queryFn: async () => (await api.get("/alerts")).data,
  });
  const docs = useQuery<DocumentRecord[]>({
    queryKey: ["documents"],
    queryFn: async () => (await api.get("/documents")).data,
  });

  return (
    <div className="space-y-8 max-w-6xl">
      <header>
        <h1 className="text-2xl font-bold">Dashboard</h1>
        <p className="text-muted text-sm mt-1">
          Phase 1 active: Structured Equity Model. Upload your Chart Champions PDFs, then run a
          report on any company.
        </p>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Stat
          icon={<FileText size={16} />}
          label="Methodology docs"
          value={docs.data?.length ?? "—"}
          href="/documents"
        />
        <Stat
          icon={<TrendingUp size={16} />}
          label="Equity reports"
          value={reports.data?.length ?? "—"}
          href="/equity"
        />
        <Stat
          icon={<ListTree size={16} />}
          label="Watchlists"
          value={watchlists.data?.length ?? "—"}
          href="/watchlists"
        />
        <Stat
          icon={<Bell size={16} />}
          label="Active alerts"
          value={alerts.data?.filter((a) => a.is_enabled).length ?? "—"}
          href="/alerts"
        />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Recent equity reports</CardTitle>
          <CardSubtitle>9-step Chart Champions analyses you've run.</CardSubtitle>
        </CardHeader>
        <CardBody>
          {reports.isLoading ? (
            <p className="text-sm text-muted">Loading…</p>
          ) : reports.data && reports.data.length > 0 ? (
            <ul className="divide-y divide-border">
              {reports.data.slice(0, 10).map((r) => (
                <li key={r.id} className="py-3 flex items-center justify-between">
                  <div>
                    <Link href={`/equity/${r.ticker}`} className="font-medium hover:text-accent">
                      {r.ticker}
                    </Link>
                    <div className="text-xs text-muted">{r.company_name}</div>
                  </div>
                  <div className="flex items-center gap-6 text-sm">
                    <span className="font-mono">{fmtScore(r.composite_score)}</span>
                    <span className={CONVICTION_TONE[r.conviction]}>
                      {CONVICTION_LABEL[r.conviction]}
                    </span>
                    <span className="text-muted text-xs">{r.investment_stance}</span>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted">
              No reports yet.{" "}
              <Link href="/equity" className="text-accent hover:underline">
                Run your first analysis
              </Link>
              .
            </p>
          )}
        </CardBody>
      </Card>
    </div>
  );
}

function Stat({
  icon,
  label,
  value,
  href,
}: {
  icon: React.ReactNode;
  label: string;
  value: number | string;
  href: string;
}) {
  return (
    <Card>
      <Link href={href}>
        <div className="p-4">
          <div className="flex items-center justify-between text-muted text-xs">
            <span className="flex items-center gap-2">
              {icon} {label}
            </span>
            <ArrowRight size={12} />
          </div>
          <div className="mt-2 text-2xl font-semibold">{value}</div>
        </div>
      </Link>
    </Card>
  );
}
