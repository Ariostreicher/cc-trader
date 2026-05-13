"""Equity analysis endpoints — Phase 1."""

from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ....db.session import get_db
from ....models.equity import CategoryScore, EquityReport
from ....models.user import User
from ....schemas.equity import (
    CategoryScoreOut,
    EquityReportOut,
    EquityReportSummary,
    EquityRunIn,
)
from ....services.equity import EquityAnalysisService
from ...deps import current_user

router = APIRouter(prefix="/equity", tags=["equity"])


async def _to_out(db: AsyncSession, report: EquityReport) -> EquityReportOut:
    scores_res = await db.execute(
        select(CategoryScore).where(CategoryScore.report_id == report.id)
    )
    out = EquityReportOut.model_validate(report)
    out.category_scores = [CategoryScoreOut.model_validate(s) for s in scores_res.scalars()]
    return out


@router.post("/run", response_model=EquityReportOut, status_code=status.HTTP_201_CREATED)
async def run_equity(
    payload: EquityRunIn,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> EquityReportOut:
    try:
        report = await EquityAnalysisService.run(
            db, user_id=user.id, ticker=payload.ticker, company_name=payload.company_name
        )
    except RuntimeError as exc:
        # OPENAI_API_KEY missing or transient upstream failure
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return await _to_out(db, report)


@router.get("/reports", response_model=List[EquityReportSummary])
async def list_reports(
    ticker: Optional[str] = Query(default=None, max_length=32),
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> List[EquityReportSummary]:
    stmt = select(EquityReport).where(EquityReport.user_id == user.id)
    if ticker:
        stmt = stmt.where(EquityReport.ticker == ticker.upper())
    stmt = stmt.order_by(EquityReport.created_at.desc())
    res = await db.execute(stmt)
    return [EquityReportSummary.model_validate(r) for r in res.scalars()]


@router.get("/reports/{report_id}", response_model=EquityReportOut)
async def get_report(
    report_id: uuid.UUID,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> EquityReportOut:
    res = await db.execute(
        select(EquityReport).where(
            EquityReport.id == report_id, EquityReport.user_id == user.id
        )
    )
    report = res.scalar_one_or_none()
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "report not found")
    return await _to_out(db, report)


@router.get("/latest/{ticker}", response_model=Optional[EquityReportOut])
async def latest_for_ticker(
    ticker: str,
    user: User = Depends(current_user),
    db: AsyncSession = Depends(get_db),
) -> Optional[EquityReportOut]:
    res = await db.execute(
        select(EquityReport)
        .where(EquityReport.user_id == user.id, EquityReport.ticker == ticker.upper())
        .order_by(EquityReport.created_at.desc())
        .limit(1)
    )
    report = res.scalar_one_or_none()
    if not report:
        return None
    return await _to_out(db, report)
