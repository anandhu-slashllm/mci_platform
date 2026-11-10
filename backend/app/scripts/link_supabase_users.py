"""One-off migration: create Supabase Auth users for existing app users.

Run after `alembic upgrade head` on a database that already has users:

    python -m app.scripts.link_supabase_users            # link everyone not yet linked
    python -m app.scripts.link_supabase_users --dry-run  # show what would happen

Existing bcrypt hashes in users.hashed_password are imported, so people keep their passwords.
Users with neither an email nor a username are skipped (they had phone-OTP only, which is not
offered any more); give them an email first.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import inspect, select, text

from app.auth.supabase_auth import get_auth_client
from app.db.session import AsyncSessionLocal
from app.models import User
from app.services.auth_accounts import auth_email_for, ensure_auth_user


async def _legacy_hashes(db) -> dict[int, str]:
    has_col = await db.run_sync(
        lambda s: any(c["name"] == "hashed_password" for c in inspect(s.connection()).get_columns("users"))
    )
    if not has_col:
        return {}
    rows = await db.execute(text("SELECT id, hashed_password FROM users WHERE hashed_password IS NOT NULL"))
    return {int(r[0]): str(r[1]) for r in rows}


async def main(dry_run: bool) -> None:
    client = get_auth_client()
    linked = skipped = 0
    async with AsyncSessionLocal() as db:
        hashes = await _legacy_hashes(db)
        users = (await db.execute(select(User).where(User.supabase_user_id.is_(None)).order_by(User.id))).scalars().all()
        for user in users:
            email = auth_email_for(user)
            if not email:
                print(f"skip user {user.id} ({user.role.value}): no email or username")
                skipped += 1
                continue
            print(f"{'would link' if dry_run else 'link'} user {user.id} ({user.role.value}) -> {email}"
                  f"{' [password imported]' if user.id in hashes else ''}")
            if not dry_run:
                await ensure_auth_user(db, client, user, password_hash=hashes.get(user.id))
            linked += 1
        if not dry_run:
            await db.commit()
    print(f"done: linked={linked} skipped={skipped} dry_run={dry_run}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(parser.parse_args().dry_run))
