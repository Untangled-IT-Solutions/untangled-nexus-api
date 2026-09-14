from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.db import connect_db, close_db
from app.routers import (
    health,
    auth,
    dashboard,
    attendance,
    notifications,
    tasks,
    approvals,
    quotes,
    employees,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_db()
    print("✅ MongoDB connected")
    print("🚀 Untangled Nexus API (FastAPI) ready")
    yield
    await close_db()


app = FastAPI(
    title="Untangled Nexus API",
    version="2.0.0",
    lifespan=lifespan,
)

settings = get_settings()
origins = [o.strip() for o in (settings.frontend_url or "*").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins if origins != ["*"] else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    # Log real path (not "/") so Render logs are useful
    print(
        {
            "method": request.method,
            "route": request.url.path,
            "query": request.url.query or None,
            "status": response.status_code,
            "duration_ms": round(duration_ms, 1),
        }
    )
    return response


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(attendance.router)
app.include_router(notifications.router)
app.include_router(tasks.router)
app.include_router(approvals.router)
app.include_router(quotes.router)
app.include_router(employees.router)
