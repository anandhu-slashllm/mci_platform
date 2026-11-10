"""Every major workflow, end to end, on real Supabase Auth / Storage / Postgres.

Steps run in file order and share ``state``. Each run uses fresh, unique emails so it can be repeated
against the same database.
"""

from __future__ import annotations

import base64
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from jose import jwt as jose_jwt

SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get(
    "SUPABASE_ANON_KEY",
    # Well-known key of the local Supabase CLI stack (not a secret).
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZS1kZW1vIiwicm9sZSI6ImFub24iLCJleHAiOjE5ODM4MTI5OTZ9."
    "CRXP1A7WOeoJeXxjNni43kdQwgnWNReilDMblYTn_I0",
)
MAILPIT_URL = os.environ.get("MAILPIT_URL", "http://127.0.0.1:54324").rstrip("/")
CRON_SECRET = os.environ.get("CRON_SECRET", "local-cron-secret")

RUN = uuid.uuid4().hex[:8]
WORKER_EMAIL = f"e2e.worker.{RUN}@example.com"
CLIENT_EMAIL = f"e2e.client.{RUN}@example.com"
JOB_LAT, JOB_LNG = 1.3000, 103.8000

DEMO_ACCOUNTS = {
    "worker": ("demo_worker", "worker"),
    "worker_2": ("demo_worker_2", "worker"),
    "worker_8": ("demo_worker_8", "worker"),
    "ta": ("demo_ta", "ta"),
    "ta_2": ("demo_ta_2", "ta"),
    "client": ("demo_client", "client"),
    "client_2": ("demo_client_2", "client"),
    "admin": ("demo_admin", "admin"),
}

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def ok(r: httpx.Response, *codes: int) -> dict | list | None:
    assert r.status_code in (codes or (200,)), f"{r.request.method} {r.request.url} -> {r.status_code}: {r.text[:500]}"
    if r.status_code == 204 or not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def mailpit_code(email: str, timeout: float = 15.0) -> str:
    """Read the newest OTP Supabase sent to ``email`` from Mailpit (independent of the API's dev echo)."""
    deadline = time.time() + timeout
    with httpx.Client(base_url=MAILPIT_URL, timeout=10) as mp:
        while time.time() < deadline:
            msgs = mp.get("/api/v1/search", params={"query": f"to:{email}", "limit": 1}).json().get("messages") or []
            if msgs:
                body = mp.get(f"/api/v1/message/{msgs[0]['ID']}").json()
                m = re.search(r"\b(\d{6})\b", body.get("Text") or "") or re.search(r"\b(\d{6})\b", body.get("HTML") or "")
                if m:
                    return m.group(1)
            time.sleep(0.4)
    raise AssertionError(f"no OTP email for {email} in Mailpit")


def request_otp(api: httpx.Client, path: str, body: dict) -> dict:
    """Supabase enforces a minimum interval between OTP emails to one address (1s locally, 60s hosted
    by default); the API surfaces it as 429. Wait and retry like a user pressing "resend"."""
    for _ in range(10):
        r = api.post(path, json=body)
        if r.status_code != 429:
            return ok(r)
        assert "Too many attempts" in r.text
        time.sleep(0.7)
    raise AssertionError("OTP request still rate limited")


def login(api: httpx.Client, username: str, password: str) -> dict:
    return ok(api.post("/auth/login", json={"username": username, "password": password}))


# ================================================================================================
# 1. Authentication (Supabase Auth)
# ================================================================================================


@pytest.mark.parametrize("username", list(DEMO_ACCOUNTS))
def test_01_password_login_every_demo_role(api, state, username):
    password, role = DEMO_ACCOUNTS[username]
    s = login(api, username, password)
    assert s["role"] == role
    claims = jose_jwt.get_unverified_claims(s["access_token"])
    assert claims["aud"] == "authenticated" and claims["iss"].endswith("/auth/v1")
    assert claims["app_metadata"]["mci_role"] == role
    assert claims["app_metadata"]["mci_user_id"] == s["user_id"]
    ok(api.get("/notifications", headers=bearer(s["access_token"])))
    state.setdefault("sessions", {})[username] = s


def test_02_bad_password_and_unknown_user_rejected(api):
    assert api.post("/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert api.post("/auth/login", json={"username": "ghost", "password": "x"}).status_code == 401
    assert api.get("/notifications").status_code == 401
    assert api.get("/notifications", headers=bearer("garbage.token.here")).status_code == 401


def test_03_refresh_rotates_and_logout_revokes(api):
    s = login(api, "ta_2", "demo_ta_2")
    s2 = ok(api.post("/auth/refresh", json={"refresh_token": s["refresh_token"]}))
    assert s2["refresh_token"] != s["refresh_token"]
    ok(api.get("/notifications", headers=bearer(s2["access_token"])))
    ok(api.post("/auth/logout", headers=bearer(s2["access_token"])), 204)
    # The session's refresh token is revoked in Supabase.
    assert api.post("/auth/refresh", json={"refresh_token": s2["refresh_token"]}).status_code == 401


def test_04_worker_signup_by_email_otp(api, state):
    r = ok(api.post("/auth/worker/email-otp/request", json={"email": WORKER_EMAIL, "locale": "en"}))
    code = mailpit_code(WORKER_EMAIL)
    if r["delivery"] == "dev":
        assert r["dev_code"] == code  # local dev echo matches what Supabase mailed
    # A wrong code is rejected by Supabase.
    bad = "000000" if code != "000000" else "111111"
    assert api.post("/auth/worker/email-otp/verify", json={"email": WORKER_EMAIL, "code": bad}).status_code == 400

    s = ok(api.post("/auth/worker/email-otp/verify", json={"email": WORKER_EMAIL, "code": code, "locale": "en"}))
    assert s["role"] == "worker"
    state["worker"] = s
    # The code is single use.
    assert api.post("/auth/worker/email-otp/verify", json={"email": WORKER_EMAIL, "code": code}).status_code == 400


def test_05_client_signup_by_email_otp(api, state):
    ok(api.post("/auth/client/email-otp/request", json={"email": CLIENT_EMAIL}))
    s = ok(api.post("/auth/client/email-otp/verify", json={"email": CLIENT_EMAIL, "code": mailpit_code(CLIENT_EMAIL)}))
    assert s["role"] == "client"
    state["client"] = s


def test_06_existing_user_email_otp_login_maps_to_same_account(api, state):
    email = "lucy.tan@mci-demoapp.com"  # seeded worker "worker"
    ok(api.post("/auth/worker/email-otp/request", json={"email": email, "login_only": True}))
    s = ok(api.post("/auth/worker/email-otp/verify", json={"email": email, "code": mailpit_code(email), "login_only": True}))
    assert s["user_id"] == state["sessions"]["worker"]["user_id"]
    # Persona mismatch and unknown-email rules still apply.
    assert api.post("/auth/client/email-otp/request", json={"email": email, "login_only": True}).status_code == 404
    unknown = f"nobody.{RUN}@example.com"
    assert api.post("/auth/worker/email-otp/request", json={"email": unknown, "login_only": True}).status_code == 404


def test_07_app_tables_not_reachable_with_public_anon_key():
    h = {"apikey": SUPABASE_ANON_KEY, "Authorization": f"Bearer {SUPABASE_ANON_KEY}"}
    for table in ("users", "worker_profiles", "payment_records"):
        r = httpx.get(f"{SUPABASE_URL}/rest/v1/{table}?select=*&limit=1", headers=h, timeout=10)
        assert r.status_code >= 400 or r.json() == [], f"{table} readable via PostgREST: {r.status_code} {r.text[:200]}"


# ================================================================================================
# 2. Onboarding + documents (Supabase Storage)
# ================================================================================================


def test_10_worker_onboarding_profile_and_document(api, state):
    h = bearer(state["worker"]["access_token"])
    ok(
        api.put(
            "/workers/me/profile",
            headers=h,
            json={
                "full_name": f"E2E Worker {RUN}",
                "nric": "S1234567D",
                "bank_account": "123-456-789",
                "bank_name": "DBS",
                "languages": ["en"],
                "job_type_preferences": ["Warehouse Worker"],
                "skills": ["Data Entry"],
                "availability": {"always": True},
                "location_lat": JOB_LAT,
                "location_lng": JOB_LNG,
                "work_pass_not_applicable": True,
            },
        )
    )
    ok(api.post("/workers/me/pdpa-consent", headers=h))
    up = ok(
        api.post(
            "/workers/me/documents?doc_type=nric_front",
            headers=h,
            files={"file": ("nric.png", PNG, "image/png")},
        )
    )
    assert up["path"].startswith("storage:documents/")
    ok(api.post("/workers/me/onboarding/complete", headers=h, json={"terms_accepted": True}))

    docs = ok(api.get("/workers/me/documents", headers=h))
    signed = ok(api.get(f"/workers/me/documents/{docs[0]['id']}/signed-url", headers=h))
    blob = httpx.get(signed["url"], timeout=20)
    assert blob.status_code == 200 and blob.content == PNG, "signed Storage URL must return the uploaded bytes"
    # Not publicly listed: the bucket is private.
    assert httpx.get(f"{SUPABASE_URL}/storage/v1/object/public/mci-uploads/{docs[0]['storage_object_path']}").status_code >= 400

    # Another worker cannot fetch it.
    other = bearer(state["sessions"]["worker_2"]["access_token"])
    assert api.get(f"/workers/me/documents/{docs[0]['id']}/signed-url", headers=other).status_code == 404


def test_11_profile_email_change_syncs_supabase_login(api, state):
    h = bearer(state["worker"]["access_token"])
    new_email = f"e2e.worker.changed.{RUN}@example.com"
    prof = ok(api.get("/workers/me/profile", headers=h))
    ok(api.put("/workers/me/profile", headers=h, json={**_profile_in(prof), "email": new_email}))
    ok(api.post("/auth/worker/email-otp/request", json={"email": new_email, "login_only": True}))
    s = ok(api.post("/auth/worker/email-otp/verify", json={"email": new_email, "code": mailpit_code(new_email), "login_only": True}))
    assert s["user_id"] == state["worker"]["user_id"]
    state["worker"] = s


def _profile_in(prof: dict) -> dict:
    keys = ("full_name", "languages", "job_type_preferences", "skills", "certifications", "availability",
            "location_lat", "location_lng", "bank_name")
    return {k: prof.get(k) for k in keys if prof.get(k) is not None}


def test_12_client_onboarding(api, state):
    h = bearer(state["client"]["access_token"])
    ok(api.put("/client/me/profile", headers=h, json={"company_name": f"E2E Co {RUN}", "contact_name": "Pat", "contact_email": CLIENT_EMAIL}))
    ok(api.post("/client/me/pdpa-consent", headers=h))
    ok(api.post("/client/me/onboarding/complete", headers=h, json={"terms_accepted": True}))


# ================================================================================================
# 3. Client <-> TA contract
# ================================================================================================


def test_20_client_requests_ta_and_ta_accepts(api, state):
    ch = bearer(state["client"]["access_token"])
    ta = state["sessions"]["ta"]
    tas = ok(api.get("/ta-contracts/directory/talent-agents", headers=ch))
    assert any(t.get("user_id", t.get("id")) == ta["user_id"] for t in tas)
    req = ok(api.post("/ta-contracts/client/request", headers=ch, json={"ta_user_id": ta["user_id"], "message": "e2e"}))
    contract_id = req.get("id") or req.get("contract_id")

    th = bearer(ta["access_token"])
    incoming = ok(api.get("/ta-contracts/ta/incoming", headers=th))
    if contract_id is None:
        contract_id = next(c["id"] for c in incoming if c.get("client_org_id") == _client_org(api, ch))
    ok(api.post(f"/ta-contracts/{contract_id}/accept", headers=th))
    ctx = ok(api.get("/ta-contracts/client/context", headers=ch))
    assert "active" in str(ctx).lower()


def _client_org(api, h) -> int | None:
    ctx = api.get("/ta-contracts/client/context", headers=h).json()
    return ctx.get("client_org_id") if isinstance(ctx, dict) else None


# ================================================================================================
# 4. Job order -> marketplace -> attendance -> timesheet -> payroll -> exports
# ================================================================================================


def test_30_client_creates_and_publishes_job(api, state):
    h = bearer(state["client"]["access_token"])
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    job = ok(
        api.post(
            "/job-orders",
            headers=h,
            json={
                "title": f"E2E packing {RUN}",
                "job_type": "Warehouse Worker",
                "location_address": "1 E2E Road, Singapore",
                "location_lat": JOB_LAT,
                "location_lng": JOB_LNG,
                "workers_required_total": 1,
                "hourly_rate": 15.0,
                "pay_model": "hourly",
                "geofence_radius_m": 500,
                "automatic_dispatch_enabled": True,
                "automatic_dispatch_mode": "fcfs",
                "shifts": [{"starts_at": start.isoformat(), "ends_at": (start + timedelta(hours=4)).isoformat(), "workers_required": 1}],
            },
        ),
        200,
        201,
    )
    state["job_id"] = job["id"]
    pub = ok(api.post(f"/job-orders/{job['id']}/publish", headers=h))
    assert pub["status"] in ("open", "filled", "partially_filled")
    detail = ok(api.get(f"/job-orders/{job['id']}", headers=h))
    state["shift_id"] = detail["shifts"][0]["id"]


def test_31_worker_finds_shift_and_is_confirmed(api, state):
    h = bearer(state["worker"]["access_token"])
    shifts = ok(api.get("/worker/marketplace/shifts", headers=h))
    ids = [s.get("shift_id", s.get("id")) for s in shifts]
    assert state["shift_id"] in ids, f"shift {state['shift_id']} not in marketplace {ids[:20]}"
    ok(api.post(f"/worker/marketplace/shifts/{state['shift_id']}/apply", headers=h))
    mine = ok(api.get("/worker/my-shifts", headers=h))
    rows = mine if isinstance(mine, list) else mine.get("items", [])
    a = next(r for r in rows if (r.get("shift") or {}).get("id") == state["shift_id"])
    assert a["status"] == "confirmed", a
    state["assignment_id"] = a["assignment_id"]


def test_31b_job_order_chat_round_trip(api, state):
    """Runs while the worker is a confirmed (chat) participant; completed shifts leave the chat."""
    ch = bearer(state["client"]["access_token"])
    wh = bearer(state["worker"]["access_token"])
    key = base64.b64encode(os.urandom(32)).decode()
    for h in (ch, wh):
        ok(api.put("/job-order-chat/me/identity", headers=h, json={"x25519_public_key_b64": key}))
    parts = ok(api.get(f"/job-order-chat/{state['job_id']}/participants", headers=ch))
    assert {state["client"]["user_id"], state["worker"]["user_id"]} <= {p["user_id"] for p in parts}
    # Like the frontend: wrap the room key for every participant that has published a chat key.
    pkeys = ok(api.get(f"/job-order-chat/{state['job_id']}/participant-keys", headers=ch))
    for k in pkeys:
        ok(api.put(f"/job-order-chat/{state['job_id']}/wrappers", headers=ch,
                   json={"target_user_id": k["user_id"], "wrapped_key_b64": key}))
    ct = base64.b64encode(b"opaque-ciphertext").decode()
    iv = base64.b64encode(os.urandom(12)).decode()
    ok(api.post(f"/job-order-chat/{state['job_id']}/messages", headers=ch, json={"ciphertext_b64": ct, "iv_b64": iv}), 200, 201)
    msgs = ok(api.get(f"/job-order-chat/{state['job_id']}/messages", headers=wh))
    msgs = msgs if isinstance(msgs, list) else msgs.get("items", msgs.get("messages", []))
    assert any(m.get("ciphertext_b64") == ct for m in msgs)


def test_32_worker_clocks_in_and_out_with_photos(api, state):
    h = bearer(state["worker"]["access_token"])
    aid = state["assignment_id"]
    form = {"lat": str(JOB_LAT), "lng": str(JOB_LNG)}
    cin = ok(api.post(f"/attendance/assignments/{aid}/clock-in", headers=h, data=form, files={"photo": ("in.png", PNG, "image/png")}))
    assert cin is not None
    live = ok(api.get(f"/client/job-orders/{state['job_id']}/live-attendance", headers=bearer(state["client"]["access_token"])))
    assert "storage:attendance/" in str(live), "clock-in photo should be stored in Supabase Storage"
    ok(api.post(f"/attendance/assignments/{aid}/clock-out", headers=h, data=form, files={"photo": ("out.png", PNG, "image/png")}))


def test_33_client_endorses_and_ta_verifies_timesheet(api, state):
    ch = bearer(state["client"]["access_token"])
    rows = ok(api.get("/timesheets", headers=ch))
    rows = rows if isinstance(rows, list) else rows.get("items", [])
    t = next(r for r in rows if r["shift_id"] == state["shift_id"])
    assert t["status"] == "pending"
    ok(api.post(f"/timesheets/{t['id']}/employer-endorse", headers=ch, json={"stars": 5, "prefer_worker": True}))
    th = bearer(state["sessions"]["ta"]["access_token"])
    ok(api.post(f"/timesheets/{t['id']}/ta-verify", headers=th))
    state["timesheet_id"] = t["id"]


def test_34_ta_generates_payment_file_into_storage(api, state):
    th = bearer(state["sessions"]["ta"]["access_token"])
    out = ok(api.post("/payroll/generate-payment-file", headers=th))
    assert out["file"].startswith("storage:payroll/pay_") and out["workers"] >= 1


@pytest.mark.parametrize(
    "path",
    [
        "/exports/zoho/job-orders.csv",
        "/exports/zoho/timesheets.csv",
        "/exports/zoho/shifts.csv",
        "/exports/zoho/job-orders.xlsx",
        "/exports/zoho/timesheets.xlsx",
        "/exports/payroll/ta-verified-lines.csv",
    ],
)
def test_35_ta_exports(api, state, path):
    r = api.get(path, headers=bearer(state["sessions"]["ta"]["access_token"]))
    assert r.status_code == 200 and len(r.content) > 20, (path, r.status_code, r.text[:200])


def test_36_client_rates_worker_and_sees_analytics(api, state):
    ch = bearer(state["client"]["access_token"])
    ok(api.post(f"/client/assignments/{state['assignment_id']}/rate", headers=ch, json={"stars": 5, "comment": "great"}), 200, 400)
    ok(api.get("/analytics/portal-dashboard", headers=ch))
    r = api.get("/exports/payroll/client-lines.csv", headers=ch)
    assert r.status_code == 200


# ================================================================================================
# 5. Messaging, notifications, admin, cron
# ================================================================================================


def test_41_worker_got_notifications(api, state):
    n = ok(api.get("/notifications", headers=bearer(state["worker"]["access_token"])))
    assert isinstance(n, (list, dict))


def test_42_admin_views_personas_and_audit_log(api, state):
    h = bearer(state["sessions"]["admin"]["access_token"])
    ok(api.get("/admin/personas/worker", headers=h))
    logs = ok(api.get("/admin/audit-logs", headers=h))
    assert "job_order_publish" in str(logs)
    # Workers and clients are refused (admin + TA only).
    assert api.get("/admin/audit-logs", headers=bearer(state["worker"]["access_token"])).status_code == 403
    assert api.get("/admin/audit-logs", headers=bearer(state["client"]["access_token"])).status_code == 403


def test_43_blacklisted_worker_is_locked_out_everywhere(api, state):
    # Fresh worker so the main test worker stays usable.
    email = f"e2e.blacklist.{RUN}@example.com"
    ok(api.post("/auth/worker/email-otp/request", json={"email": email}))
    w = ok(api.post("/auth/worker/email-otp/verify", json={"email": email, "code": mailpit_code(email)}))
    ok(api.get("/notifications", headers=bearer(w["access_token"])))

    ah = bearer(state["sessions"]["admin"]["access_token"])
    ok(api.post(f"/admin/personas/{w['user_id']}/blacklist", headers=ah, json={"reason": "e2e"}))

    assert api.get("/notifications", headers=bearer(w["access_token"])).status_code == 401  # live token refused
    assert api.post("/auth/refresh", json={"refresh_token": w["refresh_token"]}).status_code == 401
    httpx.delete(f"{MAILPIT_URL}/api/v1/search", params={"query": f"to:{email}"})
    request_otp(api, "/auth/worker/email-otp/request", {"email": email, "login_only": True})
    r = api.post("/auth/worker/email-otp/verify", json={"email": email, "code": mailpit_code(email), "login_only": True})
    assert r.status_code == 403


def test_44_cron_endpoints(api):
    assert api.get("/internal/cron/tick").status_code == 401
    out = ok(api.get("/internal/cron/tick", headers=bearer(CRON_SECRET)))
    assert {"dispatch_tasks_processed", "shift_reminders_sent", "recommendations"} <= set(out)
    # The completed shift queued a recommendation refresh, which some tick (loop or cron) drained.
    out2 = ok(api.post("/internal/cron/tick", headers=bearer(CRON_SECRET)))
    assert out2["recommendations"]["processed_workers"] == 0
    daily = ok(api.post("/internal/cron/daily", headers=bearer(CRON_SECRET)))
    assert daily["daily_refreshed"] is True
