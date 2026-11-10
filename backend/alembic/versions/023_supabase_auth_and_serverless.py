"""Supabase Auth link + serverless-safe background state.

- users.supabase_user_id: maps an app user to auth.users.id (sessions resolve through it).
- shift_reminders_sent: reminder idempotency (was Redis SET NX / in-process dict).
- recommendation_refresh_queue: durable queue (was an in-process set).
- On Supabase: enable RLS on every public table and revoke anon/authenticated access, so the
  public anon key cannot read app data through PostgREST/GraphQL. The backend connects as the
  table owner (postgres) and is unaffected.

users.hashed_password is intentionally NOT dropped here: app/scripts/link_supabase_users.py
imports those bcrypt hashes into Supabase Auth so existing users keep their passwords. Drop the
column in a later migration once every environment has been linked.

Revision ID: 023
Revises: 022
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _lock_down_public_schema() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    has_supabase_roles = bind.execute(
        sa.text("SELECT count(*) FROM pg_roles WHERE rolname IN ('anon', 'authenticated')")
    ).scalar()
    op.execute(
        """
        DO $$
        DECLARE r record;
        BEGIN
          FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', r.tablename);
          END LOOP;
        END $$;
        """
    )
    if has_supabase_roles == 2:
        op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated")
        op.execute("REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated")
        op.execute("REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM anon, authenticated")
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
            "REVOKE ALL ON TABLES FROM anon, authenticated"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
            "REVOKE ALL ON SEQUENCES FROM anon, authenticated"
        )
        op.execute(
            "ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public "
            "REVOKE ALL ON FUNCTIONS FROM anon, authenticated"
        )


def upgrade() -> None:
    # 001 builds the schema from current model metadata, so on a fresh database these objects
    # already exist: every step checks first (same convention as 019-022).
    insp = sa.inspect(op.get_bind())
    tables = set(insp.get_table_names())

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "supabase_user_id" not in user_cols:
        op.add_column("users", sa.Column("supabase_user_id", sa.String(length=36), nullable=True))
    user_indexes = {i["name"] for i in insp.get_indexes("users")}
    if "ix_users_supabase_user_id" not in user_indexes:
        op.create_index("ix_users_supabase_user_id", "users", ["supabase_user_id"], unique=True)

    if "shift_reminders_sent" not in tables:
        op.create_table(
            "shift_reminders_sent",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "assignment_id",
                sa.Integer(),
                sa.ForeignKey("shift_assignments.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.String(length=16), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("assignment_id", "kind", name="uq_shift_reminder_assignment_kind"),
        )
        op.create_index("ix_shift_reminders_sent_assignment_id", "shift_reminders_sent", ["assignment_id"])

    if "recommendation_refresh_queue" not in tables:
        op.create_table(
            "recommendation_refresh_queue",
            sa.Column(
                "worker_profile_id",
                sa.Integer(),
                sa.ForeignKey("worker_profiles.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        )

    _lock_down_public_schema()


def downgrade() -> None:
    op.drop_table("recommendation_refresh_queue")
    op.drop_index("ix_shift_reminders_sent_assignment_id", table_name="shift_reminders_sent")
    op.drop_table("shift_reminders_sent")
    op.drop_index("ix_users_supabase_user_id", table_name="users")
    op.drop_column("users", "supabase_user_id")
