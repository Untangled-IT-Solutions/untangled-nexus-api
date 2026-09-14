# Untangled Nexus API (FastAPI)

Python **FastAPI** rewrite of the Nexus backend for lower latency and simpler Render deploys.

## Stack

- FastAPI + Uvicorn
- Motor (async MongoDB)
- Same MongoDB Atlas database as before
- Compatible password hashes (`pbkdf2_sha256$…` and SHA-256 hex)

## Endpoints (desktop-critical)

| Area | Paths |
|------|--------|
| Health | `GET /api/health`, `GET /ready` |
| Auth | `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout` |
| Dashboard | `GET /api/dashboard/summary`, `GET /api/v1/nexus/dashboard` |
| Attendance | `GET /api/attendance/today`, team/history aliases, clock-in/out, break |
| Notifications | `GET /api/notifications`, unread-count, mark read |
| Tasks | `GET /api/tasks`, get/update/start/assign |
| Approvals | `GET /api/approvals` |
| Quotes/Orders | track + list |

Dashboard returns `present_count`, `late_count`, `absent_count` and nested `attendance` (Present = people working).

## Render

**Build command**

```text
pip install -r requirements.txt
```

**Start command**

```text
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

**Health check path:** `/api/health`

**Env**

- `MONGODB_URI` (required) — same Atlas URI as the Node app
- `FRONTEND_URL` (optional, default `*`)
- `DASHBOARD_CACHE_MS` (optional, default `15000`)

## Local

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
export MONGODB_URI="mongodb+srv://..."
uvicorn app.main:app --reload --port 10000
```

## Push to GitHub (replace Node deploy)

```powershell
cd path\to\untangled-nexus-api-fastapi
git init
git remote remove origin 2>$null
git remote add origin https://github.com/Siyanda-UntangledItS/untangled-nexus-api.git
git add -A
git commit -m "Migrate API to FastAPI"
git branch -M main
git push -u origin main --force
```

Then in Render: set runtime to **Python**, build/start commands as above, keep `MONGODB_URI`.
