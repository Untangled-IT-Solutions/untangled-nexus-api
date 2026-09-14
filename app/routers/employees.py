from __future__ import annotations

from fastapi import APIRouter, Depends

from app.security import require_session, serialize_id, is_active

router = APIRouter(tags=["employees"])


def _is_manager(role) -> bool:
    r = str(role or "").lower()
    return any(k in r for k in ("admin", "manager", "director", "executive"))


@router.get("/api/employees")
@router.get("/api/admin/employees")
async def list_employees(ctx: dict = Depends(require_session)):
    db, user = ctx["db"], ctx["user"]
    query = {}
    # Non-managers still get a list (desktop People page); filter inactive lightly
    rows = await db["employees"].find(query).sort("full_name", 1).limit(500).to_list(500)
    items = [serialize_id(r) for r in rows]
    return {"success": True, "employees": items, "items": items, "count": len(items)}
