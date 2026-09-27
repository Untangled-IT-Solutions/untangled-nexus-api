from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import get_db
from app.security import serialize_id

router = APIRouter(tags=["quotes-orders"])


def _deep_serialize(value: Any) -> Any:
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


# ---------------------------------------------------------------------------
# Create order (website checkout)
# ---------------------------------------------------------------------------

class OrderLine(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)
    name: Optional[str] = None
    price: Optional[float] = None


class CreateOrderBody(BaseModel):
    email: str
    full_name: Optional[str] = None
    phone: Optional[str] = None
    items: List[OrderLine]
    shipping_address: Optional[dict] = None
    notes: Optional[str] = None
    reference: Optional[str] = None


@router.post("/api/orders")
async def create_order(body: CreateOrderBody):
    """
    Place order + atomically decrease stock.
    409 if any line would oversell (with rollback of earlier lines).
    """
    if not body.items:
        raise HTTPException(status_code=400, detail="Order must include at least one item")

    email = (body.email or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email is required")

    db = get_db()
    now = datetime.now(timezone.utc)
    reference = (body.reference or f"ORD-{int(now.timestamp())}").strip().upper()

    decremented: list[dict] = []

    for line in body.items:
        result = await db["products"].find_one_and_update(
            {
                "id": line.product_id,
                "stock_quantity": {"$gte": line.quantity},
            },
            {
                "$inc": {"stock_quantity": -line.quantity},
                "$set": {"updated_at": now},
            },
            return_document=True,
        )
        if not result:
            for prev in decremented:
                await db["products"].update_one(
                    {"id": prev["product_id"]},
                    {"$inc": {"stock_quantity": prev["quantity"]}},
                )
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for product {line.product_id}",
            )
        decremented.append({"product_id": line.product_id, "quantity": line.quantity})

    order_doc = {
        "reference": reference,
        "email": email,
        "full_name": body.full_name,
        "phone": body.phone,
        "items": [i.model_dump() for i in body.items],
        "shipping_address": body.shipping_address,
        "notes": body.notes,
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "createdAt": now,
    }
    insert = await db["orders"].insert_one(order_doc)

    await db["stock_movements"].insert_one({
        "type": "sale",
        "order_reference": reference,
        "items": [i.model_dump() for i in body.items],
        "created_at": now,
    })

    return {
        "success": True,
        "order": {
            "id": str(insert.inserted_id),
            "reference": reference,
            "status": "pending",
            "email": email,
            "full_name": body.full_name,
            "items": order_doc["items"],
        },
    }


@router.get("/api/quotes")
async def list_quotes(limit: int = Query(default=50, ge=1, le=200)):
    try:
        db = get_db()
        try:
            cursor = db["quotes"].find({}).sort([("createdAt", -1)]).limit(limit)
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
