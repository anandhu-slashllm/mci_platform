"""Job-order overlap visibility and removal behavior."""

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AssignmentStatus,
    ClientOrganization,
    JobOrder,
    JobOrderStatus,
    JobTypeWeight,
    PayModel,
    Shift,
    ShiftAssignment,
    TaClientContract,
    TaClientContractStatus,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.routers import job_orders as job_orders_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(job_orders_router.router)


async def _seed(active_contract: bool, worker_status: WorkerStatus = WorkerStatus.active):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)

    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+17771110001", role=UserRole.client, locale="en", client_org_id=org.id)
        ta_user = User(phone="+17771110002", role=UserRole.ta, locale="en")
        worker_user = User(phone="+17771110003", role=UserRole.worker, locale="en")
        session.add_all([client_user, ta_user, worker_user])
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="Worker One",
            location_lat=1.31,
            location_lng=103.81,
            rating_avg=4.8,
            job_type_preferences=["Warehouse Worker"],
            completion_rate=0.9,
            certifications=["forklift"],
            skills=["loading"],
            tier=WorkerTier.standard,
            status=worker_status,
        )
        session.add(worker)
        session.add(
            JobTypeWeight(
                job_type="Warehouse Worker",
                weight_proximity=0.2,
                weight_rating=0.3,
                weight_job_type=0.2,
                weight_completion=0.2,
                weight_tier=0.1,
            )
        )
        await session.flush()

        current_job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Current Job",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        overlap_job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Overlap Job",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        adjacent_job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Adjacent Job",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        pending_job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Pending Job",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        session.add_all([current_job, overlap_job, adjacent_job, pending_job])
        await session.flush()

        current_shift = Shift(
            job_order_id=current_job.id,
            starts_at=now,
            ends_at=now + timedelta(hours=8),
            workers_required=1,
        )
        overlap_shift = Shift(
            job_order_id=overlap_job.id,
            starts_at=now + timedelta(hours=3),
            ends_at=now + timedelta(hours=9),
            workers_required=1,
        )
        adjacent_shift = Shift(
            job_order_id=adjacent_job.id,
            starts_at=now + timedelta(hours=8),
            ends_at=now + timedelta(hours=12),
            workers_required=1,
        )
        pending_shift = Shift(
            job_order_id=pending_job.id,
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=6),
            workers_required=1,
        )
        session.add_all([current_shift, overlap_shift, adjacent_shift, pending_shift])
        await session.flush()

        current_assignment = ShiftAssignment(
            shift_id=current_shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.confirmed,
        )
        overlap_assignment = ShiftAssignment(
            shift_id=overlap_shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.confirmed,
        )
        adjacent_assignment = ShiftAssignment(
            shift_id=adjacent_shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.confirmed,
        )
        pending_assignment = ShiftAssignment(
            shift_id=pending_shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.waitlisted,
        )
        session.add_all([current_assignment, overlap_assignment, adjacent_assignment, pending_assignment])

        if active_contract:
            session.add(
                TaClientContract(
                    client_org_id=org.id,
                    ta_user_id=ta_user.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=client_user.id,
                    requested_at=now - timedelta(days=1),
                    responded_at=now - timedelta(hours=12),
                )
            )

        await session.commit()
        return engine, Session, {
            "client_user_id": client_user.id,
            "ta_user_id": ta_user.id,
            "job_id": current_job.id,
            "current_assignment_id": current_assignment.id,
            "pending_assignment_id": pending_assignment.id,
        }


@contextmanager
def overlap_api_client(*, active_contract: bool, worker_status: WorkerStatus = WorkerStatus.active):
    engine, Session, ids = asyncio.run(_seed(active_contract, worker_status))

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    tokens = {
        "client": create_access_token(str(ids["client_user_id"]), {}),
        "ta": create_access_token(str(ids["ta_user_id"]), {}),
    }
    try:
        with TestClient(_app) as client:
            yield client, tokens, ids
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_ta_detail_shows_only_confirmed_external_overlaps():
    with overlap_api_client(active_contract=True) as (client, tokens, ids):
        res = client.get(f"/job-orders/{ids['job_id']}", headers=_auth(tokens["ta"]))
        assert res.status_code == 200, res.text

        detail = res.json()
        assert detail["viewer_can_vet_assignments"] is True
        assert len(detail["overlapping_assignments"]) == 1

        overlap = detail["overlapping_assignments"][0]
        assert overlap["current_assignment_id"] == ids["current_assignment_id"]
        assert overlap["overlap_job_title"] == "Overlap Job"
        assert overlap["overlap_job_id"] != ids["job_id"]


def test_client_detail_hides_overlap_data_when_ta_contract_is_active():
    with overlap_api_client(active_contract=True) as (client, tokens, ids):
        res = client.get(f"/job-orders/{ids['job_id']}", headers=_auth(tokens["client"]))
        assert res.status_code == 200, res.text

        detail = res.json()
        assert detail["viewer_can_vet_assignments"] is False
        assert detail["overlapping_assignments"] == []


def test_client_cannot_reject_assignment_when_ta_contract_is_active():
    with overlap_api_client(active_contract=True) as (client, tokens, ids):
        reject_res = client.post(
            f"/job-orders/assignments/{ids['current_assignment_id']}/reject",
            headers=_auth(tokens["client"]),
        )
        assert reject_res.status_code == 403, reject_res.text


def test_rejecting_current_job_assignment_clears_overlap_from_detail():
    with overlap_api_client(active_contract=True) as (client, tokens, ids):
        reject_res = client.post(
            f"/job-orders/assignments/{ids['current_assignment_id']}/reject",
            headers=_auth(tokens["ta"]),
        )
        assert reject_res.status_code == 200, reject_res.text

        detail_res = client.get(f"/job-orders/{ids['job_id']}", headers=_auth(tokens["ta"]))
        assert detail_res.status_code == 200, detail_res.text
        detail = detail_res.json()
        assert detail["overlapping_assignments"] == []
        assert len(detail["rejected_workers"]) == 1
        assert detail["rejected_workers"][0]["assignment_id"] == ids["current_assignment_id"]


def test_client_without_ta_contract_can_manage_current_job_overlaps():
    with overlap_api_client(active_contract=False) as (client, tokens, ids):
        detail_res = client.get(f"/job-orders/{ids['job_id']}", headers=_auth(tokens["client"]))
        assert detail_res.status_code == 200, detail_res.text
        detail = detail_res.json()
        assert detail["viewer_can_vet_assignments"] is True
        assert len(detail["overlapping_assignments"]) == 1

        reject_res = client.post(
            f"/job-orders/assignments/{ids['current_assignment_id']}/reject",
            headers=_auth(tokens["client"]),
        )
        assert reject_res.status_code == 200, reject_res.text

        refreshed = client.get(f"/job-orders/{ids['job_id']}", headers=_auth(tokens["client"]))
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["overlapping_assignments"] == []


def test_blacklisted_worker_cannot_be_promoted_from_waitlist():
    with overlap_api_client(active_contract=True, worker_status=WorkerStatus.blacklisted) as (client, tokens, ids):
        promote_res = client.post(
            f"/job-orders/assignments/{ids['pending_assignment_id']}/promote-from-waitlist",
            headers=_auth(tokens["ta"]),
        )
        assert promote_res.status_code == 403, promote_res.text
        assert "blacklisted" in promote_res.text.lower()
