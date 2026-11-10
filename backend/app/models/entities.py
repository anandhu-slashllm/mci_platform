import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    LargeBinary,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class UserRole(str, enum.Enum):
    worker = "worker"
    ta = "ta"
    client = "client"
    admin = "admin"


class WorkerStatus(str, enum.Enum):
    active = "active"
    inactive = "inactive"
    blacklisted = "blacklisted"


class WorkerTier(str, enum.Enum):
    probation = "probation"
    standard = "standard"
    preferred = "preferred"
    elite = "elite"


class ClientUserRole(str, enum.Enum):
    owner = "owner"
    member = "member"


class JobOrderStatus(str, enum.Enum):
    draft = "draft"
    open = "open"
    partially_filled = "partially_filled"
    fully_filled = "fully_filled"
    completed = "completed"
    cancelled = "cancelled"


class AutomaticDispatchMode(str, enum.Enum):
    fcfs = "fcfs"
    performance_priority = "performance_priority"
    preferred_priority = "preferred_priority"


class PayModel(str, enum.Enum):
    hourly = "hourly"
    per_shift = "per_shift"
    mixed = "mixed"


class AssignmentStatus(str, enum.Enum):
    invited = "invited"
    applied = "applied"
    confirmed = "confirmed"
    waitlisted = "waitlisted"
    rejected = "rejected"
    cancelled = "cancelled"
    no_show = "no_show"
    completed = "completed"


class ShiftOfferStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    expired = "expired"


class AttendanceStatus(str, enum.Enum):
    unverified = "unverified"
    present = "present"
    absent = "absent"
    late = "late"
    flagged = "flagged"


class TimesheetStatus(str, enum.Enum):
    pending = "pending"
    disputed = "disputed"
    employer_endorsed = "employer_endorsed"
    ta_verified = "ta_verified"
    payment_released = "payment_released"
    client_payroll_released = "client_payroll_released"


class TaClientContractStatus(str, enum.Enum):
    pending_ta = "pending_ta"
    active = "active"
    declined = "declined"
    terminated = "terminated"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    paid = "paid"


class WorkerReportStatus(str, enum.Enum):
    open = "open"
    under_review = "under_review"
    resolved = "resolved"


class DispatchTaskStatus(str, enum.Enum):
    pending = "pending"
    completed = "completed"
    cancelled = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Supabase Auth user id (auth.users.id, UUID as text). Sessions are resolved through this column.
    supabase_user_id: Mapped[Optional[str]] = mapped_column(String(36), unique=True, index=True, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), index=True)
    locale: Mapped[str] = mapped_column(String(10), default="en")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    worker_profile: Mapped[Optional["WorkerProfile"]] = relationship(back_populates="user", uselist=False)
    client_profile: Mapped[Optional["ClientProfile"]] = relationship(back_populates="user", uselist=False)
    client_org_id: Mapped[Optional[int]] = mapped_column(ForeignKey("client_organizations.id"), nullable=True)
    client_org: Mapped[Optional["ClientOrganization"]] = relationship(back_populates="users")


class WorkerProfile(Base):
    __tablename__ = "worker_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    full_name: Mapped[str] = mapped_column(String(255), default="")
    nric_masked: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    nric_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bank_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    bank_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    work_pass_not_applicable: Mapped[bool] = mapped_column(Boolean, default=False)
    languages: Mapped[list] = mapped_column(JSON, default=list)
    job_type_preferences: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    certifications: Mapped[list] = mapped_column(JSON, default=list)
    availability: Mapped[dict] = mapped_column(JSON, default=dict)
    location_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rating_avg: Mapped[float] = mapped_column(Float, default=0.0)
    completion_rate: Mapped[float] = mapped_column(Float, default=1.0)
    tier: Mapped[WorkerTier] = mapped_column(Enum(WorkerTier), default=WorkerTier.standard)
    status: Mapped[WorkerStatus] = mapped_column(Enum(WorkerStatus), default=WorkerStatus.active)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    follow_up_required: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    no_show_count: Mapped[int] = mapped_column(Integer, default=0)
    late_cancel_count: Mapped[int] = mapped_column(Integer, default=0)
    profile_completeness_pct: Mapped[int] = mapped_column(Integer, default=0)
    pdpa_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="worker_profile")
    documents: Mapped[list["Document"]] = relationship(back_populates="worker")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    doc_type: Mapped[str] = mapped_column(String(64))
    file_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    storage_bucket: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    storage_object_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    worker: Mapped["WorkerProfile"] = relationship(back_populates="documents")


class ClientOrganization(Base):
    __tablename__ = "client_organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    users: Mapped[list["User"]] = relationship(back_populates="client_org")
    job_orders: Mapped[list["JobOrder"]] = relationship(back_populates="client_org")
    preferred_workers: Mapped[list["PreferredWorker"]] = relationship(back_populates="client_org")
    ta_contracts: Mapped[list["TaClientContract"]] = relationship(back_populates="client_org")


class ClientProfile(Base):
    __tablename__ = "client_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    company_name: Mapped[str] = mapped_column(String(255), default="")
    contact_name: Mapped[str] = mapped_column(String(255), default="")
    contact_email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    address_line: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    location_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    location_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    billing_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    profile_completeness_pct: Mapped[int] = mapped_column(Integer, default=0)
    pdpa_consent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    terms_accepted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    onboarding_completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    user: Mapped["User"] = relationship(back_populates="client_profile")


class TaClientContract(Base):
    """At most one row with status=active per client_org_id (enforced via partial unique index)."""

    __tablename__ = "ta_client_contracts"
    __table_args__ = (
        Index(
            "uq_ta_client_contract_one_active_per_org",
            "client_org_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_org_id: Mapped[int] = mapped_column(ForeignKey("client_organizations.id"), index=True)
    ta_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[TaClientContractStatus] = mapped_column(
        Enum(TaClientContractStatus, name="taclientcontractstatus"),
        default=TaClientContractStatus.pending_ta,
    )
    requested_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    client_org: Mapped["ClientOrganization"] = relationship(back_populates="ta_contracts")


class PreferredWorker(Base):
    __tablename__ = "preferred_workers"
    __table_args__ = (UniqueConstraint("client_org_id", "worker_id", name="uq_pref_client_worker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_org_id: Mapped[int] = mapped_column(ForeignKey("client_organizations.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))

    client_org: Mapped["ClientOrganization"] = relationship(back_populates="preferred_workers")


class JobOrder(Base):
    __tablename__ = "job_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_org_id: Mapped[int] = mapped_column(ForeignKey("client_organizations.id"))
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(255))
    job_type: Mapped[str] = mapped_column(String(64))
    location_address: Mapped[str] = mapped_column(String(512))
    location_lat: Mapped[float] = mapped_column(Float)
    location_lng: Mapped[float] = mapped_column(Float)
    workers_required_total: Mapped[int] = mapped_column(Integer, default=1)
    hourly_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    per_shift_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pay_model: Mapped[PayModel] = mapped_column(Enum(PayModel), default=PayModel.hourly)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)
    required_certifications: Mapped[list] = mapped_column(JSON, default=list)
    uniform_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    special_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    match_weight_overrides: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    geofence_radius_m: Mapped[int] = mapped_column(Integer, default=200)
    acceptance_window_minutes: Mapped[int] = mapped_column(Integer, default=120)
    automatic_dispatch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    automatic_dispatch_mode: Mapped[AutomaticDispatchMode] = mapped_column(
        Enum(AutomaticDispatchMode),
        default=AutomaticDispatchMode.fcfs,
    )
    automatic_dispatch_min_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    automatic_dispatch_top_percent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    automatic_dispatch_priority_window_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    no_show_grace_minutes: Mapped[int] = mapped_column(Integer, default=15)
    overtime_multiplier: Mapped[float] = mapped_column(Float, default=1.5)
    status: Mapped[JobOrderStatus] = mapped_column(Enum(JobOrderStatus), default=JobOrderStatus.draft)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    client_org: Mapped["ClientOrganization"] = relationship(back_populates="job_orders")
    shifts: Mapped[list["Shift"]] = relationship(back_populates="job_order", cascade="all, delete-orphan")


class Shift(Base):
    __tablename__ = "shifts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_order_id: Mapped[int] = mapped_column(ForeignKey("job_orders.id"))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    workers_required: Mapped[int] = mapped_column(Integer, default=1)

    job_order: Mapped["JobOrder"] = relationship(back_populates="shifts")
    assignments: Mapped[list["ShiftAssignment"]] = relationship(back_populates="shift", cascade="all, delete-orphan")
    offers: Mapped[list["ShiftOffer"]] = relationship(back_populates="shift", cascade="all, delete-orphan")


class ShiftAssignment(Base):
    __tablename__ = "shift_assignments"
    __table_args__ = (UniqueConstraint("shift_id", "worker_id", name="uq_shift_worker"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    status: Mapped[AssignmentStatus] = mapped_column(Enum(AssignmentStatus), default=AssignmentStatus.invited)
    waitlist_rank: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    shift: Mapped["Shift"] = relationship(back_populates="assignments")


class ShiftOffer(Base):
    __tablename__ = "shift_offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    status: Mapped[ShiftOfferStatus] = mapped_column(Enum(ShiftOfferStatus), default=ShiftOfferStatus.pending)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    shift: Mapped["Shift"] = relationship(back_populates="offers")


class ShiftDispatchTask(Base):
    __tablename__ = "shift_dispatch_tasks"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_shift_dispatch_tasks_dedupe_key"),
        Index("ix_shift_dispatch_tasks_run_after", "run_after"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"), index=True)
    job_order_id: Mapped[int] = mapped_column(ForeignKey("job_orders.id"), index=True)
    phase: Mapped[str] = mapped_column(String(32), default="performance_phase_2")
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[DispatchTaskStatus] = mapped_column(
        Enum(DispatchTaskStatus),
        default=DispatchTaskStatus.pending,
        index=True,
    )
    dedupe_key: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketplaceShiftDismissal(Base):
    """Worker hid a marketplace shift listing (no offer required)."""

    __tablename__ = "marketplace_shift_dismissals"
    __table_args__ = (UniqueConstraint("worker_profile_id", "shift_id", name="uq_marketplace_dismiss_worker_shift"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_profile_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WorkerShiftRecommendation(Base):
    """Precomputed worker recommendations for hybrid refresh serving."""

    __tablename__ = "worker_shift_recommendations"
    __table_args__ = (
        UniqueConstraint("worker_profile_id", "shift_id", name="uq_worker_shift_recommendation_worker_shift"),
        Index("ix_worker_shift_recommendations_worker_profile_id", "worker_profile_id"),
        Index("ix_worker_shift_recommendations_shift_id", "shift_id"),
        Index("ix_worker_shift_recommendations_score_total", "score_total"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_profile_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    score_total: Mapped[float] = mapped_column(Float, default=0.0)
    urgency_score: Mapped[float] = mapped_column(Float, default=0.0)
    return_opportunity: Mapped[bool] = mapped_column(Boolean, default=False)
    score_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    reason_flags: Mapped[list] = mapped_column(JSON, default=list)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    source: Mapped[str] = mapped_column(String(32), default="daily")


class AttendanceRecord(Base):
    __tablename__ = "attendance_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_assignment_id: Mapped[int] = mapped_column(ForeignKey("shift_assignments.id"), unique=True)
    clock_in_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    clock_in_photo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    clock_in_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clock_in_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clock_in_geofence_valid: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    clock_out_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    clock_out_photo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    clock_out_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    clock_out_lng: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    total_hours: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[AttendanceStatus] = mapped_column(Enum(AttendanceStatus), default=AttendanceStatus.unverified)
    absent_after_grace_ta_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    absence_strike_recorded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TimesheetEntry(Base):
    __tablename__ = "timesheet_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_order_id: Mapped[int] = mapped_column(ForeignKey("job_orders.id"))
    shift_id: Mapped[int] = mapped_column(ForeignKey("shifts.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    shift_assignment_id: Mapped[int] = mapped_column(ForeignKey("shift_assignments.id"))
    attendance_record_id: Mapped[Optional[int]] = mapped_column(ForeignKey("attendance_records.id"), nullable=True)
    scheduled_hours: Mapped[float] = mapped_column(Float, default=0.0)
    actual_hours: Mapped[float] = mapped_column(Float, default=0.0)
    overtime_hours: Mapped[float] = mapped_column(Float, default=0.0)
    hourly_rate_snapshot: Mapped[float] = mapped_column(Float, default=0.0)
    per_shift_rate_snapshot: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    gross_pay: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[TimesheetStatus] = mapped_column(Enum(TimesheetStatus), default=TimesheetStatus.pending)
    dispute_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    dispute_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    employer_endorsed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    employer_endorsed_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    ta_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ta_verified_by_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)


class PaymentRecord(Base):
    __tablename__ = "payment_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    timesheet_entry_ids: Mapped[list] = mapped_column(JSON, default=list)
    total_amount: Mapped[float] = mapped_column(Float, default=0.0)
    payment_status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending)
    payment_instructions_file_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("shift_assignment_id", "rated_by_user_id", name="uq_rating_shift_rater"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_assignment_id: Mapped[int] = mapped_column(ForeignKey("shift_assignments.id"))
    worker_id: Mapped[int] = mapped_column(ForeignKey("worker_profiles.id"))
    rated_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    stars: Mapped[int] = mapped_column(Integer)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(32), default="in_app")
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    old_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    new_values: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WorkerShiftReport(Base):
    __tablename__ = "worker_shift_reports"
    __table_args__ = (
        UniqueConstraint("shift_assignment_id", "worker_user_id", name="uq_worker_shift_report_assignment_worker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    shift_assignment_id: Mapped[int] = mapped_column(ForeignKey("shift_assignments.id"), index=True)
    worker_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[WorkerReportStatus] = mapped_column(Enum(WorkerReportStatus), default=WorkerReportStatus.open, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class JobTypeWeight(Base):
    __tablename__ = "job_type_weights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(64), unique=True)
    weight_proximity: Mapped[float] = mapped_column(Float, default=0.3)
    weight_rating: Mapped[float] = mapped_column(Float, default=0.25)
    weight_job_type: Mapped[float] = mapped_column(Float, default=0.2)
    weight_completion: Mapped[float] = mapped_column(Float, default=0.15)
    weight_tier: Mapped[float] = mapped_column(Float, default=0.1)


class UserChatIdentity(Base):
    """X25519 public key for job-order chat key wrapping (E2EE). Private key stays on client."""

    __tablename__ = "user_chat_identities"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    x25519_public_key: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class JobOrderChatKeyWrapper(Base):
    """Room symmetric key, sealed for one participant (opaque blob from client crypto)."""

    __tablename__ = "job_order_chat_key_wrappers"
    __table_args__ = (
        UniqueConstraint("job_order_id", "user_id", name="uq_job_order_chat_wrap_user"),
        Index("ix_job_order_chat_wrappers_job", "job_order_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_order_id: Mapped[int] = mapped_column(ForeignKey("job_orders.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class JobOrderChatMessage(Base):
    """Opaque ciphertext only; server never decrypts."""

    __tablename__ = "job_order_chat_messages"
    __table_args__ = (Index("ix_job_order_chat_msg_job_created", "job_order_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_order_id: Mapped[int] = mapped_column(ForeignKey("job_orders.id"), index=True)
    sender_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    iv: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class ShiftReminderSent(Base):
    """Idempotency marker for shift reminders (replaces the Redis SET NX key)."""

    __tablename__ = "shift_reminders_sent"
    __table_args__ = (UniqueConstraint("assignment_id", "kind", name="uq_shift_reminder_assignment_kind"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey("shift_assignments.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class RecommendationRefreshQueue(Base):
    """Durable queue of worker profiles whose recommendations need recomputing.

    Replaces an in-process set so that work enqueued by one serverless invocation is
    picked up by the cron invocation.
    """

    __tablename__ = "recommendation_refresh_queue"

    worker_profile_id: Mapped[int] = mapped_column(
        ForeignKey("worker_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
