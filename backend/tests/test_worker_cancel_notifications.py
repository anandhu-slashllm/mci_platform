"""Worker assignment cancel: stakeholder alerts and waitlist auto-notify."""

import asyncio
import os
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
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
    Notification,
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
from app.routers import worker_app as worker_app_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(worker_app_router.router)


async def _notifications_for_user(Session, user_id: int) -> list[Notification]:
    async with Session() as session:
        return (
            await session.execute(
                select(Notification).where(Notification.user_id == user_id).order_by(Notification.id.asc())
            )
        ).scalars().all()


@contextmanager
def _worker_client(seed_coro):
    engine, Session, ids = asyncio.run(seed_coro)

    async def override_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    _app.dependency_overrides[get_db] = override_get_db
    token = create_access_token(str(ids["cancelling_worker_user_id"]), {})
    try:
        with TestClient(_app) as client:
            yield client, Session, ids, token
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


async def _seed_late_cancel(*, with_contract: bool, extra_ta: bool):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)
    shift_start = now + timedelta(hours=1)

    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+188801", role=UserRole.client, locale="en", client_org_id=org.id)
        ta_contract = User(phone="+188802", role=UserRole.ta, locale="en")
        session.add_all([client_user, ta_contract])
        if extra_ta:
            ta_other = User(phone="+188803", role=UserRole.ta, locale="en")
            session.add(ta_other)
        worker_user = User(phone="+188804", role=UserRole.worker, locale="en")
        session.add(worker_user)
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="Cancel Tester",
            location_lat=1.31,
            location_lng=103.81,
            job_type_preferences=["Warehouse Worker"],
            skills=["loading"],
            certifications=[],
            tier=WorkerTier.standard,
            status=WorkerStatus.active,
        )
        session.add(worker)
        await session.flush()

        job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Dock shift",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        session.add(job)
        await session.flush()

        shift = Shift(
            job_order_id=job.id,
            starts_at=shift_start,
            ends_at=shift_start + timedelta(hours=8),
            workers_required=1,
        )
        session.add(shift)
        await session.flush()

        assignment = ShiftAssignment(
            shift_id=shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.confirmed,
        )
        session.add(assignment)

        if with_contract:
            session.add(
                TaClientContract(
                    client_org_id=org.id,
                    ta_user_id=ta_contract.id,
                    status=TaClientContractStatus.active,
                    requested_by_user_id=client_user.id,
                    requested_at=now - timedelta(days=1),
                    responded_at=now - timedelta(hours=12),
                )
            )

        await session.commit()

        out = {
            "client_user_id": client_user.id,
            "ta_contract_id": ta_contract.id,
            "cancelling_worker_user_id": worker_user.id,
            "assignment_id": assignment.id,
        }
        if extra_ta:
            out["ta_other_id"] = ta_other.id
        return engine, Session, out


async def _seed_early_cancel_waitlist():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    shift_start = now + timedelta(days=2)

    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+188901", role=UserRole.client, locale="en", client_org_id=org.id)
        w1_user = User(phone="+188902", role=UserRole.worker, locale="en")
        w2_user = User(phone="+188903", role=UserRole.worker, locale="en")
        session.add_all([client_user, w1_user, w2_user])
        await session.flush()

        w1 = WorkerProfile(
            user_id=w1_user.id,
            full_name="Confirmed W",
            location_lat=1.31,
            location_lng=103.81,
            job_type_preferences=["Warehouse Worker"],
            skills=["loading"],
            certifications=[],
            tier=WorkerTier.standard,
            status=WorkerStatus.active,
        )
        w2 = WorkerProfile(
            user_id=w2_user.id,
            full_name="Waitlisted W",
            location_lat=1.31,
            location_lng=103.81,
            job_type_preferences=["Warehouse Worker"],
            skills=["loading"],
            certifications=[],
            tier=WorkerTier.standard,
            status=WorkerStatus.active,
        )
        session.add_all([w1, w2])
        await session.flush()

        job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Future shift",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=2,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        session.add(job)
        await session.flush()

        shift = Shift(
            job_order_id=job.id,
            starts_at=shift_start,
            ends_at=shift_start + timedelta(hours=8),
            workers_required=2,
        )
        session.add(shift)
        await session.flush()

        a_conf = ShiftAssignment(
            shift_id=shift.id,
            worker_id=w1.id,
            status=AssignmentStatus.confirmed,
        )
        a_wait = ShiftAssignment(
            shift_id=shift.id,
            worker_id=w2.id,
            status=AssignmentStatus.waitlisted,
            waitlist_rank=1,
        )
        session.add_all([a_conf, a_wait])
        await session.commit()

        return engine, Session, {
            "cancelling_worker_user_id": w1_user.id,
            "waitlisted_worker_user_id": w2_user.id,
            "assignment_id": a_conf.id,
        }


def test_late_cancel_notifies_client_and_contracted_ta_only():
    with _worker_client(_seed_late_cancel(with_contract=True, extra_ta=True)) as (client, Session, ids, token):
        res = client.post(
            f"/worker/assignments/{ids['assignment_id']}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["late_cancellation"] is True

        n_client = asyncio.run(_notifications_for_user(Session, ids["client_user_id"]))
        n_ta_c = asyncio.run(_notifications_for_user(Session, ids["ta_contract_id"]))
        n_ta_o = asyncio.run(_notifications_for_user(Session, ids["ta_other_id"]))
        assert len(n_client) == 1
        assert n_client[0].payload.get("type") == "late_cancellation"
        assert len(n_ta_c) == 1
        assert n_ta_c[0].payload.get("type") == "late_cancellation"
        assert len(n_ta_o) == 0


def test_late_cancel_without_contract_notifies_client_not_tas():
    with _worker_client(_seed_late_cancel(with_contract=False, extra_ta=True)) as (client, Session, ids, token):
        res = client.post(
            f"/worker/assignments/{ids['assignment_id']}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text

        n_client = asyncio.run(_notifications_for_user(Session, ids["client_user_id"]))
        n_ta_c = asyncio.run(_notifications_for_user(Session, ids["ta_contract_id"]))
        n_ta_o = asyncio.run(_notifications_for_user(Session, ids["ta_other_id"]))
        assert len(n_client) == 1
        assert n_client[0].payload.get("type") == "late_cancellation"
        assert len(n_ta_c) == 0
        assert len(n_ta_o) == 0


def test_early_cancel_notifies_next_waitlisted_worker():
    with _worker_client(_seed_early_cancel_waitlist()) as (client, Session, ids, token):
        res = client.post(
            f"/worker/assignments/{ids['assignment_id']}/cancel",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["late_cancellation"] is False

        n_wait = asyncio.run(_notifications_for_user(Session, ids["waitlisted_worker_user_id"]))
        assert len(n_wait) == 1
        assert n_wait[0].payload.get("type") == "waitlist_opening"
