from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.deps import require_roles
from app.auth.supabase_auth import SupabaseAuthClient, get_auth_client
from app.config import get_settings
from app.services.storage import get_storage, new_object_key, storage_ref
from app.db.session import get_db
from app.services.auth_accounts import sync_auth_email
from app.models import (
    ClientOrganization,
    Document,
    JobOrder,
    PreferredWorker,
    Rating,
    Shift,
    ShiftAssignment,
    User,
    UserRole,
    WorkerProfile,
    WorkerStatus,
)
from app.schemas.workers import (
    OnboardingCompleteIn,
    PreferredClientOut,
    ProfileCompletenessDetailOut,
    ProfileCompletenessItemOut,
    WorkerDirectoryEntryOut,
    WorkerManagementOut,
    WorkerManagementUpdateIn,
    WorkerProfileIn,
    WorkerProfileOut,
    WorkerRatingItemOut,
    WorkerReputationOut,
)
from app.services.audit import log_audit
from app.services.crypto_pii import encrypt_value, mask_nric

router = APIRouter(prefix="/workers", tags=["workers"])

_ALLOWED_DOC_CT = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/heic",
        "application/pdf",
    }
)


def _completeness_flags(wp: WorkerProfile, user: User, doc_types: set[str]) -> dict[str, bool]:
    avail = wp.availability if isinstance(wp.availability, dict) else {}
    avail_filled = bool(avail) and any(bool(avail.get(k)) for k in ("morning", "evening", "weekend", "always"))
    cert_doc = "certification" in doc_types or "certifications" in doc_types
    train_doc = "training_record" in doc_types or "training" in doc_types
    prof_proof = cert_doc or train_doc or bool(wp.certifications)
    return {
        "full_name": bool(wp.full_name and str(wp.full_name).strip()),
        "nric": bool(wp.nric_encrypted or wp.nric_masked),
        "phone": bool(user.phone and str(user.phone).strip()),
        "email": bool(user.email and str(user.email).strip()),
        "languages": bool(wp.languages),
        "job_type_preferences": bool(wp.job_type_preferences),
        "availability": avail_filled,
        "skills": bool(wp.skills),
        "bank_account": bool(wp.bank_encrypted),
        "document_nric": "nric_copy" in doc_types or "nric" in doc_types,
        "work_pass": "work_pass" in doc_types or bool(wp.work_pass_not_applicable),
        "document_certification": cert_doc,
        "document_training": train_doc,
        "certifications_list": bool(wp.certifications),
        "bank_name": bool(wp.bank_name and str(wp.bank_name).strip()),
        "location": wp.location_lat is not None and wp.location_lng is not None,
        "pdpa_consent": bool(wp.pdpa_consent_at),
        "terms_accepted": bool(wp.terms_accepted_at),
        "professional_proof": prof_proof,
    }


def _weighted_completeness(wp: WorkerProfile, user: User, doc_types: set[str]) -> int:
    """Weights: Basic 20%, Preferences 20%, Bank 15%, Documents 35%, Optional 10%."""
    f = _completeness_flags(wp, user, doc_types)
    s = 0.0
    if f["full_name"]:
        s += 5
    if f["nric"]:
        s += 5
    if f["phone"]:
        s += 5
    if f["email"]:
        s += 5
    if f["languages"]:
        s += 5
    if f["job_type_preferences"]:
        s += 5
    if f["availability"]:
        s += 5
    if f["skills"]:
        s += 5
    if f["bank_account"]:
        s += 15
    if f["document_nric"]:
        s += 10
    if f["work_pass"]:
        s += 8
    if f["document_certification"]:
        s += 8.5
    if f["document_training"]:
        s += 8.5
    if f["certifications_list"]:
        s += 4
    if f["bank_name"]:
        s += 2
    if f["location"]:
        s += 4
    return int(min(100, round(s)))


def _completeness_checklist(wp: WorkerProfile, user: User, doc_types: set[str]) -> list[ProfileCompletenessItemOut]:
    f = _completeness_flags(wp, user, doc_types)
    specs: list[tuple[str, bool, bool]] = [
        ("full_name", f["full_name"], True),
        ("nric_fin", f["nric"], True),
        ("phone", f["phone"], True),
        ("email", f["email"], True),
        ("languages", f["languages"], True),
        ("job_type_preferences", f["job_type_preferences"], True),
        ("available_hours", f["availability"], True),
        ("skills", f["skills"], True),
        ("bank_account", f["bank_account"], True),
        ("bank_name", f["bank_name"], False),
        ("home_location", f["location"], False),
        ("document_nric_copy", f["document_nric"], True),
        ("work_pass_or_exempt", f["work_pass"], True),
        ("certifications_or_training", f["professional_proof"], True),
        ("pdpa_consent", f["pdpa_consent"], True),
        ("terms_accepted", f["terms_accepted"], True),
    ]
    return [
        ProfileCompletenessItemOut(id=i, done=d, required_for_compliance=r) for i, d, r in specs
    ]


async def _doc_types_for_worker(db: AsyncSession, worker_profile_id: int) -> set[str]:
    r = await db.execute(select(Document.doc_type).where(Document.worker_id == worker_profile_id))
    return {row[0] for row in r.all()}


async def _recalc_completeness(db: AsyncSession, wp: WorkerProfile, user: User) -> int:
    types = await _doc_types_for_worker(db, wp.id)
    pct = _weighted_completeness(wp, user, types)
    wp.profile_completeness_pct = pct
    return pct


def _profile_out(wp: WorkerProfile, user: User) -> WorkerProfileOut:
    return WorkerProfileOut(
        id=wp.id,
        full_name=wp.full_name,
        nric_masked=wp.nric_masked,
        phone=user.phone or "",
        email=user.email,
        languages=wp.languages or [],
        job_type_preferences=wp.job_type_preferences or [],
        skills=wp.skills or [],
        certifications=wp.certifications or [],
        availability=wp.availability or {},
        location_lat=wp.location_lat,
        location_lng=wp.location_lng,
        bank_name=wp.bank_name,
        work_pass_not_applicable=bool(wp.work_pass_not_applicable),
        rating_avg=wp.rating_avg,
        tier=wp.tier.value,
        status=wp.status.value,
        profile_completeness_pct=wp.profile_completeness_pct,
        pdpa_consent=bool(wp.pdpa_consent_at),
        terms_accepted=bool(wp.terms_accepted_at),
        onboarding_completed=bool(wp.onboarding_completed_at),
    )


def _directory_row_out(wp: WorkerProfile, user: User) -> WorkerDirectoryEntryOut:
    return WorkerDirectoryEntryOut(
        id=wp.id,
        full_name=wp.full_name or "",
        phone=user.phone or "",
        email=user.email,
        status=wp.status.value,
        internal_notes=wp.internal_notes,
        follow_up_required=bool(wp.follow_up_required),
    )


def _worker_management_out(wp: WorkerProfile, user: User) -> WorkerManagementOut:
    return WorkerManagementOut(
        id=wp.id,
        full_name=wp.full_name,
        phone=user.phone or "",
        email=user.email,
        languages=wp.languages or [],
        job_type_preferences=wp.job_type_preferences or [],
        skills=wp.skills or [],
        certifications=wp.certifications or [],
        rating_avg=wp.rating_avg,
        tier=wp.tier.value,
        status=wp.status.value,
        internal_notes=wp.internal_notes,
        follow_up_required=bool(wp.follow_up_required),
    )


@router.get("/me/reputation", response_model=WorkerReputationOut)
async def get_my_reputation(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(status_code=404, detail="Profile not found")

    stmt = (
        select(Rating, User, ClientOrganization, JobOrder.title, Shift.starts_at)
        .join(User, Rating.rated_by_user_id == User.id)
        .outerjoin(ClientOrganization, User.client_org_id == ClientOrganization.id)
        .outerjoin(ShiftAssignment, Rating.shift_assignment_id == ShiftAssignment.id)
        .outerjoin(Shift, ShiftAssignment.shift_id == Shift.id)
        .outerjoin(JobOrder, Shift.job_order_id == JobOrder.id)
        .where(Rating.worker_id == wp.id)
        .order_by(Rating.created_at.desc())
        .limit(50)
    )
    r2 = await db.execute(stmt)
    ratings_out: list[WorkerRatingItemOut] = []
    for rating, _rater, co, job_title, shift_starts in r2.all():
        ratings_out.append(
            WorkerRatingItemOut(
                stars=rating.stars,
                comment=rating.comment,
                created_at=rating.created_at,
                client_org_id=co.id if co else None,
                client_org_name=co.name if co else None,
                job_title=job_title,
                shift_starts_at=shift_starts,
            )
        )

    stmt_pw = (
        select(ClientOrganization)
        .join(PreferredWorker, PreferredWorker.client_org_id == ClientOrganization.id)
        .where(PreferredWorker.worker_id == wp.id)
        .order_by(ClientOrganization.name)
    )
    r3 = await db.execute(stmt_pw)
    pref_clients = [PreferredClientOut(client_org_id=o.id, name=o.name) for o in r3.scalars().all()]

    return WorkerReputationOut(
        rating_avg=float(wp.rating_avg or 0.0),
        points=int(wp.points or 0),
        tier=wp.tier.value,
        ratings=ratings_out,
        preferred_by_clients=pref_clients,
    )


@router.get("/me/profile", response_model=WorkerProfileOut)
async def get_my_profile(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(status_code=404, detail="Profile not found")
    await db.refresh(user)
    return _profile_out(wp, user)


@router.get("/me/profile/completeness", response_model=ProfileCompletenessDetailOut)
async def get_my_profile_completeness(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(status_code=404, detail="Profile not found")
    await db.refresh(user)
    await _recalc_completeness(db, wp, user)
    types = await _doc_types_for_worker(db, wp.id)
    items = _completeness_checklist(wp, user, types)
    await db.flush()
    return ProfileCompletenessDetailOut(
        profile_completeness_pct=wp.profile_completeness_pct,
        items=items,
    )


@router.put("/me/profile", response_model=WorkerProfileOut)
async def update_my_profile(
    body: WorkerProfileIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
    auth_client: Annotated[SupabaseAuthClient, Depends(get_auth_client)],
):
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        wp = WorkerProfile(user_id=user.id)
        db.add(wp)
        await db.flush()

    old = {"full_name": wp.full_name}
    wp.full_name = body.full_name or wp.full_name
    if body.nric:
        wp.nric_encrypted = encrypt_value(body.nric)
        wp.nric_masked = mask_nric(body.nric)
    if body.bank_account:
        wp.bank_encrypted = encrypt_value(body.bank_account)
    if body.bank_name is not None:
        wp.bank_name = body.bank_name
    if body.email is not None:
        em = body.email.strip().lower() or None
        if em:
            clash = await db.execute(
                select(User).where(func.lower(User.email) == em, User.id != user.id)
            )
            if clash.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Email already in use")
            if em != (user.email or "").lower():
                await sync_auth_email(auth_client, user, em)
        user.email = em
    if body.phone is not None:
        pn = body.phone.strip().replace(" ", "")
        if pn and pn != user.phone:
            clash = await db.execute(select(User).where(User.phone == pn, User.id != user.id))
            if clash.scalar_one_or_none():
                raise HTTPException(status_code=409, detail="Phone already in use")
            user.phone = pn[:32]
    if body.work_pass_not_applicable is not None:
        wp.work_pass_not_applicable = body.work_pass_not_applicable
    wp.languages = body.languages
    wp.job_type_preferences = body.job_type_preferences
    wp.skills = body.skills
    wp.certifications = body.certifications
    wp.availability = body.availability
    if body.location_lat is not None:
        wp.location_lat = body.location_lat
    if body.location_lng is not None:
        wp.location_lng = body.location_lng
    await _recalc_completeness(db, wp, user)
    await db.flush()
    await log_audit(
        db,
        actor_user_id=user.id,
        action="worker_profile_update",
        entity_type="WorkerProfile",
        entity_id=str(wp.id),
        old_values=old,
        new_values={"full_name": wp.full_name},
    )
    await db.refresh(user)
    return _profile_out(wp, user)


@router.post("/me/pdpa-consent")
async def pdpa_consent(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    from datetime import datetime, timezone

    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(404)
    wp.pdpa_consent_at = datetime.now(timezone.utc)
    await _recalc_completeness(db, wp, user)
    await db.flush()
    return {"ok": True}


@router.post("/me/onboarding/complete")
async def onboarding_complete(
    body: OnboardingCompleteIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    from datetime import datetime, timezone

    if not body.terms_accepted:
        raise HTTPException(status_code=400, detail="Terms must be accepted")
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(404)
    now = datetime.now(timezone.utc)
    wp.terms_accepted_at = now
    wp.onboarding_completed_at = now
    if not wp.pdpa_consent_at:
        wp.pdpa_consent_at = now
    await _recalc_completeness(db, wp, user)
    await db.flush()
    return {"ok": True, "profile_completeness_pct": wp.profile_completeness_pct}


@router.post("/me/documents")
async def upload_document(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
    doc_type: str,
    file: UploadFile = File(...),
):
    settings = get_settings()
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(404)
    content = await file.read()
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="File too large")
    ct = (file.content_type or "").split(";")[0].strip().lower()
    if ct not in _ALLOWED_DOC_CT:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {ct}")

    storage = get_storage()
    key = new_object_key(f"documents/worker-{wp.id}", file.filename, ".bin")
    await storage.upload(key, content, ct)
    db.add(Document(worker_id=wp.id, doc_type=doc_type, storage_bucket=storage.bucket, storage_object_path=key))
    out_path = storage_ref(key)

    await db.flush()
    await _recalc_completeness(db, wp, user)
    await db.flush()
    return {"ok": True, "path": out_path}


@router.get("/me/documents")
async def list_my_documents(
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        return []
    r2 = await db.execute(select(Document).where(Document.worker_id == wp.id).order_by(Document.id.desc()))
    docs = r2.scalars().all()
    return [
        {
            "id": d.id,
            "doc_type": d.doc_type,
            "created_at": d.created_at.isoformat() if d.created_at else None,
            "storage_object_path": d.storage_object_path,
            "storage_bucket": d.storage_bucket,
        }
        for d in docs
    ]


@router.get("/me/documents/{doc_id}/signed-url")
async def document_signed_url(
    doc_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.worker))],
):
    r = await db.execute(select(WorkerProfile).where(WorkerProfile.user_id == user.id))
    wp = r.scalar_one_or_none()
    if not wp:
        raise HTTPException(404)
    doc = await db.get(Document, doc_id)
    if not doc or doc.worker_id != wp.id:
        raise HTTPException(404)
    if not doc.storage_object_path:
        raise HTTPException(
            status_code=410,
            detail="Document was stored on a previous server's local disk and is no longer available; please re-upload.",
        )
    url = await get_storage().signed_url(doc.storage_object_path, expires_in=15 * 60)
    return {"url": url, "expires_in": 15 * 60}


@router.get("/directory", response_model=list[WorkerDirectoryEntryOut])
async def list_workers_directory(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    _user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
    q: str | None = None,
    status_filter: str | None = None,
    follow_up_filter: str | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    stmt = select(WorkerProfile)
    if status_filter:
        try:
            stmt = stmt.where(WorkerProfile.status == WorkerStatus(status_filter))
        except ValueError:
            raise HTTPException(400, detail="Invalid status_filter")
    if follow_up_filter:
        fu = follow_up_filter.strip().lower()
        if fu in ("required", "yes", "true", "1"):
            stmt = stmt.where(WorkerProfile.follow_up_required.is_(True))
        elif fu in ("not_required", "no", "false", "0"):
            stmt = stmt.where(WorkerProfile.follow_up_required.is_(False))
        else:
            raise HTTPException(400, detail="Invalid follow_up_filter")
    r = await db.execute(stmt)
    rows = r.scalars().all()
    out = []
    for wp in rows:
        if q and q.lower() not in (wp.full_name or "").lower():
            continue
        u = await db.get(User, wp.user_id)
        if not u:
            continue
        out.append(_directory_row_out(wp, u))
    return out


@router.patch("/{worker_id}/status")
async def update_worker_status(
    worker_id: int,
    new_status: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
):
    wp = await db.get(WorkerProfile, worker_id)
    if not wp:
        raise HTTPException(404)
    try:
        next_status = WorkerStatus(new_status)
    except ValueError:
        raise HTTPException(400, detail="Invalid status")
    if next_status == WorkerStatus.blacklisted and user.role != UserRole.admin:
        raise HTTPException(403, detail="Only admin can blacklist workers")
    wp.status = next_status
    await log_audit(
        db,
        actor_user_id=user.id,
        action="worker_status_change",
        entity_type="WorkerProfile",
        entity_id=str(worker_id),
        new_values={"status": new_status},
    )
    return {"ok": True}


@router.get("/{worker_id}/management", response_model=WorkerManagementOut)
async def get_worker_management(
    response: Response,
    worker_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
):
    del user
    response.headers["Cache-Control"] = "no-store"
    wp = await db.get(WorkerProfile, worker_id)
    if not wp:
        raise HTTPException(404)
    profile_user = await db.get(User, wp.user_id)
    if not profile_user:
        raise HTTPException(404)
    return _worker_management_out(wp, profile_user)


@router.patch("/{worker_id}/management", response_model=WorkerManagementOut)
async def update_worker_management(
    worker_id: int,
    body: WorkerManagementUpdateIn,
    db: Annotated[AsyncSession, Depends(get_db)],
    user: Annotated[User, Depends(require_roles(UserRole.ta, UserRole.admin))],
):
    wp = await db.get(WorkerProfile, worker_id)
    if not wp:
        raise HTTPException(404)
    old = {
        "status": wp.status.value,
        "internal_notes": wp.internal_notes,
        "follow_up_required": bool(wp.follow_up_required),
    }
    if body.status is not None:
        try:
            next_status = WorkerStatus(body.status)
        except ValueError:
            raise HTTPException(400, detail="Invalid status")
        if next_status == WorkerStatus.blacklisted and user.role != UserRole.admin:
            raise HTTPException(403, detail="Only admin can blacklist workers")
        wp.status = next_status
    if body.internal_notes is not None:
        wp.internal_notes = body.internal_notes.strip() or None
    if body.follow_up_required is not None:
        wp.follow_up_required = body.follow_up_required
    await db.flush()
    await log_audit(
        db,
        actor_user_id=user.id,
        action="worker_management_update",
        entity_type="WorkerProfile",
        entity_id=str(worker_id),
        old_values=old,
        new_values={
            "status": wp.status.value,
            "internal_notes": wp.internal_notes,
            "follow_up_required": bool(wp.follow_up_required),
        },
    )
    profile_user = await db.get(User, wp.user_id)
    if not profile_user:
        raise HTTPException(404)
    return _worker_management_out(wp, profile_user)
