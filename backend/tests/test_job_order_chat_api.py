"""Job-order E2EE chat API: ACL and message persistence."""

import base64
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-jwt-tests-only-32b")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from auth_helpers import create_access_token
from app.config import get_settings
from app.db.base import Base
from app.db.session import get_db
from app.models import (
    AssignmentStatus,
    ClientOrganization,
    JobOrder,
    JobOrderChatMessage,
    JobOrderStatus,
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
from app.routers import job_order_chat as job_order_chat_router

get_settings.cache_clear()

_app = FastAPI()
_app.include_router(job_order_chat_router.router)


@pytest_asyncio.fixture
async def engine_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as session:
        yield engine, session
    await engine.dispose()


def _client_for_session(session: AsyncSession):
    async def _override():
        yield session

    _app.dependency_overrides[get_db] = _override
    return TestClient(_app)


@pytest.mark.asyncio
async def test_admin_forbidden(engine_session):
    engine, session = engine_session
    now = datetime.now(timezone.utc)
    admin = User(phone="+19990000001", role=UserRole.admin, locale="en")
    session.add(admin)
    await session.commit()

    tc = _client_for_session(session)
    token = create_access_token(str(admin.id), {"role": "admin"})
    r = tc.get("/job-order-chat", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    _app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_worker_client_ta_acl_and_message(engine_session):
    engine, session = engine_session
    now = datetime.now(timezone.utc)
    org = ClientOrganization(name="OrgChat")
    session.add(org)
    await session.flush()

    client_u = User(phone="+19990000002", role=UserRole.client, locale="en", client_org_id=org.id)
    worker_u = User(phone="+19990000003", role=UserRole.worker, locale="en")
    other_worker_u = User(phone="+19990000004", role=UserRole.worker, locale="en")
    ta_u = User(phone="+19990000005", role=UserRole.ta, locale="en")
    session.add_all([client_u, worker_u, other_worker_u, ta_u])
    await session.flush()

    wp = WorkerProfile(
        user_id=worker_u.id,
        full_name="Assigned",
        tier=WorkerTier.standard,
        status=WorkerStatus.active,
    )
    wp_other = WorkerProfile(
        user_id=other_worker_u.id,
        full_name="Other",
        tier=WorkerTier.standard,
        status=WorkerStatus.active,
    )
    session.add_all([wp, wp_other])
    await session.flush()

    job = JobOrder(
        client_org_id=org.id,
        created_by_user_id=client_u.id,
        title="Chat Job",
        job_type="Warehouse Worker",
        location_address="SG",
        location_lat=1.0,
        location_lng=103.0,
        workers_required_total=1,
        pay_model=PayModel.hourly,
        status=JobOrderStatus.open,
    )
    session.add(job)
    await session.flush()

    shift = Shift(
        job_order_id=job.id,
        starts_at=now,
        ends_at=now + timedelta(hours=4),
        workers_required=1,
    )
    session.add(shift)
    await session.flush()

    session.add(
        ShiftAssignment(
            shift_id=shift.id,
            worker_id=wp.id,
            status=AssignmentStatus.confirmed,
        )
    )
    await session.flush()

    session.add(
        TaClientContract(
            client_org_id=org.id,
            ta_user_id=ta_u.id,
            status=TaClientContractStatus.active,
            requested_by_user_id=client_u.id,
        )
    )
    await session.commit()

    tc = _client_for_session(session)
    worker_token = create_access_token(str(worker_u.id), {"role": "worker"})
    client_token = create_access_token(str(client_u.id), {"role": "client"})
    ta_token = create_access_token(str(ta_u.id), {"role": "ta"})
    other_token = create_access_token(str(other_worker_u.id), {"role": "worker"})

    rw = tc.get("/job-order-chat", headers={"Authorization": f"Bearer {worker_token}"})
    assert rw.status_code == 200
    jobs = rw.json()
    assert len(jobs) == 1 and jobs[0]["id"] == job.id

    ro = tc.get(f"/job-order-chat/{job.id}/participants", headers={"Authorization": f"Bearer {other_token}"})
    assert ro.status_code == 404

    rp = tc.get(f"/job-order-chat/{job.id}/participants", headers={"Authorization": f"Bearer {worker_token}"})
    assert rp.status_code == 200
    pids = {p["user_id"] for p in rp.json()}
    assert client_u.id in pids and worker_u.id in pids and ta_u.id in pids

    rc = tc.get(f"/job-order-chat/{job.id}/participants", headers={"Authorization": f"Bearer {client_token}"})
    assert rc.status_code == 200

    rt = tc.get(f"/job-order-chat/{job.id}/participants", headers={"Authorization": f"Bearer {ta_token}"})
    assert rt.status_code == 200

    fake_pk = base64.b64encode(b"\x01" * 32).decode("ascii")
    for uid, tok in [
        (worker_u.id, worker_token),
        (client_u.id, client_token),
        (ta_u.id, ta_token),
    ]:
        ir = tc.put(
            "/job-order-chat/me/identity",
            headers={"Authorization": f"Bearer {tok}"},
            json={"x25519_public_key_b64": fake_pk},
        )
        assert ir.status_code == 200, ir.text

    wrap = base64.b64encode(b"wrapped-demo-bytes").decode("ascii")
    pr = tc.put(
        f"/job-order-chat/{job.id}/wrappers",
        headers={"Authorization": f"Bearer {client_token}"},
        json={"target_user_id": worker_u.id, "wrapped_key_b64": wrap},
    )
    assert pr.status_code == 200, pr.text

    gw = tc.get(
        f"/job-order-chat/{job.id}/wrappers/me",
        headers={"Authorization": f"Bearer {worker_token}"},
    )
    assert gw.status_code == 200
    assert gw.json()["wrapped_key_b64"] == wrap

    ct = base64.b64encode(b"ciphertext").decode("ascii")
    iv = base64.b64encode(b"\x00" * 12).decode("ascii")
    mr = tc.post(
        f"/job-order-chat/{job.id}/messages",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"ciphertext_b64": ct, "iv_b64": iv},
    )
    assert mr.status_code == 200, mr.text
    mid = mr.json()["id"]

    lr = tc.get(
        f"/job-order-chat/{job.id}/messages",
        headers={"Authorization": f"Bearer {client_token}"},
    )
    assert lr.status_code == 200
    assert len(lr.json()) == 1
    assert lr.json()[0]["id"] == mid

    _app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ta_without_contract_denied(engine_session):
    engine, session = engine_session
    now = datetime.now(timezone.utc)
    org = ClientOrganization(name="O2")
    session.add(org)
    await session.flush()
    client_u = User(phone="+19990000010", role=UserRole.client, locale="en", client_org_id=org.id)
    ta_u = User(phone="+19990000011", role=UserRole.ta, locale="en")
    session.add_all([client_u, ta_u])
    await session.flush()
    job = JobOrder(
        client_org_id=org.id,
        created_by_user_id=client_u.id,
        title="No TA",
        job_type="Warehouse Worker",
        location_address="SG",
        location_lat=1.0,
        location_lng=103.0,
        workers_required_total=1,
        pay_model=PayModel.hourly,
        status=JobOrderStatus.open,
    )
    session.add(job)
    await session.commit()

    tc = _client_for_session(session)
    ta_token = create_access_token(str(ta_u.id), {"role": "ta"})
    r = tc.get(f"/job-order-chat/{job.id}/participants", headers={"Authorization": f"Bearer {ta_token}"})
    assert r.status_code == 404
    _app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_ta_only_sees_messages_after_contract_activation(engine_session):
    engine, session = engine_session
    now = datetime.now(timezone.utc)
    org = ClientOrganization(name="O3")
    session.add(org)
    await session.flush()

    client_u = User(phone="+19990000020", role=UserRole.client, locale="en", client_org_id=org.id)
    worker_u = User(phone="+19990000021", role=UserRole.worker, locale="en")
    ta_u = User(phone="+19990000022", role=UserRole.ta, locale="en")
    session.add_all([client_u, worker_u, ta_u])
    await session.flush()

    wp = WorkerProfile(user_id=worker_u.id, full_name="W1", tier=WorkerTier.standard, status=WorkerStatus.active)
    session.add(wp)
    await session.flush()

    job = JobOrder(
        client_org_id=org.id,
        created_by_user_id=client_u.id,
        title="Late TA contract",
        job_type="Warehouse Worker",
        location_address="SG",
        location_lat=1.0,
        location_lng=103.0,
        workers_required_total=1,
        pay_model=PayModel.hourly,
        status=JobOrderStatus.open,
    )
    session.add(job)
    await session.flush()

    shift = Shift(
        job_order_id=job.id,
        starts_at=now,
        ends_at=now + timedelta(hours=4),
        workers_required=1,
    )
    session.add(shift)
    await session.flush()
    session.add(ShiftAssignment(shift_id=shift.id, worker_id=wp.id, status=AssignmentStatus.confirmed))
    await session.commit()

    tc = _client_for_session(session)
    worker_token = create_access_token(str(worker_u.id), {"role": "worker"})
    ta_token = create_access_token(str(ta_u.id), {"role": "ta"})
    fake_pk = base64.b64encode(b"\x01" * 32).decode("ascii")
    for tok in (worker_token, ta_token):
        ir = tc.put(
            "/job-order-chat/me/identity",
            headers={"Authorization": f"Bearer {tok}"},
            json={"x25519_public_key_b64": fake_pk},
        )
        assert ir.status_code == 200, ir.text

    # Message before TA contract should remain hidden from TA.
    ct_old = base64.b64encode(b"before-contract").decode("ascii")
    iv = base64.b64encode(b"\x00" * 12).decode("ascii")
    mr_old = tc.post(
        f"/job-order-chat/{job.id}/messages",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"ciphertext_b64": ct_old, "iv_b64": iv},
    )
    assert mr_old.status_code == 200, mr_old.text

    old_id = mr_old.json()["id"]
    await session.execute(
        update(JobOrderChatMessage)
        .where(JobOrderChatMessage.id == old_id)
        .values(created_at=now - timedelta(minutes=15))
    )
    await session.commit()

    contract_cutoff = now - timedelta(minutes=5)
    session.add(
        TaClientContract(
            client_org_id=org.id,
            ta_user_id=ta_u.id,
            status=TaClientContractStatus.active,
            requested_by_user_id=client_u.id,
            requested_at=contract_cutoff,
            responded_at=contract_cutoff,
        )
    )
    await session.commit()

    ct_new = base64.b64encode(b"after-contract").decode("ascii")
    mr_new = tc.post(
        f"/job-order-chat/{job.id}/messages",
        headers={"Authorization": f"Bearer {worker_token}"},
        json={"ciphertext_b64": ct_new, "iv_b64": iv},
    )
    assert mr_new.status_code == 200, mr_new.text

    tr = tc.get(f"/job-order-chat/{job.id}/messages", headers={"Authorization": f"Bearer {ta_token}"})
    assert tr.status_code == 200
    rows = tr.json()
    assert len(rows) == 1
    assert rows[0]["id"] == mr_new.json()["id"]
    _app.dependency_overrides.clear()
