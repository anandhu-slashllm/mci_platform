"""End-to-end tests against a running API + local Supabase stack (no fakes).

    npx supabase start
    cd backend && alembic upgrade head && python -m app.scripts.seed_demo
    uvicorn app.main:app --port 8000
    E2E_BASE_URL=http://127.0.0.1:8000 pytest tests_e2e -v

Set E2E_BASE_URL to ".../api" to exercise the /api prefix (Vercel layout). The Supabase values default
to the local CLI stack; override with SUPABASE_URL / SUPABASE_ANON_KEY / MAILPIT_URL / CRON_SECRET.
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")

collect_ignore_glob = [] if BASE_URL else ["test_*.py"]


@pytest.fixture(scope="session")
def api() -> httpx.Client:
    with httpx.Client(base_url=BASE_URL, timeout=60) as c:
        yield c


@pytest.fixture(scope="session")
def state() -> dict:
    """Values handed from one workflow step to the next."""
    return {}
