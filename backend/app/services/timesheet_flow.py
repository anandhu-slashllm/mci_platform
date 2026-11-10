from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    JobOrder,
    Shift,
    ShiftAssignment,
    TimesheetEntry,
    TimesheetStatus,
)
from app.services.payroll_calc import compute_timesheet_pay
from app.services.recommendation_refresh import enqueue_worker_recommendation_refresh
from app.services.worker_ratings import POINTS_PER_COMPLETED_SHIFT, adjust_worker_points


def _hours_since_shift_start(shift: Shift, end_time: datetime | None) -> float:
    if end_time is None:
        return 0.0
    starts_at = shift.starts_at
    if starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=timezone.utc)
    ends_at = end_time
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=timezone.utc)
    return max(0.0, (ends_at - starts_at).total_seconds() / 3600.0)


async def ensure_timesheet_for_assignment(
    db: AsyncSession,
    assignment: ShiftAssignment,
    attendance: AttendanceRecord | None,
) -> tuple[TimesheetEntry | None, bool]:
    if assignment.status not in (AssignmentStatus.completed, AssignmentStatus.confirmed):
        return None, False
    r = await db.execute(
        select(TimesheetEntry).where(TimesheetEntry.shift_assignment_id == assignment.id)
    )
    existing = r.scalar_one_or_none()
    if existing:
        return existing, False

    shift = assignment.shift
    job = shift.job_order

    sched_h = (shift.ends_at - shift.starts_at).total_seconds() / 3600.0
    actual_h = _hours_since_shift_start(shift, attendance.clock_out_time if attendance else None)
    if actual_h <= 0 and attendance and attendance.total_hours:
        actual_h = float(attendance.total_hours)

    overtime_h = max(0.0, actual_h - sched_h) if sched_h > 0 else 0.0
    gross, ot = compute_timesheet_pay(job, sched_h, actual_h, overtime_h)

    entry = TimesheetEntry(
        job_order_id=job.id,
        shift_id=shift.id,
        worker_id=assignment.worker_id,
        shift_assignment_id=assignment.id,
        attendance_record_id=attendance.id if attendance else None,
        scheduled_hours=round(sched_h, 2),
        actual_hours=round(actual_h, 2),
        overtime_hours=round(ot, 2),
        hourly_rate_snapshot=float(job.hourly_rate or 0),
        per_shift_rate_snapshot=job.per_shift_rate,
        gross_pay=gross,
        status=TimesheetStatus.pending,
    )
    db.add(entry)
    await db.flush()
    return entry, True


async def mark_assignment_completed_if_clocked(
    db: AsyncSession,
    assignment: ShiftAssignment,
    attendance: AttendanceRecord,
) -> None:
    if attendance.clock_out_time and attendance.status in (
        AttendanceStatus.present,
        AttendanceStatus.late,
        AttendanceStatus.unverified,
    ):
        assignment.status = AssignmentStatus.completed
        _entry, created = await ensure_timesheet_for_assignment(db, assignment, attendance)
        if created:
            await adjust_worker_points(db, assignment.worker_id, POINTS_PER_COMPLETED_SHIFT)
            await enqueue_worker_recommendation_refresh(db, assignment.worker_id)


def _hours_from_attendance(attendance: AttendanceRecord, shift: Shift) -> tuple[float, float, float]:
    sched_h = (shift.ends_at - shift.starts_at).total_seconds() / 3600.0
    actual_h = _hours_since_shift_start(shift, attendance.clock_out_time)
    if actual_h <= 0:
        actual_h = float(attendance.total_hours or 0.0)
    overtime_h = max(0.0, actual_h - sched_h) if sched_h > 0 else 0.0
    return sched_h, actual_h, overtime_h


async def get_clock_out_pay_summary(
    db: AsyncSession,
    assignment: ShiftAssignment,
    attendance: AttendanceRecord,
    shift: Shift,
    job: JobOrder,
) -> dict:
    """Pay figures for API responses after clock-out; matches timesheet math when a row exists."""
    sched_h, actual_h, overtime_h = _hours_from_attendance(attendance, shift)
    gross_computed, ot_used = compute_timesheet_pay(job, sched_h, actual_h, overtime_h)

    r = await db.execute(
        select(TimesheetEntry).where(TimesheetEntry.shift_assignment_id == assignment.id)
    )
    ts = r.scalar_one_or_none()
    timesheet_created = ts is not None
    gross_pay = float(ts.gross_pay) if ts else gross_computed
    pay_pending_review = attendance.status == AttendanceStatus.flagged or not timesheet_created

    return {
        "total_hours": round(actual_h, 2),
        "actual_hours": round(actual_h, 2),
        "scheduled_hours": round(sched_h, 2),
        "overtime_hours": round(ot_used, 2),
        "gross_pay": gross_pay,
        "timesheet_created": timesheet_created,
        "pay_pending_review": pay_pending_review,
    }
