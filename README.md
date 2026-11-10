# MCI Workforce Platform (MVP)

Mobile-first web app: Worker PWA, TA/Admin dashboard, Employer client portal.

## Stack

- **Backend**: FastAPI, SQLAlchemy, Alembic
- **Data, auth and files**: Supabase (Postgres, Supabase Auth for password + email-OTP login, Supabase Storage)
- **Frontend**: React, TypeScript, Vite, Tailwind, PWA, i18next (EN/ZH)
- **Hosting**: Vercel (static PWA + FastAPI as a Python function + Vercel Cron), or one Docker container

## How auth works

The browser only talks to the FastAPI backend; it never holds Supabase keys.

- `POST /auth/login` (username + password), `POST /auth/{worker|client}/email-otp/request|verify` and
  `POST /auth/refresh|logout` are thin wrappers over Supabase Auth. Supabase generates, emails and verifies
  OTP codes and issues the session; the backend adds the app rules (login-only, persona checks,
  inactive/blacklisted workers) and links each Supabase user to `users.supabase_user_id`.
- API calls send the Supabase access token (`Authorization: Bearer ...`). The backend verifies it locally
  (JWKS for ES256/RS256, or `SUPABASE_JWT_SECRET` for HS256) and loads the user by `supabase_user_id`.
- Access tokens last 1 hour; the frontend refreshes them silently (`frontend/src/api.ts`).
- App tables live in Supabase's `public` schema but are not reachable with the public anon key: RLS is on,
  `anon`/`authenticated` grants are revoked (migration `023`), and `public` is not exposed over the Data API.

## Local development

Requirements: Docker, Node 20+, Python 3.12+.

**1. Supabase (Postgres, Auth, Storage, Mailpit) in Docker**

```bash
npm install                 # installs the Supabase CLI (repo dev dependency) + frontend deps
npx supabase start          # first run pulls images; config in supabase/config.toml
npx supabase status -o env  # keys for the next step
```

| URL | Purpose |
|-----|---------|
| http://127.0.0.1:54323 | Supabase Studio |
| http://127.0.0.1:54324 | Mailpit: every OTP email lands here |

**2. Backend**

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows (source .venv/bin/activate elsewhere)
pip install -r requirements-dev.txt
copy .env.example .env          # paste ANON_KEY / SERVICE_ROLE_KEY / JWT_SECRET from `supabase status`
alembic upgrade head
python -m app.scripts.seed_demo # creates demo users in Supabase Auth too
uvicorn app.main:app --reload --port 8000
```

**3. Frontend**

```bash
npm run dev                     # from the repo root; http://localhost:5173, /api proxied to :8000
```

With `OTP_DEV_MODE=true` and `SUPABASE_DEV_MAILBOX_URL` set, the login/sign-up screens show the code
that Supabase just mailed to Mailpit. Never set these in production.

### Everything in Docker

With Supabase running (`npx supabase start`) and `backend/.env` filled in:

```bash
docker compose up --build       # API + built PWA on http://localhost:8000 (migrates + seeds on start)
```

### Existing database with users from before Supabase Auth

```bash
alembic upgrade head
python -m app.scripts.link_supabase_users --dry-run
python -m app.scripts.link_supabase_users   # imports bcrypt hashes: passwords keep working
```

## Tests

```bash
cd backend
pytest                                                    # unit/API tests (Supabase faked, SQLite)
E2E_BASE_URL=http://127.0.0.1:8000 pytest tests_e2e       # live workflows vs. running API + local Supabase
E2E_BASE_URL=http://127.0.0.1:8001/api pytest tests_e2e   # same, against the Vercel entry (api/index.py)
```

`tests_e2e` drives every role end to end: password + email-OTP login (codes read from Mailpit), refresh
and logout, onboarding with document upload to Storage, client-TA contract, job order -> marketplace ->
clock in/out with photos -> endorse -> TA verify -> payment file -> exports, chat, admin blacklisting,
cron, and a check that the anon key cannot read app tables.

## Deploy: Vercel + Supabase

1. **Supabase project**: create one, then from this repo:
   `npx supabase link --project-ref <ref>` and `npx supabase config push` (applies the bucket, OTP
   email templates and auth settings in `supabase/config.toml`). In the dashboard:
   - Authentication > Emails: configure custom SMTP (the built-in sender is heavily rate limited).
   - Data API: remove `public` from exposed schemas (the migration also locks the tables down).
2. **Schema + data** (from your machine/CI, using the *direct* or *session* connection string):
   `DATABASE_URL=postgresql+asyncpg://... alembic upgrade head`, then optionally
   `python -m app.scripts.seed_demo` for demo data or `link_supabase_users` for migrated users.
3. **Vercel project** (import the repo; `vercel.json` sets build, output, the `/api/*` function and crons).
   Environment variables:

   | Variable | Value |
   |----------|-------|
   | `DATABASE_URL` | Supavisor **transaction** pooler URL, port 6543, with `postgresql+asyncpg://` |
   | `SUPABASE_URL` | `https://<ref>.supabase.co` |
   | `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY` | Project Settings > API keys |
   | `SUPABASE_JWT_SECRET` | only if the project still signs with the legacy HS256 secret |
   | `SECRET_KEY`, `FERNET_KEY` | long random values (FERNET_KEY encrypts NRIC/bank fields) |
   | `CRON_SECRET` | random; Vercel Cron sends it as a Bearer token |
   | `CORS_ORIGINS` | your Vercel domain(s) |
   | `OTP_DEV_MODE` | `false` |

   `api/index.py` defaults `DB_POOL_MODE=serverless` (NullPool, no prepared statements) and
   `BACKGROUND_MODE=cron`.

Notes for Vercel:
- **Crons**: `vercel.json` runs `/api/internal/cron/tick` every 5 minutes (dispatch tasks, shift
  reminders, queued recommendation refreshes) and `/daily` at 03:00 UTC. Sub-daily crons need a Pro plan;
  on Hobby, schedule the same endpoints from Supabase `pg_cron` + `pg_net` instead.
- **Request size**: Vercel functions accept ~4.5 MB bodies, so keep `MAX_UPLOAD_BYTES` at or below
  ~4 MB there (photos/documents are uploaded through the API).
- **Duration**: functions are capped at 60 s (`maxDuration`); large exports must finish within that.

## Demo accounts (after seed)

| Role   | Username | Password      |
|--------|----------|---------------|
| Worker | worker   | demo_worker   |
| Worker | worker_2 | demo_worker_2 |
| Worker | worker_3 | demo_worker_3 |
| Worker | worker_4 | demo_worker_4 |
| Worker | worker_5 | demo_worker_5 |
| Worker | worker_6 | demo_worker_6 |
| Worker | worker_7 | demo_worker_7 |
| Worker | worker_8 | demo_worker_8 |
| TA     | ta       | demo_ta       |
| TA     | ta_2     | demo_ta_2     |
| Client | client   | demo_client   |
| Client | client_2 | demo_client_2 |
| Admin  | admin    | demo_admin    |

Every demo account also has a seeded email (e.g. `lucy.tan@mci-demoapp.com` for `worker`), so the Worker/Client
email-OTP login works too; codes appear in Mailpit.

## Seed inventory (local demo)

`python -m app.scripts.seed_demo` now seeds realistic local demo states:
- Multiple organizations, TA/client users, and a broader worker pool with varied skills/tier/rating/location.
- Job orders across draft, open, partially-filled/live, and completed statuses with multiple shifts.
- Mixed assignment and offer outcomes (confirmed, waitlisted, applied, rejected, pending, declined, expired).
- Attendance and payroll trails (present, late, absent, flagged; pending through released timesheets; paid payment record).

## Environment

See `backend/.env.example` for every variable and its local/production value.

## MVP features (recent)

- **Shift reminders**: in-app notifications ~24h and ~2h before confirmed shifts, run by the background loop (Docker) or the cron tick (Vercel); idempotent via the `shift_reminders_sent` table.
- **Files**: documents, clock-in photos and payment files live in the private `mci-uploads` Supabase Storage bucket; `GET /workers/me/documents/{id}/signed-url` returns a 15-minute signed URL.
- **ZOHO exports**: CSV + **Excel** (`.xlsx`) under `/exports/zoho/`.

## License

Proprietary — MCI Career Services Pte. Ltd.
# mci_platform
