import csv
import io
import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_roles
from app.services.storage import get_storage, storage_ref
from app.db.session import get_db
from app.models import JobOrder, PaymentRecord, PaymentStatus, TimesheetEntry, TimesheetStatus, User, UserRole, WorkerProfile
from app.services.audit import log_audit
from app.services.ta_client_contracts import list_contracted_org_ids_for_ta
from app.services.crypto_pii import decrypt_value
from app.services.notifications import notify_user

router = APIRouter(prefix="/payroll", tags=["payroll"])


@router.post("/generate-payment-file")
async def generate_payment_file(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
):
    stmt = (
        select(TimesheetEntry)
        .join(JobOrder, TimesheetEntry.job_order_id == JobOrder.id)
        .where(TimesheetEntry.status == TimesheetStatus.ta_verified)
    )
    if user.role == UserRole.ta:
        org_ids = list(await list_contracted_org_ids_for_ta(db, user.id))
        if not org_ids:
            raise HTTPException(400, detail="No verified timesheets")
        stmt = stmt.where(JobOrder.client_org_id.in_(org_ids))
    r = await db.execute(stmt)
    entries = r.scalars().all()
    if not entries:
        raise HTTPException(400, detail="No verified timesheets")

    by_worker: dict[int, list[TimesheetEntry]] = {}
    for e in entries:
        by_worker.setdefault(e.worker_id, []).append(e)

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["worker_name", "bank_account", "bank_name", "amount_sgd", "timesheet_ids"])
    file_rows = []
    for wid, ents in by_worker.items():
        wp = await db.get(WorkerProfile, wid)
        if not wp:
            continue
        total = sum(e.gross_pay for e in ents)
        bank = decrypt_value(wp.bank_encrypted) if wp.bank_encrypted else ""
        bank_name = (wp.bank_name or "").strip()
        ids = ",".join(str(e.id) for e in ents)
        w.writerow([wp.full_name, bank, bank_name, f"{total:.2f}", ids])
        file_rows.append((wid, [e.id for e in ents], total))
        for e in ents:
            e.status = TimesheetStatus.payment_released

    key = f"payroll/pay_{uuid.uuid4().hex}.csv"
    await get_storage().upload(key, buf.getvalue().encode("utf-8"), "text/csv")
    rel = storage_ref(key)

    for wid, eids, total in file_rows:
        pr = PaymentRecord(
            worker_id=wid,
            timesheet_entry_ids=eids,
            total_amount=total,
            payment_status=PaymentStatus.processing,
            payment_instructions_file_url=rel,
        )
        db.add(pr)
        wp = await db.get(WorkerProfile, wid)
        if not wp:
            continue
        usr = await db.get(User, wp.user_id)
        if usr:
            await notify_user(
                db,
                usr.id,
                title="Payment processing",
                body=f"Your payment of {total:.2f} SGD is being processed.",
            )

    await log_audit(
        db,
        actor_user_id=user.id,
        action="payment_file_generated",
        entity_type="PaymentBatch",
        entity_id=key,
    )
    return {"ok": True, "file": rel, "workers": len(by_worker)}


@router.post("/mark-paid/{payment_id}")
async def mark_paid(
    payment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
):
    pr = await db.get(PaymentRecord, payment_id)
    if not pr:
        raise HTTPException(404)
    pr.payment_status = PaymentStatus.paid
    pr.paid_at = datetime.now(timezone.utc)
    wp = await db.get(WorkerProfile, pr.worker_id)
    if wp:
        usr = await db.get(User, wp.user_id)
        if usr:
            await notify_user(db, usr.id, title="Paid", body=f"Payment of {pr.total_amount:.2f} marked paid.")
    await log_audit(db, actor_user_id=user.id, action="payment_mark_paid", entity_type="PaymentRecord", entity_id=str(payment_id))
    return {"ok": True}
