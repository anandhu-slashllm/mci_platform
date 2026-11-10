# UAT / Demo walkthrough (MVP)

## Preconditions

Start Supabase (Postgres, Auth, Storage, Mailpit) and the app, as in the main README:

```bash
npx supabase start
docker compose up --build        # or run backend + frontend on the host
```

- **App (PWA) + API:** http://localhost:8000 (or http://localhost:5173 with Vite)
- **Swagger:** http://localhost:8000/docs
- **OTP emails:** http://127.0.0.1:54324 (Mailpit). With `OTP_DEV_MODE=true` the code is also shown on screen.

To skip re-seeding on restart: `RUN_SEED=false docker compose up`

The seed includes one **active** TA-client contract for the primary demo org so TA payroll and verification screens are populated immediately, plus historical contract records for realism.

## Accounts

Workers and clients log in with **email OTP** (Worker / Client tabs); staff use username + password (TA/Admin tab).

| Login | Role | Flow |
|-------|------|------|
| `ta` / `demo_ta` | TA | Publish job, exports, payroll |
| `lucy.tan@mci-demoapp.com` (or `worker` / `demo_worker`) | Worker | Accept offer, clock in/out |
| `ops@demowarehouse.sg` (or `client` / `demo_client`) | Client | Endorse timesheets, live attendance |
| `admin` / `demo_admin` | Admin | Audit logs, deactivate user (PDPA MVP) |
| `ta_2` / `demo_ta_2` | TA 2 | Secondary TA data for contract history |
| `ops@harborretail.sg` (or `client_2`) | Client 2 | Completed payroll trail and contract history |
| `worker_2` ... `worker_8` | Worker pool | Mixed assignment/offer outcomes |

Username/password demo logins are also seeded:
- `worker`, `worker_2` ... `worker_8`
- `ta`, `ta_2`
- `client`, `client_2`
- `admin`

## Seeded scenarios to demo quickly

- **Draft flow**: `Demo packing shift` starts as draft for TA publish walkthrough.
- **Open demand**: `Dock loading weekend rush` has future shifts with pending/accepted/declined/expired offers.
- **Live attendance**: `Inventory recount night shift` includes confirmed + waitlisted workers with present and late clock-ins.
- **Completed payroll trail**: `Retail pop-up teardown` includes present/absent/flagged attendance, disputed/verified timesheets, and a paid payment record.
- **Profile depth**: worker tiers, ratings, skills, certs, and preferred workers are varied to make matching views realistic.

## Happy path

1. **Client** (`ops@demowarehouse.sg`): Talent agent → pick a TA from the directory → **Send request**.
2. **TA** (`ta`): Clients → **Accept** the incoming request (establishes the active contract).
3. **TA**: Job Orders → **Publish** “Demo packing shift”.
4. **Worker** (`lucy.tan@mci-demoapp.com`): My offers → **Accept** (creates confirmed assignment when slots available).
5. **Worker**: After shift window (or adjust DB times for demo): Clock-in / Clock-out via API or future UI (use Swagger `POST /attendance/assignments/{id}/clock-in|clock-out` with photo + lat/lng near job site).
6. **Client** (`ops@demowarehouse.sg`): Timesheets → **Endorse** pending rows.
7. **TA**: Timesheets → **TA verify** → **Payroll** → Generate payment CSV → **Exports** → download ZOHO CSVs.

## Notes

- Geofence uses Haversine distance vs job `location_lat/lng` and `geofence_radius_m`.
- ZOHO column names are placeholders; replace with MCI’s import template when provided.
- **Shift reminders** (in-app): worker should see notifications ~24h / ~2h before shift start (background loop in Docker, `/internal/cron/tick` on Vercel).
- **Signed document download**: `GET /workers/me/documents/{id}/signed-url` (auth) returns a Supabase Storage URL valid for 15 minutes.
- **Excel exports**: TA dashboard → `job-orders.xlsx`, `timesheets.xlsx` (same data as CSV templates).
