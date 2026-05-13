"use client";

import { useSearchParams } from "next/navigation";
import { use, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { AppShell } from "@/components/layout/AppShell";
import { Button } from "@/components/ui/Button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/Card";
import { StepCard } from "@/components/equity/StepCard";
import { Scorecard } from "@/components/equity/Scorecard";
import { api } from "@/lib/api";
import { fmtMoney, fmtScore } from "@/lib/format";
import type { EquityReport } from "@/lib/types";

const STEP_TO_CATEGORY: Record<string, string> = {
  step_1_snapshot: "Business Quality",
  step_2_financial_quality: "Financial Quality",
  step_3_competitive: "Competitive Positioning",
  step_4_bull: "Growth Potential",
  step_5_bear: "Risk Profile",
  step_6_sentiment: "Sentiment & Positioning",
  step_7_valuation: "Valuation Outlook",
};

const STEP_KEYS = [
  "step_1_snapshot",
  "step_2_financial_quality",
  "step_3_competitive",
  "step_4_bull",
  "step_5_bear",
  "step_6_sentiment",
  "step_7_valuation",
  "step_8_scorecard",
  "step_9_thesis",
];

// Next.js 15 made dynamic-route params asynchronous (a Promise). The page
// is a Client Component so we can't `await`, but React 19's `use()` hook
// unwraps the promise synchronously inside a Suspense boundary.
export default function EquityReportPage({
  params,
}: {
  params: Promise<{ ticker: string }>;
}) {
  const { ticker } = use(params);
  return (
    <AppShell>
      <Inner ticker={ticker.toUpperCase()} />
    </AppShell>
  );
}

function Inner({ ticker }: { ticker: string }) {
  const search = useSearchParams();
  const requestedId = search.get("id");
  const qc = useQueryClient();
  const [report, setReport] = useState<EquityReport | null>(null);

  const latestQuery = useQuery<EquityReport | null>({
    queryKey: ["equity", "latest", ticker],
    enabled: !requestedId,
    queryFn: async () => (await api.get(`/equity/latest/${ticker}`)).data,
  });

  const byIdQuery = useQuery<EquityReport>({
    queryKey: ["equity", "report", requestedId],
    enabled: !!requestedId,
    queryFn: async () => (await api.get(`/equity/reports/${requestedId}`)).data,
  });

  useEffect(() => {
    if (requestedId && byIdQuery.data) setReport(byIdQuery.data);
    else if (!requestedId && latestQuery.data) setReport(latestQuery.data);
  }, [requestedId, byIdQuery.data, latestQuery.data]);

  const runMutation = useMutation({
    mutationFn: async () => {
      const { data } = await api.post<EquityReport>("/equity/run", { ticker });
      return data;
    },
    onSuccess: (data) => {
      setReport(data);
      qc.invalidateQueries({ queryKey: ["reports"] });
      qc.invalidateQueries({ queryKey: ["equity", "latest", ticker] });
    },
  });

  const loading = latestQuery.isLoading || byIdQuery.isLoading;

  return (
    <div className="space-y-6 max-w-5xl">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">
            {ticker}
            {report?.company_name && (
              <span className="text-muted font-normal ml-3 text-base">{report.company_name}</span>
            )}
          </h1>
          {report && (
            <p className="text-xs text-muted mt-1">
              Generated {new Date(report.created_at).toLocaleString()} · model {report.model_used} ·
              {" "}
              {report.tokens_used?.toLocaleString()} tokens
            </p>
          )}
        </div>
        <Button
          onClick={() => runMutation.mutate()}
          disabled={runMutation.isPending}
        >
          <RefreshCw size={14} className={runMutation.isPending ? "animate-spin" : ""} />
          {report ? "Re-run analysis" : "Run analysis"}
        </Button>
      </header>

      {runMutation.isError && (
        <Card>
          <CardBody className="text-danger text-sm">
            {(runMutation.error as any)?.response?.data?.detail ||
              "Run failed. Check that OPENAI_API_KEY is configured."}
          </CardBody>
        </Card>
      )}

      {loading && !report && (
        <Card>
          <CardBody className="text-muted text-sm">Loading existing reports…</CardBody>
        </Card>
      )}

      {!loading && !report && !runMutation.isPending && (
        <Card>
          <CardHeader>
            <CardTitle>No report yet for {ticker}</CardTitle>
            <CardSubtitle>
              Click "Run analysis" to execute the 9-step Chart Champions framework.
            </CardSubtitle>
          </CardHeader>
        </Card>
      )}

      {runMutation.isPending && (
        <Card>
          <CardBody className="text-sm">Running the 9-step model. This usually takes 20–60 seconds…</CardBody>
        </Card>
      )}

      {report && (
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardTitle>Scenario targets (Step 7 anchor)</CardTitle>
            </CardHeader>
            <CardBody>
              <div className="grid grid-cols-4 gap-3 text-center">
                <Target label="Anchor" v={report.anchor_price} tone="text-foreground" />
                <Target label="Bull" v={report.bull_target} tone="text-accent" />
                <Target label="Base" v={report.base_target} tone="text-foreground" />
                <Target label="Bear" v={report.bear_target} tone="text-danger" />
              </div>
            </CardBody>
          </Card>

          {STEP_KEYS.map((key, idx) => {
            const section = report.sections?.[key];
            if (!section) return null;
            const category = STEP_TO_CATEGORY[key];
            const score = category
              ? report.category_scores.find((c) => c.category === category)?.score
              : undefined;
            return (
              <StepCard
                key={key}
                step={idx + 1}
                title={section.title}
                analysis={section.analysis}
                category={category}
                score={score ?? null}
              />
            );
          })}

          <Scorecard
            composite={report.composite_score}
            conviction={report.conviction}
            stance={report.investment_stance}
            categories={report.category_scores}
          />

          {report.invalidation_triggers && report.invalidation_triggers.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Invalidation Triggers</CardTitle>
                <CardSubtitle>What would break this thesis.</CardSubtitle>
              </CardHeader>
              <CardBody>
                <ul className="space-y-2">
                  {report.invalidation_triggers.map((t, i) => (
                    <li
                      key={i}
                      className="rounded-md border border-border bg-muted/10 p-3 text-sm"
                    >
                      {t}
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          )}

          {report.citations && report.citations.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>Methodology citations</CardTitle>
                <CardSubtitle>Chunks from your uploaded Chart Champions docs.</CardSubtitle>
              </CardHeader>
              <CardBody>
                <ul className="text-xs space-y-1 text-muted">
                  {report.citations.map((c, i) => (
                    <li key={i}>
                      {c.filename} · page {c.page ?? "?"} · sim {fmtScore(c.score)}
                    </li>
                  ))}
                </ul>
              </CardBody>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}

function Target({ label, v, tone }: { label: string; v: number | null; tone: string }) {
  return (
    <div className="rounded-lg border border-border bg-muted/10 p-3">
      <div className="text-xs text-muted uppercase">{label}</div>
      <div className={`text-lg font-mono ${tone}`}>{fmtMoney(v)}</div>
    </div>
  );
}
