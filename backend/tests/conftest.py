"""Shared test setup.

- Users authenticate with Supabase-shaped JWTs signed by TEST_JWT_SECRET (see auth_helpers.py).
- Supabase Auth and Storage are replaced by in-memory fakes for every test.
- Users inserted in tests get a deterministic ``supabase_user_id`` so ``create_access_token(user_id)``
  works; modules that test the real linking flow opt out with ``pytestmark = pytest.mark.no_auto_auth_link``.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from auth_helpers import TEST_JWT_SECRET, auth_uid_for_user  # noqa: E402

# Force (not setdefault): backend/.env may point at a live local Supabase.
os.environ["SUPABASE_JWT_SECRET"] = TEST_JWT_SECRET
os.environ["SUPABASE_DEV_MAILBOX_URL"] = ""
os.environ["SUPABASE_URL"] = "http://supabase.invalid"
os.environ["BACKGROUND_MODE"] = "loop"
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from sqlalchemy import event, update  # noqa: E402
from sqlalchemy.orm.attributes import set_committed_value  # noqa: E402

from app.auth.supabase_auth import set_auth_client  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.models import User  # noqa: E402
from app.services.storage import InMemoryStorage, set_storage  # noqa: E402
from supabase_fake import FakeSupabaseAuth  # noqa: E402

get_settings.cache_clear()

_auto_link = {"enabled": True}


@event.listens_for(User, "after_insert")
def _assign_test_auth_uid(mapper, connection, target):  # noqa: ARG001
    if not _auto_link["enabled"] or target.supabase_user_id:
        return
    uid = auth_uid_for_user(target.id)
    connection.execute(update(User.__table__).where(User.__table__.c.id == target.id).values(supabase_user_id=uid))
    set_committed_value(target, "supabase_user_id", uid)


def pytest_configure(config):
    config.addinivalue_line("markers", "no_auto_auth_link: do not auto-assign supabase_user_id to inserted users")


@pytest.fixture(autouse=True)
def fake_auth(request):
    _auto_link["enabled"] = request.node.get_closest_marker("no_auto_auth_link") is None
    fake = FakeSupabaseAuth()
    set_auth_client(fake)
    yield fake
    set_auth_client(None)
    _auto_link["enabled"] = True


@pytest.fixture(autouse=True)
def memory_storage():
    storage = InMemoryStorage()
    set_storage(storage)
    yield storage
    set_storage(None)
