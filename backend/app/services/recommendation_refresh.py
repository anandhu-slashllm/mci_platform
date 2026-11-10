from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models import RecommendationRefreshQueue, WorkerProfile
from app.services.recommendations import recompute_all_active_worker_recommendations, recompute_worker_recommendations

logger = logging.getLogger(__name__)


async def enqueue_worker_recommendation_refresh(db: AsyncSession, worker_profile_id: int) -> None:
    """Queue a worker for recompute in the caller's transaction (idempotent per worker)."""
    wid = int(worker_profile_id)
    if await db.get(RecommendationRefreshQueue, wid) is None:
        db.add(RecommendationRefreshQueue(worker_profile_id=wid, queued_at=datetime.now(timezone.utc)))
        await db.flush()


async def process_recommendation_refresh_once(*, run_daily: bool | None = None) -> dict:
    """Run queued worker refreshes and the daily baseline refresh.

    ``run_daily=None`` keeps the loop behaviour (around 03:00 UTC); cron passes True/False explicitly.
    """
    processed_workers = 0
    daily_refreshed = False
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        queued = (
            await db.execute(select(RecommendationRefreshQueue).order_by(RecommendationRefreshQueue.worker_profile_id))
        ).scalars().all()
        for item in queued:
            worker = await db.get(WorkerProfile, item.worker_profile_id)
            if worker:
                await recompute_worker_recommendations(db, worker, source="post_shift")
                processed_workers += 1
            await db.delete(item)

        if run_daily is None:
            run_daily = now.hour == 3 and now.minute < 10
        if run_daily:
            await recompute_all_active_worker_recommendations(db, source="daily")
            daily_refreshed = True
        await db.commit()
    return {"processed_workers": processed_workers, "daily_refreshed": daily_refreshed}


async def recommendation_refresh_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            outcome = await process_recommendation_refresh_once()
            if outcome["processed_workers"] or outcome["daily_refreshed"]:
                logger.info(
                    "recommendation_refresh processed_workers=%s daily_refreshed=%s",
                    outcome["processed_workers"],
                    outcome["daily_refreshed"],
                )
        except Exception:
            logger.exception("recommendation_refresh loop error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=60.0)
        except asyncio.TimeoutError:
            continue
