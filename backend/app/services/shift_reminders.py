"""In-app shift reminders ~24h and ~2h before start (PRD).

Idempotency is a unique (assignment_id, kind) row in shift_reminders_sent, so it holds across
processes and serverless invocations.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionLocal
from app.models import AssignmentStatus, ShiftAssignment, ShiftReminderSent, User, WorkerProfile

logger = logging.getLogger(__name__)


async def _mark_sent(db: AsyncSession, assignment_id: int, kind: str) -> bool:
    """Return True if this is the first send (should notify)."""
    existing = await db.execute(
        select(ShiftReminderSent.id).where(
            ShiftReminderSent.assignment_id == assignment_id, ShiftReminderSent.kind == kind
        )
    )
    if existing.first():
        return False
    try:
        async with db.begin_nested():
            db.add(ShiftReminderSent(assignment_id=assignment_id, kind=kind, sent_at=datetime.now(timezone.utc)))
            await db.flush()
    except IntegrityError:
        return False  # a concurrent run claimed it
    return True


async def process_shift_reminders_once() -> int:
    """Send due reminders; returns count of notifications created."""
    from app.services.notifications import notify_user

    now = datetime.now(timezone.utc)
    sent = 0
    async with AsyncSessionLocal() as db:
        q = await db.execute(
            select(ShiftAssignment)
            .options(selectinload(ShiftAssignment.shift))
            .where(ShiftAssignment.status == AssignmentStatus.confirmed)
        )
        assigns = q.scalars().all()
        for a in assigns:
            start = a.shift.starts_at
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start <= now:
                continue
            delta = (start - now).total_seconds()
            # ~24 hours before shift
            if 23 * 3600 <= delta <= 25 * 3600:
                if await _mark_sent(db, a.id, "24h"):
                    wp = await db.get(WorkerProfile, a.worker_id)
                    if wp:
                        u = await db.get(User, wp.user_id)
                        if u:
                            await notify_user(
                                db,
                                u.id,
                                title="Shift reminder",
                                body=f"Your shift starts in about 24 hours ({start.isoformat()}).",
                                payload={"kind": "shift_reminder_24h", "shift_id": a.shift_id, "assignment_id": a.id},
                            )
                            sent += 1
            # ~2 hours before shift
            if 1.75 * 3600 <= delta <= 2.25 * 3600:
                if await _mark_sent(db, a.id, "2h"):
                    wp = await db.get(WorkerProfile, a.worker_id)
                    if wp:
                        u = await db.get(User, wp.user_id)
                        if u:
                            await notify_user(
                                db,
                                u.id,
                                title="Shift starting soon",
                                body=f"Your shift starts in about 2 hours ({start.isoformat()}).",
                                payload={"kind": "shift_reminder_2h", "shift_id": a.shift_id, "assignment_id": a.id},
                            )
                            sent += 1
        await db.commit()
    return sent


async def reminder_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await process_shift_reminders_once()
        except Exception:
            logger.exception("shift reminder loop error")
        try:
            await asyncio.wait_for(stop.wait(), timeout=120.0)
        except asyncio.TimeoutError:
            continue
