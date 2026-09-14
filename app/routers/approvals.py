from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.security import require_session, serialize_id

router = APIRouter(tags=["approvals"])


@router.get("/api/approvals")
async def list_approvals(
    status: Optional[str] = Query(default=None),
    ctx: dict = Depends(require_session),
):
    db = ctx["db"]
    query: dict = {}
    if status:
        query["status"] = {"$in": [status, status.lower(), status.upper(), status.capitalize()]}
    rows = await db["approvals"].find(query).sort("created_at", -1).limit(100).to_list(100)
    items = [serialize_id(r) for r in rows]
    return {"success": True, "approvals": items, "items": items}
