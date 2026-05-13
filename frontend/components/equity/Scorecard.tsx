import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { ScoreBar } from "@/components/ui/ScoreBar";
import { fmtScore, CONVICTION_LABEL, CONVICTION_TONE } from "@/lib/format";
import type { CategoryScore, ConvictionLevel } from "@/lib/types";

interface ScorecardProps {
  composite: number;
  conviction: ConvictionLevel;
  stance: string | null;
  categories: CategoryScore[];
}

export function Scorecard({ composite, conviction, stance, categories }: ScorecardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-col gap-3">
        <CardTitle>Composite Rating & Final Assessment</CardTitle>
        <div className="grid grid-cols-3 gap-4 mt-2">
          <div>
            <div className="text-xs text-muted uppercase">Composite</div>
            <div className="text-3xl font-bold font-mono">{fmtScore(composite)}/5.0</div>
            <ScoreBar score={composite} className="mt-2" />
          </div>
          <div>
            <div className="text-xs text-muted uppercase">Conviction</div>
            <div className={`text-lg font-semibold ${CONVICTION_TONE[conviction]}`}>
              {CONVICTION_LABEL[conviction]}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted uppercase">Stance</div>
            <div className="text-lg font-semibold">{stance ?? "—"}</div>
          </div>
        </div>
      </CardHeader>
      <CardBody className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {categories.map((c) => (
          <div key={c.category} className="rounded-lg border border-border p-3">
            <div className="flex items-center justify-between text-sm">
              <span>{c.category}</span>
              <span className="font-mono">{fmtScore(c.score)}</span>
            </div>
            <ScoreBar score={c.score} className="mt-2" />
            {c.summary && (
              <p className="text-xs text-muted mt-2 line-clamp-3">{c.summary}</p>
            )}
          </div>
        ))}
      </CardBody>
    </Card>
  );
}
