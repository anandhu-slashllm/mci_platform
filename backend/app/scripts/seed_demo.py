"""Seed realistic demo data for local walkthroughs."""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.auth.supabase_auth import get_auth_client
from app.db.session import AsyncSessionLocal
from app.services.auth_accounts import ensure_auth_user
from app.models import (
    AssignmentStatus,
    AttendanceRecord,
    AttendanceStatus,
    ClientOrganization,
    ClientProfile,
    JobOrder,
    JobOrderStatus,
    JobTypeWeight,
    Notification,
    PayModel,
    PaymentRecord,
    PaymentStatus,
    PreferredWorker,
    Shift,
    ShiftAssignment,
    ShiftOffer,
    ShiftOfferStatus,
    TaClientContract,
    TaClientContractStatus,
    TimesheetEntry,
    TimesheetStatus,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)

# Demo-only credentials for POST /auth/login.
DEMO_LOGIN_BY_PHONE: dict[str, tuple[str, str]] = {
    "+6590000001": ("worker", "demo_worker"),
    "+6590000005": ("worker_2", "demo_worker_2"),
    "+6590000006": ("worker_3", "demo_worker_3"),
    "+6590000007": ("worker_4", "demo_worker_4"),
    "+6590000010": ("worker_6", "demo_worker_6"),
    "+6590000011": ("worker_7", "demo_worker_7"),
    "+6590000012": ("worker_8", "demo_worker_8"),
    "+6590000008": ("worker_5", "demo_worker_5"),
    "+6590000002": ("ta", "demo_ta"),
    "+6590000009": ("ta_2", "demo_ta_2"),
    "+6590000003": ("client", "demo_client"),
    "+6590000013": ("client_2", "demo_client_2"),
    "+6590000004": ("admin", "demo_admin"),
}


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _hours(start: datetime, end: datetime) -> float:
    return max((end - start).total_seconds() / 3600, 0.0)


async def _upsert_model(db, model_cls, where_clause, values: dict[str, Any]):
    row = (await db.execute(select(model_cls).where(*where_clause))).scalar_one_or_none()
    if row is None:
        row = model_cls(**values)
        db.add(row)
        await db.flush()
        return row
    for field, value in values.items():
        setattr(row, field, value)
    await db.flush()
    return row


def apply_demo_login(user: User, phone: str) -> None:
    pair = DEMO_LOGIN_BY_PHONE.get(phone)
    if not pair:
        return
    user.username = pair[0]


async def link_demo_logins(db) -> None:
    """Create/update the Supabase Auth user (email + demo password) for every demo account."""
    client = get_auth_client()
    for phone, (_username, plain) in DEMO_LOGIN_BY_PHONE.items():
        user = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
        if user is not None:
            await ensure_auth_user(db, client, user, password=plain)


async def ensure_user(
    db,
    *,
    phone: str,
    role: UserRole,
    display_name: str,
    org_id: int | None = None,
    email: str | None = None,
    worker_profile: dict[str, Any] | None = None,
    client_profile: dict[str, Any] | None = None,
) -> tuple[User, WorkerProfile | None]:
    user = await _upsert_model(
        db,
        User,
        [User.phone == phone],
        {
            "phone": phone,
            "email": email,
            "display_name": display_name,
            "role": role,
            "locale": "en",
            "client_org_id": org_id,
            "is_active": True,
        },
    )
    apply_demo_login(user, phone)

    worker = None
    if role == UserRole.worker:
        profile_values = {
            "user_id": user.id,
            "full_name": display_name,
            "languages": ["en"],
            "job_type_preferences": ["Warehouse Worker"],
            "skills": ["packing"],
            "certifications": [],
            "availability": {"always": True},
            "location_lat": 1.3521,
            "location_lng": 103.8198,
            "rating_avg": 4.2,
            "completion_rate": 0.92,
            "tier": WorkerTier.standard,
            "status": WorkerStatus.active,
            "points": 100,
            "no_show_count": 0,
            "late_cancel_count": 0,
            "profile_completeness_pct": 85,
            "work_pass_not_applicable": True,
            "pdpa_consent_at": datetime.now(timezone.utc) - timedelta(days=120),
            "terms_accepted_at": datetime.now(timezone.utc) - timedelta(days=120),
            "onboarding_completed_at": datetime.now(timezone.utc) - timedelta(days=110),
        }
        if worker_profile:
            profile_values.update(worker_profile)
        worker = await _upsert_model(
            db,
            WorkerProfile,
            [WorkerProfile.user_id == user.id],
            profile_values,
        )

    if role == UserRole.client:
        base_client_profile = {
            "user_id": user.id,
            "company_name": "Demo Warehouse Pte Ltd",
            "contact_name": display_name,
            "contact_email": email,
            "contact_phone": phone,
            "address_line": "1 Harbourfront Walk, Singapore",
            "location_lat": 1.2644,
            "location_lng": 103.8223,
            "billing_notes": "Net 14 demo terms",
            "profile_completeness_pct": 90,
            "pdpa_consent_at": datetime.now(timezone.utc) - timedelta(days=90),
            "terms_accepted_at": datetime.now(timezone.utc) - timedelta(days=90),
            "onboarding_completed_at": datetime.now(timezone.utc) - timedelta(days=85),
        }
        if client_profile:
            base_client_profile.update(client_profile)
        await _upsert_model(
            db,
            ClientProfile,
            [ClientProfile.user_id == user.id],
            base_client_profile,
        )

    return user, worker


async def main() -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    async with AsyncSessionLocal() as db:
        for jt, p, r, j, c, t in [
            ("_default", 0.3, 0.25, 0.2, 0.15, 0.1),
            ("Warehouse Worker", 0.25, 0.25, 0.15, 0.25, 0.1),
            ("Packing Assistant", 0.25, 0.25, 0.15, 0.25, 0.1),
            ("Inventory Assistant", 0.2, 0.3, 0.15, 0.25, 0.1),
            ("Retail Crew", 0.2, 0.3, 0.15, 0.25, 0.1),
        ]:
            await _upsert_model(
                db,
                JobTypeWeight,
                [JobTypeWeight.job_type == jt],
                {
                    "job_type": jt,
                    "weight_proximity": p,
                    "weight_rating": r,
                    "weight_job_type": j,
                    "weight_completion": c,
                    "weight_tier": t,
                },
            )

        org_demo = await _upsert_model(
            db,
            ClientOrganization,
            [ClientOrganization.name == "Demo Warehouse Pte Ltd"],
            {"name": "Demo Warehouse Pte Ltd"},
        )
        org_retail = await _upsert_model(
            db,
            ClientOrganization,
            [ClientOrganization.name == "Harbor Retail Group"],
            {"name": "Harbor Retail Group"},
        )

        u_worker, w1 = await ensure_user(
            db,
            phone="+6590000001",
            role=UserRole.worker,
            display_name="Lucy Tan",
            email="lucy.tan@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "zh"],
                "job_type_preferences": ["Warehouse Worker", "Packing Assistant"],
                "skills": ["packing", "inventory_count"],
                "certifications": [],
                "location_lat": 1.3521,
                "location_lng": 103.8198,
                "rating_avg": 4.4,
                "tier": WorkerTier.standard,
                "points": 200,
                "profile_completeness_pct": 88,
            },
        )
        _, w2 = await ensure_user(
            db,
            phone="+6590000005",
            role=UserRole.worker,
            display_name="Amir Rahman",
            email="amir.rahman@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "ms"],
                "job_type_preferences": ["Warehouse Worker"],
                "skills": ["packing", "inventory", "loading"],
                "certifications": ["forklift"],
                "location_lat": 1.33,
                "location_lng": 103.85,
                "rating_avg": 4.8,
                "tier": WorkerTier.preferred,
                "points": 230,
                "profile_completeness_pct": 94,
            },
        )
        _, w3 = await ensure_user(
            db,
            phone="+6590000006",
            role=UserRole.worker,
            display_name="Mei Lin",
            email="mei.lin@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "zh"],
                "job_type_preferences": ["Retail Crew", "Packing Assistant"],
                "skills": ["cash_handling", "customer_service", "packing"],
                "certifications": ["food_hygiene", "first_aid"],
                "location_lat": 1.295,
                "location_lng": 103.86,
                "rating_avg": 4.6,
                "tier": WorkerTier.elite,
                "points": 280,
                "profile_completeness_pct": 96,
            },
        )
        _, w4 = await ensure_user(
            db,
            phone="+6590000007",
            role=UserRole.worker,
            display_name="David Koh",
            email="david.koh@mci-demoapp.com",
            worker_profile={
                "languages": ["en"],
                "job_type_preferences": ["Warehouse Worker"],
                "skills": ["loading", "sorting"],
                "certifications": [],
                "location_lat": 1.38,
                "location_lng": 103.76,
                "rating_avg": 4.0,
                "tier": WorkerTier.standard,
                "points": 90,
                "profile_completeness_pct": 82,
            },
        )
        _, w5 = await ensure_user(
            db,
            phone="+6590000008",
            role=UserRole.worker,
            display_name="Nur Aisyah",
            email="nur.aisyah@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "ms"],
                "job_type_preferences": ["Warehouse Worker", "Inventory Assistant"],
                "skills": ["packing", "qc", "inventory"],
                "certifications": ["forklift", "wsq_safety"],
                "location_lat": 1.315,
                "location_lng": 103.7,
                "rating_avg": 4.9,
                "tier": WorkerTier.elite,
                "points": 320,
                "profile_completeness_pct": 97,
            },
        )
        _, w6 = await ensure_user(
            db,
            phone="+6590000010",
            role=UserRole.worker,
            display_name="Ravi Kumar",
            email="ravi.kumar@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "ta"],
                "job_type_preferences": ["Inventory Assistant", "Warehouse Worker"],
                "skills": ["inventory", "scanner_ops"],
                "certifications": [],
                "location_lat": 1.34,
                "location_lng": 103.78,
                "rating_avg": 4.1,
                "tier": WorkerTier.probation,
                "points": 65,
                "profile_completeness_pct": 79,
            },
        )
        _, w7 = await ensure_user(
            db,
            phone="+6590000011",
            role=UserRole.worker,
            display_name="Sarah Lee",
            email="sarah.lee@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "zh"],
                "job_type_preferences": ["Retail Crew"],
                "skills": ["customer_service", "stock_take"],
                "certifications": ["first_aid"],
                "location_lat": 1.312,
                "location_lng": 103.83,
                "rating_avg": 4.5,
                "tier": WorkerTier.preferred,
                "points": 210,
                "profile_completeness_pct": 91,
            },
        )
        _, w8 = await ensure_user(
            db,
            phone="+6590000012",
            role=UserRole.worker,
            display_name="Faizal Noor",
            email="faizal.noor@mci-demoapp.com",
            worker_profile={
                "languages": ["en", "ms"],
                "job_type_preferences": ["Warehouse Worker"],
                "skills": ["loading", "route_sorting"],
                "certifications": ["wsq_safety"],
                "location_lat": 1.36,
                "location_lng": 103.91,
                "rating_avg": 4.3,
                "tier": WorkerTier.standard,
                "points": 130,
                "profile_completeness_pct": 86,
            },
        )

        u_ta, _ = await ensure_user(
            db,
            phone="+6590000002",
            role=UserRole.ta,
            display_name="Jared TA",
            email="jared.ta@mci-demoapp.com",
        )
        u_ta_2, _ = await ensure_user(
            db,
            phone="+6590000009",
            role=UserRole.ta,
            display_name="Samira TA",
            email="samira.ta@mci-demoapp.com",
        )
        client_u, _ = await ensure_user(
            db,
            phone="+6590000003",
            role=UserRole.client,
            display_name="Mr Wong",
            org_id=org_demo.id,
            email="ops@demowarehouse.sg",
            client_profile={
                "company_name": "Demo Warehouse Pte Ltd",
                "contact_name": "Mr Wong",
                "contact_email": "ops@demowarehouse.sg",
                "contact_phone": "+6590000003",
                "address_line": "1 Harbourfront Walk, Singapore",
                "location_lat": 1.2644,
                "location_lng": 103.8223,
            },
        )
        client2_u, _ = await ensure_user(
            db,
            phone="+6590000013",
            role=UserRole.client,
            display_name="Grace Lim",
            org_id=org_retail.id,
            email="ops@harborretail.sg",
            client_profile={
                "company_name": "Harbor Retail Group",
                "contact_name": "Grace Lim",
                "contact_email": "ops@harborretail.sg",
                "contact_phone": "+6590000013",
                "address_line": "3 Temasek Blvd, Singapore",
                "location_lat": 1.2956,
                "location_lng": 103.8584,
            },
        )
        await ensure_user(
            db,
            phone="+6590000004",
            role=UserRole.admin,
            display_name="Admin User",
            email="admin@mci-demoapp.com",
        )

        await _upsert_model(
            db,
            TaClientContract,
            [
                TaClientContract.client_org_id == org_retail.id,
                TaClientContract.ta_user_id == u_ta_2.id,
                TaClientContract.status == TaClientContractStatus.terminated,
            ],
            {
                "client_org_id": org_retail.id,
                "ta_user_id": u_ta_2.id,
                "status": TaClientContractStatus.terminated,
                "requested_by_user_id": client2_u.id,
                "requested_at": now - timedelta(days=30),
                "responded_at": now - timedelta(days=25),
                "message": "Previous engagement completed after seasonal campaign",
            },
        )
        await _upsert_model(
            db,
            TaClientContract,
            [
                TaClientContract.client_org_id == org_demo.id,
                TaClientContract.ta_user_id == u_ta.id,
                TaClientContract.status == TaClientContractStatus.active,
            ],
            {
                "client_org_id": org_demo.id,
                "ta_user_id": u_ta.id,
                "status": TaClientContractStatus.active,
                "requested_by_user_id": client_u.id,
                "requested_at": now - timedelta(days=10),
                "responded_at": now - timedelta(days=9),
                "message": "Active demo contract for TA payroll scenarios",
            },
        )

        demo_job_draft = await _upsert_model(
            db,
            JobOrder,
            [JobOrder.client_org_id == org_demo.id, JobOrder.title == "Demo packing shift"],
            {
                "client_org_id": org_demo.id,
                "created_by_user_id": u_ta.id,
                "title": "Demo packing shift",
                "job_type": "Warehouse Worker",
                "location_address": "1 Harbourfront Walk, Singapore",
                "location_lat": 1.2644,
                "location_lng": 103.8223,
                "workers_required_total": 3,
                "hourly_rate": 15.0,
                "per_shift_rate": None,
                "pay_model": PayModel.hourly,
                "required_skills": ["packing"],
                "required_certifications": [],
                "uniform_notes": "Safety vest and covered shoes",
                "special_instructions": "Demo draft job for publish walkthrough",
                "geofence_radius_m": 300,
                "acceptance_window_minutes": 120,
                "status": JobOrderStatus.draft,
                "published_at": None,
            },
        )

        open_job = await _upsert_model(
            db,
            JobOrder,
            [JobOrder.client_org_id == org_demo.id, JobOrder.title == "Dock loading weekend rush"],
            {
                "client_org_id": org_demo.id,
                "created_by_user_id": u_ta.id,
                "title": "Dock loading weekend rush",
                "job_type": "Warehouse Worker",
                "location_address": "5 Benoi Road, Singapore",
                "location_lat": 1.3273,
                "location_lng": 103.7082,
                "workers_required_total": 4,
                "hourly_rate": 17.5,
                "per_shift_rate": None,
                "pay_model": PayModel.hourly,
                "required_skills": ["loading", "inventory"],
                "required_certifications": ["forklift"],
                "uniform_notes": "Reflective vest mandatory",
                "special_instructions": "Weekend inbound surge coverage",
                "geofence_radius_m": 250,
                "status": JobOrderStatus.open,
                "published_at": now - timedelta(hours=3),
            },
        )

        live_job = await _upsert_model(
            db,
            JobOrder,
            [JobOrder.client_org_id == org_demo.id, JobOrder.title == "Inventory recount night shift"],
            {
                "client_org_id": org_demo.id,
                "created_by_user_id": u_ta.id,
                "title": "Inventory recount night shift",
                "job_type": "Inventory Assistant",
                "location_address": "20 Gul Way, Singapore",
                "location_lat": 1.3119,
                "location_lng": 103.6639,
                "workers_required_total": 3,
                "hourly_rate": 18.0,
                "per_shift_rate": None,
                "pay_model": PayModel.hourly,
                "required_skills": ["inventory", "scanner_ops"],
                "required_certifications": [],
                "uniform_notes": "Dark polo + safety shoes",
                "special_instructions": "Cycle-count and bin reconciliation",
                "geofence_radius_m": 180,
                "status": JobOrderStatus.partially_filled,
                "published_at": now - timedelta(days=2),
            },
        )

        completed_job = await _upsert_model(
            db,
            JobOrder,
            [JobOrder.client_org_id == org_retail.id, JobOrder.title == "Retail pop-up teardown"],
            {
                "client_org_id": org_retail.id,
                "created_by_user_id": u_ta_2.id,
                "title": "Retail pop-up teardown",
                "job_type": "Retail Crew",
                "location_address": "3 Temasek Blvd, Singapore",
                "location_lat": 1.2956,
                "location_lng": 103.8584,
                "workers_required_total": 3,
                "hourly_rate": None,
                "per_shift_rate": 120.0,
                "pay_model": PayModel.per_shift,
                "required_skills": ["customer_service", "stock_take"],
                "required_certifications": [],
                "uniform_notes": "Black tee, jeans",
                "special_instructions": "Post-event teardown and restocking",
                "geofence_radius_m": 150,
                "status": JobOrderStatus.completed,
                "published_at": now - timedelta(days=8),
            },
        )
        history_job = await _upsert_model(
            db,
            JobOrder,
            [JobOrder.client_org_id == org_demo.id, JobOrder.title == "Cycle count backfill"],
            {
                "client_org_id": org_demo.id,
                "created_by_user_id": u_ta.id,
                "title": "Cycle count backfill",
                "job_type": "Inventory Assistant",
                "location_address": "12 Tuas South Ave, Singapore",
                "location_lat": 1.3055,
                "location_lng": 103.6402,
                "workers_required_total": 2,
                "hourly_rate": 16.5,
                "per_shift_rate": None,
                "pay_model": PayModel.hourly,
                "required_skills": ["inventory", "scanner_ops"],
                "required_certifications": [],
                "uniform_notes": "Hi-vis vest",
                "special_instructions": "Used for worker schedule/history demo",
                "geofence_radius_m": 200,
                "status": JobOrderStatus.completed,
                "published_at": now - timedelta(days=6),
            },
        )

        shift_draft = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == demo_job_draft.id, Shift.starts_at == now + timedelta(days=1, hours=1)],
            {
                "job_order_id": demo_job_draft.id,
                "starts_at": now + timedelta(days=1, hours=1),
                "ends_at": now + timedelta(days=1, hours=9),
                "workers_required": 3,
            },
        )
        shift_open_1 = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == open_job.id, Shift.starts_at == now + timedelta(days=2, hours=1)],
            {
                "job_order_id": open_job.id,
                "starts_at": now + timedelta(days=2, hours=1),
                "ends_at": now + timedelta(days=2, hours=10),
                "workers_required": 2,
            },
        )
        shift_open_2 = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == open_job.id, Shift.starts_at == now + timedelta(days=3, hours=1)],
            {
                "job_order_id": open_job.id,
                "starts_at": now + timedelta(days=3, hours=1),
                "ends_at": now + timedelta(days=3, hours=10),
                "workers_required": 2,
            },
        )
        shift_live = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == live_job.id, Shift.starts_at == now - timedelta(hours=1)],
            {
                "job_order_id": live_job.id,
                "starts_at": now - timedelta(hours=1),
                "ends_at": now + timedelta(hours=7),
                "workers_required": 3,
            },
        )
        shift_completed = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == completed_job.id, Shift.starts_at == now - timedelta(days=3, hours=9)],
            {
                "job_order_id": completed_job.id,
                "starts_at": now - timedelta(days=3, hours=9),
                "ends_at": now - timedelta(days=3, hours=1),
                "workers_required": 3,
            },
        )
        shift_late_cancel = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == open_job.id, Shift.starts_at == now + timedelta(hours=8)],
            {
                "job_order_id": open_job.id,
                "starts_at": now + timedelta(hours=8),
                "ends_at": now + timedelta(hours=16),
                "workers_required": 1,
            },
        )
        shift_history_completed = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == history_job.id, Shift.starts_at == now - timedelta(days=4, hours=9)],
            {
                "job_order_id": history_job.id,
                "starts_at": now - timedelta(days=4, hours=9),
                "ends_at": now - timedelta(days=4, hours=1),
                "workers_required": 2,
            },
        )
        shift_history_cancelled = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == history_job.id, Shift.starts_at == now - timedelta(days=1, hours=6)],
            {
                "job_order_id": history_job.id,
                "starts_at": now - timedelta(days=1, hours=6),
                "ends_at": now - timedelta(days=1),
                "workers_required": 2,
            },
        )
        shift_history_late = await _upsert_model(
            db,
            Shift,
            [Shift.job_order_id == history_job.id, Shift.starts_at == now - timedelta(days=2, hours=10)],
            {
                "job_order_id": history_job.id,
                "starts_at": now - timedelta(days=2, hours=10),
                "ends_at": now - timedelta(days=2, hours=2),
                "workers_required": 2,
            },
        )

        await _upsert_model(
            db,
            PreferredWorker,
            [PreferredWorker.client_org_id == org_demo.id, PreferredWorker.worker_id == w2.id],
            {"client_org_id": org_demo.id, "worker_id": w2.id},
        )
        await _upsert_model(
            db,
            PreferredWorker,
            [PreferredWorker.client_org_id == org_demo.id, PreferredWorker.worker_id == w5.id],
            {"client_org_id": org_demo.id, "worker_id": w5.id},
        )

        assign_live_w1 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_live.id, ShiftAssignment.worker_id == w1.id],
            {
                "shift_id": shift_live.id,
                "worker_id": w1.id,
                "status": AssignmentStatus.confirmed,
                "match_score": 88.2,
                "confirmed_at": now - timedelta(days=1),
                "waitlist_rank": None,
            },
        )
        assign_live_w2 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_live.id, ShiftAssignment.worker_id == w2.id],
            {
                "shift_id": shift_live.id,
                "worker_id": w2.id,
                "status": AssignmentStatus.confirmed,
                "match_score": 93.7,
                "confirmed_at": now - timedelta(days=1),
                "waitlist_rank": None,
            },
        )
        await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_live.id, ShiftAssignment.worker_id == w6.id],
            {
                "shift_id": shift_live.id,
                "worker_id": w6.id,
                "status": AssignmentStatus.waitlisted,
                "match_score": 71.4,
                "waitlist_rank": 1,
                "confirmed_at": None,
            },
        )
        await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_open_1.id, ShiftAssignment.worker_id == w5.id],
            {
                "shift_id": shift_open_1.id,
                "worker_id": w5.id,
                "status": AssignmentStatus.applied,
                "match_score": 95.1,
                "waitlist_rank": None,
                "confirmed_at": None,
            },
        )
        await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_open_1.id, ShiftAssignment.worker_id == w7.id],
            {
                "shift_id": shift_open_1.id,
                "worker_id": w7.id,
                "status": AssignmentStatus.rejected,
                "match_score": 62.0,
                "waitlist_rank": None,
                "confirmed_at": None,
                "cancellation_reason": "Missing forklift certification",
            },
        )
        assign_history_w1 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_history_completed.id, ShiftAssignment.worker_id == w1.id],
            {
                "shift_id": shift_history_completed.id,
                "worker_id": w1.id,
                "status": AssignmentStatus.completed,
                "match_score": 86.3,
                "waitlist_rank": None,
                "confirmed_at": now - timedelta(days=5),
            },
        )
        await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_history_cancelled.id, ShiftAssignment.worker_id == w1.id],
            {
                "shift_id": shift_history_cancelled.id,
                "worker_id": w1.id,
                "status": AssignmentStatus.cancelled,
                "match_score": 82.7,
                "waitlist_rank": None,
                "confirmed_at": now - timedelta(days=4),
                "cancellation_reason": "Early cancellation (>48h) due to demand drop",
            },
        )
        assign_history_late_w2 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_history_late.id, ShiftAssignment.worker_id == w2.id],
            {
                "shift_id": shift_history_late.id,
                "worker_id": w2.id,
                "status": AssignmentStatus.completed,
                "match_score": 91.2,
                "waitlist_rank": None,
                "confirmed_at": now - timedelta(days=4),
            },
        )
        assign_late_cancel_w6 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_late_cancel.id, ShiftAssignment.worker_id == w6.id],
            {
                "shift_id": shift_late_cancel.id,
                "worker_id": w6.id,
                "status": AssignmentStatus.cancelled,
                "match_score": 79.2,
                "waitlist_rank": None,
                "confirmed_at": now - timedelta(hours=2),
                "cancellation_reason": "Late cancellation (<24h) by worker for family emergency",
            },
        )
        assign_history_no_show_w7 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_history_late.id, ShiftAssignment.worker_id == w7.id],
            {
                "shift_id": shift_history_late.id,
                "worker_id": w7.id,
                "status": AssignmentStatus.no_show,
                "match_score": 68.5,
                "confirmed_at": now - timedelta(days=4),
                "waitlist_rank": None,
                "cancellation_reason": "No show in TA-contracted org scenario",
            },
        )
        assign_completed_w3 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_completed.id, ShiftAssignment.worker_id == w3.id],
            {
                "shift_id": shift_completed.id,
                "worker_id": w3.id,
                "status": AssignmentStatus.completed,
                "match_score": 90.0,
                "confirmed_at": now - timedelta(days=5),
                "waitlist_rank": None,
            },
        )
        assign_completed_w4 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_completed.id, ShiftAssignment.worker_id == w4.id],
            {
                "shift_id": shift_completed.id,
                "worker_id": w4.id,
                "status": AssignmentStatus.no_show,
                "match_score": 73.5,
                "confirmed_at": now - timedelta(days=5),
                "waitlist_rank": None,
                "cancellation_reason": "Did not report to site",
            },
        )
        assign_completed_w8 = await _upsert_model(
            db,
            ShiftAssignment,
            [ShiftAssignment.shift_id == shift_completed.id, ShiftAssignment.worker_id == w8.id],
            {
                "shift_id": shift_completed.id,
                "worker_id": w8.id,
                "status": AssignmentStatus.completed,
                "match_score": 84.4,
                "confirmed_at": now - timedelta(days=5),
                "waitlist_rank": None,
            },
        )

        await _upsert_model(
            db,
            ShiftOffer,
            [ShiftOffer.shift_id == shift_draft.id, ShiftOffer.worker_id == w1.id],
            {
                "shift_id": shift_draft.id,
                "worker_id": w1.id,
                "status": ShiftOfferStatus.pending,
                "match_score": 87.4,
                "expires_at": shift_draft.starts_at - timedelta(hours=8),
            },
        )
        await _upsert_model(
            db,
            ShiftOffer,
            [ShiftOffer.shift_id == shift_open_2.id, ShiftOffer.worker_id == w6.id],
            {
                "shift_id": shift_open_2.id,
                "worker_id": w6.id,
                "status": ShiftOfferStatus.declined,
                "match_score": 74.3,
                "expires_at": shift_open_2.starts_at - timedelta(hours=12),
            },
        )
        await _upsert_model(
            db,
            ShiftOffer,
            [ShiftOffer.shift_id == shift_open_2.id, ShiftOffer.worker_id == w2.id],
            {
                "shift_id": shift_open_2.id,
                "worker_id": w2.id,
                "status": ShiftOfferStatus.accepted,
                "match_score": 92.2,
                "expires_at": shift_open_2.starts_at - timedelta(hours=12),
            },
        )
        await _upsert_model(
            db,
            ShiftOffer,
            [ShiftOffer.shift_id == shift_open_1.id, ShiftOffer.worker_id == w4.id],
            {
                "shift_id": shift_open_1.id,
                "worker_id": w4.id,
                "status": ShiftOfferStatus.expired,
                "match_score": 69.5,
                "expires_at": now - timedelta(hours=1),
            },
        )

        attendance_live_w1 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_live_w1.id],
            {
                "shift_assignment_id": assign_live_w1.id,
                "clock_in_time": shift_live.starts_at + timedelta(minutes=3),
                "clock_in_photo_url": "https://demo.local/attendance/w1-in.jpg",
                "clock_in_lat": live_job.location_lat,
                "clock_in_lng": live_job.location_lng,
                "clock_in_geofence_valid": True,
                "clock_out_time": None,
                "clock_out_photo_url": None,
                "clock_out_lat": None,
                "clock_out_lng": None,
                "total_hours": None,
                "status": AttendanceStatus.present,
            },
        )
        attendance_history_w1 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_history_w1.id],
            {
                "shift_assignment_id": assign_history_w1.id,
                "clock_in_time": shift_history_completed.starts_at + timedelta(minutes=1),
                "clock_in_photo_url": "https://demo.local/attendance/w1-history-in.jpg",
                "clock_in_lat": history_job.location_lat,
                "clock_in_lng": history_job.location_lng,
                "clock_in_geofence_valid": True,
                "clock_out_time": shift_history_completed.ends_at + timedelta(minutes=6),
                "clock_out_photo_url": "https://demo.local/attendance/w1-history-out.jpg",
                "clock_out_lat": history_job.location_lat,
                "clock_out_lng": history_job.location_lng,
                "total_hours": _hours(
                    shift_history_completed.starts_at + timedelta(minutes=1),
                    shift_history_completed.ends_at + timedelta(minutes=6),
                ),
                "status": AttendanceStatus.present,
            },
        )
        attendance_history_late_w2 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_history_late_w2.id],
            {
                "shift_assignment_id": assign_history_late_w2.id,
                "clock_in_time": shift_history_late.starts_at + timedelta(minutes=34),
                "clock_in_photo_url": "https://demo.local/attendance/w2-history-late-in.jpg",
                "clock_in_lat": history_job.location_lat,
                "clock_in_lng": history_job.location_lng,
                "clock_in_geofence_valid": True,
                "clock_out_time": shift_history_late.ends_at + timedelta(minutes=2),
                "clock_out_photo_url": "https://demo.local/attendance/w2-history-late-out.jpg",
                "clock_out_lat": history_job.location_lat,
                "clock_out_lng": history_job.location_lng,
                "total_hours": _hours(
                    shift_history_late.starts_at + timedelta(minutes=34),
                    shift_history_late.ends_at + timedelta(minutes=2),
                ),
                "status": AttendanceStatus.late,
            },
        )
        await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_history_no_show_w7.id],
            {
                "shift_assignment_id": assign_history_no_show_w7.id,
                "clock_in_time": None,
                "clock_in_photo_url": None,
                "clock_in_lat": None,
                "clock_in_lng": None,
                "clock_in_geofence_valid": None,
                "clock_out_time": None,
                "clock_out_photo_url": None,
                "clock_out_lat": None,
                "clock_out_lng": None,
                "total_hours": 0.0,
                "status": AttendanceStatus.absent,
                "absent_after_grace_ta_notified_at": shift_history_late.starts_at + timedelta(minutes=20),
                "absence_strike_recorded_at": shift_history_late.starts_at + timedelta(minutes=25),
            },
        )
        attendance_live_w2 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_live_w2.id],
            {
                "shift_assignment_id": assign_live_w2.id,
                "clock_in_time": shift_live.starts_at + timedelta(minutes=22),
                "clock_in_photo_url": "https://demo.local/attendance/w2-in.jpg",
                "clock_in_lat": live_job.location_lat,
                "clock_in_lng": live_job.location_lng,
                "clock_in_geofence_valid": True,
                "clock_out_time": None,
                "clock_out_photo_url": None,
                "clock_out_lat": None,
                "clock_out_lng": None,
                "total_hours": None,
                "status": AttendanceStatus.late,
            },
        )
        attendance_completed_w3 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_completed_w3.id],
            {
                "shift_assignment_id": assign_completed_w3.id,
                "clock_in_time": shift_completed.starts_at + timedelta(minutes=2),
                "clock_in_photo_url": "https://demo.local/attendance/w3-in.jpg",
                "clock_in_lat": completed_job.location_lat,
                "clock_in_lng": completed_job.location_lng,
                "clock_in_geofence_valid": True,
                "clock_out_time": shift_completed.ends_at + timedelta(minutes=15),
                "clock_out_photo_url": "https://demo.local/attendance/w3-out.jpg",
                "clock_out_lat": completed_job.location_lat,
                "clock_out_lng": completed_job.location_lng,
                "total_hours": _hours(shift_completed.starts_at + timedelta(minutes=2), shift_completed.ends_at + timedelta(minutes=15)),
                "status": AttendanceStatus.flagged,
            },
        )
        attendance_completed_w4 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_completed_w4.id],
            {
                "shift_assignment_id": assign_completed_w4.id,
                "clock_in_time": None,
                "clock_in_photo_url": None,
                "clock_in_lat": None,
                "clock_in_lng": None,
                "clock_in_geofence_valid": None,
                "clock_out_time": None,
                "clock_out_photo_url": None,
                "clock_out_lat": None,
                "clock_out_lng": None,
                "total_hours": 0.0,
                "status": AttendanceStatus.absent,
                "absent_after_grace_ta_notified_at": shift_completed.starts_at + timedelta(minutes=20),
                "absence_strike_recorded_at": shift_completed.starts_at + timedelta(minutes=25),
            },
        )
        attendance_completed_w8 = await _upsert_model(
            db,
            AttendanceRecord,
            [AttendanceRecord.shift_assignment_id == assign_completed_w8.id],
            {
                "shift_assignment_id": assign_completed_w8.id,
                "clock_in_time": shift_completed.starts_at + timedelta(minutes=5),
                "clock_in_photo_url": "https://demo.local/attendance/w8-in.jpg",
                "clock_in_lat": completed_job.location_lat,
                "clock_in_lng": completed_job.location_lng,
                "clock_in_geofence_valid": True,
                "clock_out_time": shift_completed.ends_at,
                "clock_out_photo_url": "https://demo.local/attendance/w8-out.jpg",
                "clock_out_lat": completed_job.location_lat,
                "clock_out_lng": completed_job.location_lng,
                "total_hours": _hours(shift_completed.starts_at + timedelta(minutes=5), shift_completed.ends_at),
                "status": AttendanceStatus.present,
            },
        )

        timesheet_w1 = await _upsert_model(
            db,
            TimesheetEntry,
            [TimesheetEntry.shift_assignment_id == assign_live_w1.id],
            {
                "job_order_id": live_job.id,
                "shift_id": shift_live.id,
                "worker_id": w1.id,
                "shift_assignment_id": assign_live_w1.id,
                "attendance_record_id": attendance_live_w1.id,
                "scheduled_hours": _hours(shift_live.starts_at, shift_live.ends_at),
                "actual_hours": 0.0,
                "overtime_hours": 0.0,
                "hourly_rate_snapshot": live_job.hourly_rate or 0.0,
                "per_shift_rate_snapshot": None,
                "gross_pay": 0.0,
                "status": TimesheetStatus.pending,
                "dispute_flag": False,
                "dispute_note": None,
            },
        )
        timesheet_w2 = await _upsert_model(
            db,
            TimesheetEntry,
            [TimesheetEntry.shift_assignment_id == assign_live_w2.id],
            {
                "job_order_id": live_job.id,
                "shift_id": shift_live.id,
                "worker_id": w2.id,
                "shift_assignment_id": assign_live_w2.id,
                "attendance_record_id": attendance_live_w2.id,
                "scheduled_hours": _hours(shift_live.starts_at, shift_live.ends_at),
                "actual_hours": 0.0,
                "overtime_hours": 0.0,
                "hourly_rate_snapshot": live_job.hourly_rate or 0.0,
                "per_shift_rate_snapshot": None,
                "gross_pay": 0.0,
                "status": TimesheetStatus.ta_verified,
                "dispute_flag": False,
                "dispute_note": None,
                "employer_endorsed_at": now - timedelta(hours=1),
                "employer_endorsed_by_user_id": client_u.id,
                "ta_verified_at": now - timedelta(minutes=30),
                "ta_verified_by_user_id": u_ta.id,
            },
        )
        timesheet_history_w1 = await _upsert_model(
            db,
            TimesheetEntry,
            [TimesheetEntry.shift_assignment_id == assign_history_w1.id],
            {
                "job_order_id": history_job.id,
                "shift_id": shift_history_completed.id,
                "worker_id": w1.id,
                "shift_assignment_id": assign_history_w1.id,
                "attendance_record_id": attendance_history_w1.id,
                "scheduled_hours": _hours(shift_history_completed.starts_at, shift_history_completed.ends_at),
                "actual_hours": attendance_history_w1.total_hours or 8.0,
                "overtime_hours": 0.0,
                "hourly_rate_snapshot": history_job.hourly_rate or 0.0,
                "per_shift_rate_snapshot": None,
                "gross_pay": round((attendance_history_w1.total_hours or 8.0) * (history_job.hourly_rate or 0.0), 2),
                "status": TimesheetStatus.ta_verified,
                "dispute_flag": False,
                "dispute_note": None,
                "employer_endorsed_at": now - timedelta(days=3),
                "employer_endorsed_by_user_id": client_u.id,
                "ta_verified_at": now - timedelta(days=2, hours=12),
                "ta_verified_by_user_id": u_ta.id,
            },
        )
        await _upsert_model(
            db,
            TimesheetEntry,
            [TimesheetEntry.shift_assignment_id == assign_history_late_w2.id],
            {
                "job_order_id": history_job.id,
                "shift_id": shift_history_late.id,
                "worker_id": w2.id,
                "shift_assignment_id": assign_history_late_w2.id,
                "attendance_record_id": attendance_history_late_w2.id,
                "scheduled_hours": _hours(shift_history_late.starts_at, shift_history_late.ends_at),
                "actual_hours": attendance_history_late_w2.total_hours or 7.5,
                "overtime_hours": 0.0,
                "hourly_rate_snapshot": history_job.hourly_rate or 0.0,
                "per_shift_rate_snapshot": None,
                "gross_pay": round((attendance_history_late_w2.total_hours or 7.5) * (history_job.hourly_rate or 0.0), 2),
                "status": TimesheetStatus.ta_verified,
                "dispute_flag": False,
                "dispute_note": "Arrived late due to traffic; approved",
                "employer_endorsed_at": now - timedelta(days=2),
                "employer_endorsed_by_user_id": client_u.id,
                "ta_verified_at": now - timedelta(days=1, hours=18),
                "ta_verified_by_user_id": u_ta.id,
            },
        )
        timesheet_w3 = await _upsert_model(
            db,
            TimesheetEntry,
            [TimesheetEntry.shift_assignment_id == assign_completed_w3.id],
            {
                "job_order_id": completed_job.id,
                "shift_id": shift_completed.id,
                "worker_id": w3.id,
                "shift_assignment_id": assign_completed_w3.id,
                "attendance_record_id": attendance_completed_w3.id,
                "scheduled_hours": _hours(shift_completed.starts_at, shift_completed.ends_at),
                "actual_hours": attendance_completed_w3.total_hours or 8.0,
                "overtime_hours": max((attendance_completed_w3.total_hours or 8.0) - _hours(shift_completed.starts_at, shift_completed.ends_at), 0.0),
                "hourly_rate_snapshot": 16.0,
                "per_shift_rate_snapshot": completed_job.per_shift_rate,
                "gross_pay": 128.0,
                "status": TimesheetStatus.ta_verified,
                "dispute_flag": True,
                "dispute_note": "Clock-out photo blurred, manually validated by TA",
                "employer_endorsed_at": now - timedelta(days=2),
                "employer_endorsed_by_user_id": client2_u.id,
                "ta_verified_at": now - timedelta(days=1),
                "ta_verified_by_user_id": u_ta_2.id,
            },
        )
        timesheet_w8 = await _upsert_model(
            db,
            TimesheetEntry,
            [TimesheetEntry.shift_assignment_id == assign_completed_w8.id],
            {
                "job_order_id": completed_job.id,
                "shift_id": shift_completed.id,
                "worker_id": w8.id,
                "shift_assignment_id": assign_completed_w8.id,
                "attendance_record_id": attendance_completed_w8.id,
                "scheduled_hours": _hours(shift_completed.starts_at, shift_completed.ends_at),
                "actual_hours": attendance_completed_w8.total_hours or 7.9,
                "overtime_hours": 0.0,
                "hourly_rate_snapshot": 16.0,
                "per_shift_rate_snapshot": completed_job.per_shift_rate,
                "gross_pay": 120.0,
                "status": TimesheetStatus.client_payroll_released,
                "dispute_flag": False,
                "dispute_note": None,
                "employer_endorsed_at": now - timedelta(days=2),
                "employer_endorsed_by_user_id": client2_u.id,
                "ta_verified_at": now - timedelta(days=1),
                "ta_verified_by_user_id": u_ta_2.id,
            },
        )

        await _upsert_model(
            db,
            PaymentRecord,
            # Key on the demo file too: real payroll runs add more records for the same worker.
            [PaymentRecord.worker_id == w8.id, PaymentRecord.payment_instructions_file_url == "https://demo.local/payroll/payment-batch-apr.csv"],
            {
                "worker_id": w8.id,
                "timesheet_entry_ids": [timesheet_w8.id, timesheet_w3.id],
                "total_amount": round(timesheet_w8.gross_pay + timesheet_w3.gross_pay, 2),
                "payment_status": PaymentStatus.paid,
                "payment_instructions_file_url": "https://demo.local/payroll/payment-batch-apr.csv",
                "paid_at": now - timedelta(hours=12),
            },
        )
        await _upsert_model(
            db,
            PaymentRecord,
            # Key on the demo file too: real payroll runs add more records for the same worker.
            [PaymentRecord.worker_id == w1.id, PaymentRecord.payment_instructions_file_url == "https://demo.local/payroll/payment-batch-worker1.csv"],
            {
                "worker_id": w1.id,
                "timesheet_entry_ids": [timesheet_history_w1.id],
                "total_amount": timesheet_history_w1.gross_pay,
                "payment_status": PaymentStatus.paid,
                "payment_instructions_file_url": "https://demo.local/payroll/payment-batch-worker1.csv",
                "paid_at": now - timedelta(days=2),
            },
        )

        w6.late_cancel_count = 2
        for uid, title, body, payload in [
            (
                u_worker.id,
                "Shift reminder",
                "Upcoming shift starts in 2 hours: Inventory recount night shift.",
                {"type": "shift_reminder", "job_id": live_job.id, "shift_id": shift_live.id},
            ),
            (
                u_worker.id,
                "Payment received",
                f"Your payout of {timesheet_history_w1.gross_pay:.2f} SGD has been marked paid.",
                {"type": "payment_paid", "timesheet_id": timesheet_history_w1.id},
            ),
            (
                client_u.id,
                "Late cancellation alert",
                "A confirmed worker cancelled within 24h for Dock loading weekend rush.",
                {"type": "late_cancellation", "assignment_id": assign_late_cancel_w6.id, "shift_id": shift_late_cancel.id},
            ),
            (
                u_ta.id,
                "Timesheet ready for verification",
                "A client-endorsed timesheet is pending TA verification.",
                {"type": "timesheet_ta_verify", "timesheet_id": timesheet_w2.id},
            ),
        ]:
            await _upsert_model(
                db,
                Notification,
                [Notification.user_id == uid, Notification.title == title, Notification.body == body],
                {"user_id": uid, "title": title, "body": body, "payload": payload},
            )

        await link_demo_logins(db)
        await db.commit()
        print(
            "Realistic seed complete. Login (POST /auth/login): "
            "worker/demo_worker, worker_2/demo_worker_2, worker_3/demo_worker_3, "
            "worker_4/demo_worker_4, worker_5/demo_worker_5, worker_6/demo_worker_6, "
            "worker_7/demo_worker_7, worker_8/demo_worker_8, ta/demo_ta, ta_2/demo_ta_2, "
            "client/demo_client, client_2/demo_client_2, admin/demo_admin. "
            "Accounts with an email can also use email OTP (local codes land in Mailpit). "
            f"Scenario timestamps are anchored to { _iso(now) } UTC."
        )


if __name__ == "__main__":
    asyncio.run(main())
