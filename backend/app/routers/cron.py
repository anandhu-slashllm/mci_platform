"""Scheduled work as plain HTTP endpoints, for hosts without long-running processes (Vercel).

Vercel Cron calls these with ``Authorization: Bearer $CRON_SECRET`` (GET). Supabase pg_cron + pg_net
can call them the same way. With BACKGROUND_MODE=loop (Docker/uvicorn) the same functions run in
in-process loops instead, and these endpoints stay available for manual runs.
"""

import hmac
import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from app.config import get_settings
from app.db.session import AsyncSessionLocal
from app.services.dispatch import process_dispatch_tasks_once
from app.services.recommendation_refresh import process_recommendation_refresh_once
from app.services.shift_reminders import process_shift_reminders_once

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/cron", tags=["cron"], include_in_schema=False)


def _check_secret(authorization: str | None) -> None:
    secret = get_settings().cron_secret
    if not secret:
        raise HTTPException(status_code=503, detail="CRON_SECRET is not configured")
    expected = f"Bearer {secret}"
    if not authorization or not hmac.compare_digest(authorization.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def run_tick() -> dict:
    """One pass of the frequent jobs (what the loops did every 30-120s)."""
    out: dict[str, object] = {}
    async with AsyncSessionLocal() as db:
        out["dispatch_tasks_processed"] = await process_dispatch_tasks_once(db)
        await db.commit()
    out["shift_reminders_sent"] = await process_shift_reminders_once()
    out["recommendations"] = await process_recommendation_refresh_once(run_daily=False)
    return out


@router.get("/tick")
@router.post("/tick")
async def cron_tick(authorization: Annotated[str | None, Header()] = None):
    _check_secret(authorization)
    result = await run_tick()
    logger.info("cron tick %s", result)
    return result


@router.get("/daily")
@router.post("/daily")
async def cron_daily(authorization: Annotated[str | None, Header()] = None):
    _check_secret(authorization)
    result = await process_recommendation_refresh_once(run_daily=True)
    logger.info("cron daily %s", result)
    return result
