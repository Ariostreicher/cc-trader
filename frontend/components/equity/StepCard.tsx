import type { ReactNode } from "react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/Card";
import { ScoreBar } from "@/components/ui/ScoreBar";
import { fmtScore } from "@/lib/format";

interface StepCardProps {
  step: number;
  title: string;
  analysis: string;
  score?: number | null;
  category?: string;
  children?: ReactNode;
}

export function StepCard({ step, title, analysis, score, category, children }: StepCardProps) {
  return (
    <Card>
      <CardHeader className="flex flex-col gap-2">
        <div className="flex items-center justify-between">
          <span className="text-xs text-muted font-mono uppercase">Step {step}</span>
          {category && (
            <span className="text-[10px] tracking-wider uppercase text-accent">{category}</span>
          )}
        </div>
        <CardTitle>{title}</CardTitle>
        {score !== undefined && score !== null && (
          <div className="flex items-center gap-3 mt-1">
            <span className="font-mono text-lg">{fmtScore(score)}/5.0</span>
            <ScoreBar score={score} className="flex-1" />
          </div>
        )}
      </CardHeader>
      <CardBody className="space-y-3">
        <p className="text-sm leading-relaxed whitespace-pre-wrap">{analysis}</p>
        {children}
      </CardBody>
    </Card>
  );
}
