"""arq worker entrypoint.

Run with::

    arq app.workers.main.WorkerSettings
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import select

from ..core.config import settings
from ..core.logging import configure_logging
from ..db.session import AsyncSessionLocal
from ..models.alert import Alert
from ..services.alerts.evaluator import evaluate
from ..services.market.registry import MarketDataService
from ..services.rag import IngestionService

logger = logging.getLogger(__name__)


async def ingest_document(ctx, document_id: str) -> None:
    async with AsyncSessionLocal() as db:
        try:
            await IngestionService.ingest(db, document_id=uuid.UUID(document_id))
            await db.commit()
        except Exception as exc:
            logger.exception("ingest_document failed: %s", exc)
            await db.rollback()


async def evaluate_alerts(ctx) -> None:
    """Cron: every minute, check enabled alerts. Real-world deployments will
    drive this via websocket-pushed ticks rather than polling; the polling
    path is provided as a baseline."""
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Alert).where(Alert.is_enabled.is_(True)))
        alerts = list(res.scalars())

        # Group by symbol so we fetch bars once per symbol.
        by_symbol: dict[str, list[Alert]] = {}
        for a in alerts:
            by_symbol.setdefault(a.symbol, []).append(a)

        for symbol, group in by_symbol.items():
            df = await MarketDataService.bars(symbol, timeframe="5m", days=2)
            for a in group:
                decision = evaluate(a, df)
                if decision.should_fire:
                    a.last_triggered_at = datetime.now(timezone.utc)
                    logger.info("alert %s fired: %s", a.id, decision.reason)
        await db.commit()


class WorkerSettings:
    functions = [ingest_document]
    cron_jobs = [cron(evaluate_alerts, minute=set(range(0, 60)))]
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)


if __name__ == "__main__":  # pragma: no cover
    configure_logging()
    from arq.worker import run_worker

    asyncio.run(run_worker(WorkerSettings))
