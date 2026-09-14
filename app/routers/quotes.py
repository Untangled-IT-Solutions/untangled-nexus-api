from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Query

from app.db import get_db
from app.security import serialize_id

router = APIRouter(tags=["quotes"])


def _deep_serialize(value: Any) -> Any:
    """Recursively convert ObjectId / datetime for JSON responses."""
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _deep_serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_serialize(v) for v in value]
    return value


def _serialize_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = serialize_id(doc) or {}
    return _deep_serialize(out)


@router.get("/api/quotes")
async def list_quotes(limit: int = Query(default=50, ge=1, le=200)):
    """List quotes. Never 500 on empty collection or mixed schemas."""
    try:
        db = get_db()
        try:
            cursor = db["quotes"].find({}).sort([("createdAt", -1)]).limit(limit)
            rows = await cursor.to_list(limit)
        except Exception:
            try:
                cursor = db["quotes"].find({}).sort([("created_at", -1)]).limit(limit)
                rows = await cursor.to_list(limit)
            except Exception:
                rows = await db["quotes"].find({}).limit(limit).to_list(limit)
        return {
            "success": True,
            "quotes": [_serialize_doc(r) for r in rows if isinstance(r, dict)],
            "count": len(rows),
        }
    except Exception as exc:
        print(f"⚠️ list_quotes failed: {exc}")
        return {"success": True, "quotes": [], "count": 0, "warning": str(exc)}


@router.get("/api/quotes/track")
async def track_quote(
    ref: Optional[str] = Query(default=None),
    reference: Optional[str] = Query(default=None),
    email: Optional[str] = Query(default=None),
):
    db = get_db()
    reference = (ref or reference or "").strip().upper()
    email = (email or "").strip().lower()
    if not reference or not email:
        return {"success": False, "error": "reference and email are required"}
    quote = await db["quotes"].find_one(
        {
            "$or": [
                {"reference": reference, "email": email},
                {"reference": reference, "email": {"$regex": f"^{email}$", "$options": "i"}},
            ]
        }
    )
    if not quote:
        return {"success": False, "error": "Quote not found", "quote": None}
    return {"success": True, "quote": _serialize_doc(quote)}


@router.get("/api/orders")
async def list_orders(limit: int = Query(default=50, ge=1, le=200)):
    """List orders. Never 500 on empty collection or mixed schemas."""
    try:
        db = get_db()
        try:
            cursor = db["orders"].find({}).sort([("createdAt", -1)]).limit(limit)
            rows = await cursor.to_list(limit)
        except Exception:
            try:
                cursor = db["orders"].find({}).sort([("created_at", -1)]).limit(limit)
                rows = await cursor.to_list(limit)
            except Exception:
                rows = await db["orders"].find({}).limit(limit).to_list(limit)
        return {
            "success": True,
            "orders": [_serialize_doc(r) for r in rows if isinstance(r, dict)],
            "count": len(rows),
        }
    except Exception as exc:
        print(f"⚠️ list_orders failed: {exc}")
        return {"success": True, "orders": [], "count": 0, "warning": str(exc)}


@router.get("/api/orders/track")
async def track_order(
    ref: Optional[str] = Query(default=None),
    reference: Optional[str] = Query(default=None),
    email: Optional[str] = Query(default=None),
):
    db = get_db()
    reference = (ref or reference or "").strip().upper()
    email = (email or "").strip().lower()
    if not reference or not email:
        return {"success": False, "error": "reference and email are required"}
    order = await db["orders"].find_one(
        {
            "$or": [
                {"reference": reference, "email": email},
                {"reference": reference, "email": {"$regex": f"^{email}$", "$options": "i"}},
            ]
        }
    )
    if not order:
        return {"success": False, "error": "Order not found", "order": None}
    return {"success": True, "order": _serialize_doc(order)}
