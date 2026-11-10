from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from jose import JWTError
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth.attendance_qr_tokens import decode_attendance_qr_token
from app.auth.deps import require_roles
from app.config import get_settings
from app.services.storage import get_storage, new_object_key, storage_ref
from app.db.session import get_db
from app.models import (
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    JobOrder,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
)
from app.services.attendance_geo import validate_clock_geofence
from app.services.attendance_rules import (
    is_grace_period_late_punch,
    is_clock_in_too_early,
    is_past_clock_in_grace_deadline,
    is_past_shift_end,
)
from app.services.audit import log_audit
from app.services.clock_event_notifications import (
    notify_stakeholders_of_worker_clock_event,
    notify_stakeholders_of_worker_geofence_mismatch,
)
from app.services.timesheet_flow import get_clock_out_pay_summary, mark_assignment_completed_if_clocked

router = APIRouter(prefix="/attendance", tags=["attendance"])


async def _get_assignment_for_worker(db: AsyncSession, user: User, assignment_id: int) -> ShiftAssignment:
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(404)
    a = await db.get(ShiftAssignment, assignment_id)
    if not a or a.worker_id != wp.id or a.status != AssignmentStatus.confirmed:
        raise HTTPException(404)
    return a


async def _get_assignment_for_worker_shift(
    db: AsyncSession, user: User, shift_id: int
) -> ShiftAssignment:
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(404)
    r2 = await db.execute(
        select(ShiftAssignment)
        .options(selectinload(ShiftAssignment.shift))
        .where(
            ShiftAssignment.shift_id == shift_id,
            ShiftAssignment.worker_id == wp.id,
            ShiftAssignment.status == AssignmentStatus.confirmed,
        )
    )
    a = r2.scalar_one_or_none()
    if not a:
        raise HTTPException(404, detail="No confirmed assignment for this shift")
    return a


async def _save_clock_photo(photo: UploadFile | None) -> str | None:
    if photo is None:
        return None
    settings = get_settings()
    content = await photo.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File too large")
    ct = (photo.content_type or "image/jpeg").split(";")[0].strip().lower()
    key = new_object_key("attendance", photo.filename, ".jpg")
    await get_storage().upload(key, content, ct)
    return storage_ref(key)


async def perform_clock_in(
    db: AsyncSession,
    user: User,
    assignment_id: int,
    lat: float,
    lng: float,
    photo: UploadFile | None,
) -> dict:
    await _get_assignment_for_worker(db, user, assignment_id)
    r = await db.execute(
        select(ShiftAssignment)
        .options(selectinload(ShiftAssignment.shift))
        .where(ShiftAssignment.id == assignment_id)
    )
    a = r.scalar_one()
    shift = a.shift
    now = datetime.now(timezone.utc)
    if is_clock_in_too_early(now, shift.starts_at):
        raise HTTPException(
            400,
            detail="Clock-in opens 10 minutes before the scheduled shift start",
        )
    if is_past_clock_in_grace_deadline(now, shift.starts_at):
        raise HTTPException(
            400,
            detail={
                "code": "clock_in_grace_expired",
                "message": "Clock-in is closed because you are more than 10 minutes late.",
            },
        )
    if is_past_shift_end(now, shift.ends_at):
        raise HTTPException(400, detail="This shift has ended; clock-in is closed")

    job = await db.get(JobOrder, a.shift.job_order_id)
    ok, dist = validate_clock_geofence(job, lat, lng)
    if not ok:
        if job:
            await notify_stakeholders_of_worker_geofence_mismatch(
                db,
                job=job,
                shift=shift,
                assignment=a,
                event="clock_in",
                clock_time=now,
                distance_m=dist,
            )
            # This path raises an HTTPException; commit mismatch notifications first.
            await db.commit()
        raise HTTPException(
            status_code=400,
            detail={
                "code": "geofence_clock_in_blocked",
                "message": (
                    f"You must be within {job.geofence_radius_m}m of the job site to clock in."
                    + (
                        f" Your position is about {int(round(dist))}m away."
                        if dist is not None
                        else " Your location could not be matched to the site."
                    )
                ),
                "distance_m": dist,
                "radius_m": job.geofence_radius_m,
            },
        )

    rel = await _save_clock_photo(photo)

    ar = await db.execute(select(AttendanceRecord).where(AttendanceRecord.shift_assignment_id == a.id))
    rec = ar.scalar_one_or_none()
    if rec and rec.clock_in_time:
        raise HTTPException(400, detail="Already clocked in for this shift")
    if not rec:
        rec = AttendanceRecord(shift_assignment_id=a.id)
        db.add(rec)
    rec.clock_in_time = now
    if rel:
        rec.clock_in_photo_url = rel
    rec.clock_in_lat = lat
    rec.clock_in_lng = lng
    rec.clock_in_geofence_valid = True
    if is_grace_period_late_punch(now, shift.starts_at):
        rec.status = AttendanceStatus.late
    else:
        rec.status = AttendanceStatus.unverified
    await db.flush()
    await log_audit(
        db,
        actor_user_id=user.id,
        action="clock_in",
        entity_type="AttendanceRecord",
        entity_id=str(rec.id),
        new_values={"geofence_ok": ok, "distance_m": dist, "qr": photo is None},
    )
    if job:
        await notify_stakeholders_of_worker_clock_event(
            db,
            job=job,
            shift=shift,
            assignment=a,
            attendance_record_id=rec.id,
            event="clock_in",
            clock_time=now,
        )
    return {"ok": True, "geofence_valid": ok, "distance_m": dist, "status": rec.status.value}


async def perform_clock_out(
    db: AsyncSession,
    user: User,
    assignment_id: int,
    lat: float,
    lng: float,
    photo: UploadFile | None,
) -> dict:
    await _get_assignment_for_worker(db, user, assignment_id)
    r = await db.execute(
        select(ShiftAssignment)
        .options(selectinload(ShiftAssignment.shift))
        .where(ShiftAssignment.id == assignment_id)
    )
    a = r.scalar_one()
    shift = a.shift
    job = await db.get(JobOrder, a.shift.job_order_id)
    ok, dist = validate_clock_geofence(job, lat, lng)
    rel = await _save_clock_photo(photo)

    ar = await db.execute(select(AttendanceRecord).where(AttendanceRecord.shift_assignment_id == a.id))
    rec = ar.scalar_one_or_none()
    if not rec or not rec.clock_in_time:
        raise HTTPException(400, detail="Clock in first")
    out_now = datetime.now(timezone.utc)
    rec.clock_out_time = out_now
    if rel:
        rec.clock_out_photo_url = rel
    rec.clock_out_lat = lat
    rec.clock_out_lng = lng
    if rec.clock_in_time and rec.clock_out_time:
        cin = rec.clock_in_time
        if cin.tzinfo is None:
            cin = cin.replace(tzinfo=timezone.utc)
        rec.total_hours = (rec.clock_out_time - cin).total_seconds() / 3600.0
    if not ok:
        rec.status = AttendanceStatus.flagged
    elif rec.clock_in_time and (
        is_grace_period_late_punch(rec.clock_in_time, shift.starts_at)
        or is_past_clock_in_grace_deadline(rec.clock_in_time, shift.starts_at)
    ):
        rec.status = AttendanceStatus.late
    else:
        rec.status = AttendanceStatus.present
    await db.flush()
    await mark_assignment_completed_if_clocked(db, a, rec)
    await db.refresh(a)
    pay = await get_clock_out_pay_summary(db, a, rec, shift, job)
    await log_audit(
        db,
        actor_user_id=user.id,
        action="clock_out",
        entity_type="AttendanceRecord",
        entity_id=str(rec.id),
        new_values={"geofence_ok": ok, "distance_m": dist, "qr": photo is None},
    )
    if job:
        if ok:
            await notify_stakeholders_of_worker_clock_event(
                db,
                job=job,
                shift=shift,
                assignment=a,
                attendance_record_id=rec.id,
                event="clock_out",
                clock_time=out_now,
            )
        else:
            await notify_stakeholders_of_worker_geofence_mismatch(
                db,
                job=job,
                shift=shift,
                assignment=a,
                event="clock_out",
                clock_time=out_now,
                distance_m=dist,
            )
    return {
        "ok": True,
        "geofence_valid": ok,
        **pay,
    }


@router.post("/assignments/{assignment_id}/clock-in")
async def clock_in(
    assignment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
    lat: float = Form(...),
    lng: float = Form(...),
    photo: UploadFile = File(...),
):
    return await perform_clock_in(db, user, assignment_id, lat, lng, photo)


@router.post("/assignments/{assignment_id}/clock-out")
async def clock_out(
    assignment_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
    lat: float = Form(...),
    lng: float = Form(...),
    photo: UploadFile = File(...),
):
    return await perform_clock_out(db, user, assignment_id, lat, lng, photo)


class QrClockBody(BaseModel):
    token: str = Field(..., min_length=10)
    lat: float
    lng: float


@router.post("/qr-clock")
async def qr_clock(
    body: QrClockBody,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    try:
        claims = decode_attendance_qr_token(body.token.strip())
    except JWTError:
        raise HTTPException(400, detail="Invalid or expired QR code")
    job_id = int(claims["job_id"])
    shift_id = int(claims["shift_id"])
    action = str(claims["action"])
    if action not in ("in", "out"):
        raise HTTPException(400, detail="Invalid QR code")
    shift = await db.get(Shift, shift_id)
    if not shift or shift.job_order_id != job_id:
        raise HTTPException(400, detail="Invalid QR code")
    a = await _get_assignment_for_worker_shift(db, user, shift_id)
    if action == "in":
        return await perform_clock_in(db, user, a.id, body.lat, body.lng, None)
    return await perform_clock_out(db, user, a.id, body.lat, body.lng, None)
