from fastapi import APIRouter
from app.db import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
@router.get("/api/health")
async def health():
    return {"success": True, "status": "ok"}


@router.get("/ready")
@router.get("/api/ready")
async def ready():
    try:
        db = get_db()
        await db.command("ping")
        return {"success": True, "status": "ready", "mongo": True}
    except Exception as exc:
        return {"success": False, "status": "not_ready", "error": str(exc)}


@router.api_route("/", methods=["GET", "HEAD"])
async def root():
    return {
        "success": True,
        "service": "untangled-nexus-api",
        "runtime": "fastapi",
        "health": "/api/health",
    }
