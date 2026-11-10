"""Overlap notifications route to the contract owner or client org."""

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
    ShiftOffer,
    ShiftOfferStatus,
    TaClientContract,
    TaClientContractStatus,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
    WorkerTier,
)
from app.routers import job_orders as job_orders_router
from app.routers import worker_app as worker_app_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(job_orders_router.router)
_app.include_router(worker_app_router.router)


async def _notifications_for_users(Session, *user_ids: int) -> list[Notification]:
    async with Session() as session:
        return (
            await session.execute(
                select(Notification)
                .where(Notification.user_id.in_(user_ids))
                .order_by(Notification.user_id.asc(), Notification.id.asc())
            )
        ).scalars().all()


async def _seed_promote_overlap(active_contract: bool):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)

    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+17772220001", role=UserRole.client, locale="en", client_org_id=org.id)
        ta_user = User(phone="+17772220002", role=UserRole.ta, locale="en")
        worker_user = User(phone="+17772220003", role=UserRole.worker, locale="en")
        session.add_all([client_user, ta_user, worker_user])
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="Worker One",
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
        other_job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Other Job",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        session.add_all([current_job, other_job])
        await session.flush()

        current_shift = Shift(
            job_order_id=current_job.id,
            starts_at=now,
            ends_at=now + timedelta(hours=8),
            workers_required=1,
        )
        other_shift = Shift(
            job_order_id=other_job.id,
            starts_at=now + timedelta(hours=3),
            ends_at=now + timedelta(hours=9),
            workers_required=1,
        )
        session.add_all([current_shift, other_shift])
        await session.flush()

        promoted_assignment = ShiftAssignment(
            shift_id=current_shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.waitlisted,
        )
        conflicting_assignment = ShiftAssignment(
            shift_id=other_shift.id,
            worker_id=worker.id,
            status=AssignmentStatus.confirmed,
        )
        session.add_all([promoted_assignment, conflicting_assignment])

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
            "worker_user_id": worker_user.id,
            "assignment_id": promoted_assignment.id,
        }


async def _seed_accept_overlap(active_contract: bool):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)

    async with Session() as session:
        org = ClientOrganization(name="Acme")
        session.add(org)
        await session.flush()

        client_user = User(phone="+17773330001", role=UserRole.client, locale="en", client_org_id=org.id)
        ta_user = User(phone="+17773330002", role=UserRole.ta, locale="en")
        worker_user = User(phone="+17773330003", role=UserRole.worker, locale="en")
        session.add_all([client_user, ta_user, worker_user])
        await session.flush()

        worker = WorkerProfile(
            user_id=worker_user.id,
            full_name="Worker One",
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
        other_job = JobOrder(
            client_org_id=org.id,
            created_by_user_id=client_user.id,
            title="Other Job",
            job_type="Warehouse Worker",
            location_address="SG",
            location_lat=1.35,
            location_lng=103.82,
            workers_required_total=1,
            pay_model=PayModel.hourly,
            hourly_rate=20.0,
            status=JobOrderStatus.open,
        )
        session.add_all([current_job, other_job])
        await session.flush()

        current_shift = Shift(
            job_order_id=current_job.id,
            starts_at=now,
            ends_at=now + timedelta(hours=8),
            workers_required=1,
        )
        other_shift = Shift(
            job_order_id=other_job.id,
            starts_at=now + timedelta(hours=2),
            ends_at=now + timedelta(hours=6),
            workers_required=1,
        )
        session.add_all([current_shift, other_shift])
        await session.flush()

        session.add(
            ShiftAssignment(
                shift_id=other_shift.id,
                worker_id=worker.id,
                status=AssignmentStatus.confirmed,
            )
        )
        offer = ShiftOffer(
            shift_id=current_shift.id,
            worker_id=worker.id,
            status=ShiftOfferStatus.pending,
            match_score=0.8,
        )
        session.add(offer)

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
            "worker_user_id": worker_user.id,
            "offer_id": offer.id,
        }


@contextmanager
def notification_client(seed_coro):
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
    tokens = {
        "client": create_access_token(str(ids["client_user_id"]), {}),
        "ta": create_access_token(str(ids["ta_user_id"]), {}),
    }
    if "worker_user_id" in ids:
        tokens["worker"] = create_access_token(str(ids["worker_user_id"]), {})
    try:
        with TestClient(_app) as client:
            yield client, Session, tokens, ids
    finally:
        _app.dependency_overrides.clear()
        asyncio.run(engine.dispose())


def test_promoted_overlap_without_ta_contract_notifies_client_only():
    with notification_client(_seed_promote_overlap(active_contract=False)) as (client, Session, tokens, ids):
        res = client.post(
            f"/job-orders/assignments/{ids['assignment_id']}/promote-from-waitlist",
            headers={"Authorization": f"Bearer {tokens['client']}"},
        )
        assert res.status_code == 200, res.text

        notifications = asyncio.run(_notifications_for_users(Session, ids["client_user_id"], ids["ta_user_id"]))
        client_notifications = [n for n in notifications if n.user_id == ids["client_user_id"]]
        ta_notifications = [n for n in notifications if n.user_id == ids["ta_user_id"]]
        assert len(client_notifications) == 1
        assert client_notifications[0].title == "Shift overlap conflict"
        assert client_notifications[0].payload["type"] == "overlap_conflict"
        assert ta_notifications == []


def test_accepted_overlap_without_ta_contract_notifies_client_only():
    with notification_client(_seed_accept_overlap(active_contract=False)) as (client, Session, tokens, ids):
        res = client.post(
            f"/worker/offers/{ids['offer_id']}/accept",
            headers={"Authorization": f"Bearer {tokens['worker']}"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["assignment_status"] == "confirmed"

        notifications = asyncio.run(_notifications_for_users(Session, ids["client_user_id"], ids["ta_user_id"]))
        client_notifications = [n for n in notifications if n.user_id == ids["client_user_id"]]
        ta_notifications = [n for n in notifications if n.user_id == ids["ta_user_id"]]
        assert len(client_notifications) == 1
        assert client_notifications[0].title == "Shift overlap conflict"
        assert client_notifications[0].payload["type"] == "overlap_conflict"
        assert ta_notifications == []


def test_conflict_reject_notifies_worker_about_conflicting_shift():
    with notification_client(_seed_promote_overlap(active_contract=False)) as (client, Session, tokens, ids):
        res = client.post(
            f"/job-orders/assignments/{ids['assignment_id']}/reject",
            headers={"Authorization": f"Bearer {tokens['client']}"},
            json={"reason": "schedule_conflict"},
        )
        assert res.status_code == 200, res.text

        notifications = asyncio.run(
            _notifications_for_users(Session, ids["client_user_id"], ids["ta_user_id"], ids["worker_user_id"])
        )
        worker_notifications = [n for n in notifications if n.user_id == ids["worker_user_id"]]
        client_notifications = [n for n in notifications if n.user_id == ids["client_user_id"]]
        ta_notifications = [n for n in notifications if n.user_id == ids["ta_user_id"]]
        assert len(worker_notifications) == 1
        assert worker_notifications[0].title == "Shift assignment rejected"
        assert "conflicts with another confirmed shift" in worker_notifications[0].body
        assert worker_notifications[0].payload["type"] == "assignment_rejected_conflict"
        assert client_notifications == []
        assert ta_notifications == []


def test_regular_reject_does_not_send_conflict_reject_notification():
    with notification_client(_seed_promote_overlap(active_contract=False)) as (client, Session, tokens, ids):
        res = client.post(
            f"/job-orders/assignments/{ids['assignment_id']}/reject",
            headers={"Authorization": f"Bearer {tokens['client']}"},
        )
        assert res.status_code == 200, res.text

        notifications = asyncio.run(
            _notifications_for_users(Session, ids["client_user_id"], ids["ta_user_id"], ids["worker_user_id"])
        )
        assert notifications == []
