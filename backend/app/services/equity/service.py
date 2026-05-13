"""Equity Analysis Service — orchestrates the full 9-step run."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ...models.equity import CategoryScore, EquityReport
from ..market.fundamentals import FundamentalsService
from ..rag import RAGService
from .llm import run_chat_json
from .parser import ParsedReport, parse
from .prompts import build_system_prompt, build_user_prompt
from .scoring import classify_conviction, compute_composite


class EquityAnalysisService:
    """Run the Chart Champions Structured Equity Model end-to-end."""

    @staticmethod
    async def run(
        db: AsyncSession,
        *,
        user_id: uuid.UUID,
        ticker: str,
        company_name: str | None = None,
        force_refresh: bool = False,
    ) -> EquityReport:
        ticker = ticker.upper().strip()

        # 1) Pull live fundamentals so the LLM works on fresh numbers.
        fundamentals = await FundamentalsService.snapshot(ticker)
        resolved_name = company_name or (fundamentals.get("name") if fundamentals else None)

        # 2) RAG: retrieve any user-uploaded copy of the Master Instructions.
        retrieved = await RAGService.retrieve(
            user_id=user_id,
            query="Structured Equity Analysis Model Master Instruction Block nine steps scoring",
            k=8,
        )
        corpus_context = RAGService.format_context(retrieved) if retrieved else None
        citations = [
            {"page": c.page, "document_id": c.document_id, "filename": c.filename, "score": c.score}
            for c in retrieved
        ]

        # 3) LLM call.
        system_prompt = build_system_prompt(corpus_context=corpus_context)
        user_prompt = build_user_prompt(ticker, resolved_name, fundamentals)

        raw, usage = await run_chat_json(
            db,
            user_id=user_id,
            kind="equity",
            system=system_prompt,
            user=user_prompt,
        )

        # 4) Parse + validate.
        parsed: ParsedReport = parse(raw)

        # 5) Compute composite + conviction (deterministic, not from LLM).
        composite = compute_composite(parsed.scores)
        band = classify_conviction(composite)

        # 6) Persist.
        report = EquityReport(
            user_id=user_id,
            ticker=parsed.ticker or ticker,
            company_name=parsed.company_name or resolved_name,
            sections=parsed.sections,
            composite_score=composite,
            conviction=band.level,
            investment_stance=parsed.investment_stance,
            bull_target=parsed.bull_target,
            base_target=parsed.base_target,
            bear_target=parsed.bear_target,
            anchor_price=parsed.anchor_price or (fundamentals.get("price") if fundamentals else None),
            invalidation_triggers=parsed.invalidation_triggers,
            raw_llm_output=raw,
            model_used=usage.get("model"),
            tokens_used=(usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
            citations=citations,
        )
        db.add(report)
        await db.flush()

        for category, score in parsed.scores.items():
            db.add(
                CategoryScore(
                    report_id=report.id,
                    category=category,
                    score=score,
                    summary=_extract_category_summary(parsed.sections, category),
                )
            )
        await db.flush()
        return report


def _extract_category_summary(sections: dict[str, dict[str, Any]], category: str) -> str | None:
    """Best-effort: map each scoring category to the closest narrative step.

    The methodology pairs categories with steps as follows (per the CC cheatsheet):
      Business Quality          → step 1
      Financial Quality         → step 2
      Competitive Positioning   → step 3
      Growth Potential          → step 4
      Risk Profile              → step 5
      Sentiment & Positioning   → step 6
      Valuation Outlook         → step 7
    """
    mapping = {
        "Business Quality": "step_1_snapshot",
        "Financial Quality": "step_2_financial_quality",
        "Competitive Positioning": "step_3_competitive",
        "Growth Potential": "step_4_bull",
        "Risk Profile": "step_5_bear",
        "Sentiment & Positioning": "step_6_sentiment",
        "Valuation Outlook": "step_7_valuation",
    }
    key = mapping.get(category)
    if not key:
        return None
    step = sections.get(key) or {}
    analysis = step.get("analysis") if isinstance(step, dict) else None
    if not analysis:
        return None
    return analysis[:1000]
