"""Vercel Python function: the whole FastAPI backend, served under /api/*.

vercel.json rewrites /api/(.*) here; app.main strips the /api prefix before routing.
Serverless defaults: no in-process background loops (Vercel Cron calls /api/internal/cron/*)
and a pooler-safe database engine. Both can still be overridden by project env vars.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

os.environ.setdefault("BACKGROUND_MODE", "cron")
os.environ.setdefault("DB_POOL_MODE", "serverless")

from app.main import app  # noqa: E402,F401
