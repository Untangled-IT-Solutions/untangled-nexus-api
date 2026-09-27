from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, List, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import get_db
from app.security import require_session, serialize_id

router = APIRouter(tags=["products"])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ProductCreate(BaseModel):
    id: str = Field(..., description="Stable string id, e.g. dell-latitude-5420")
    name: str
    sku: Optional[str] = None
    price: float
    was_price: Optional[float] = None
    stock_quantity: int = 0
    badge: Optional[str] = None
    badge_tone: Optional[str] = None
    image: str
    specs: List[str] = []
    category: Optional[str] = None
    active: bool = True


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    sku: Optional[str] = None
    price: Optional[float] = None
    was_price: Optional[float] = None
    badge: Optional[str] = None
    badge_tone: Optional[str] = None
    image: Optional[str] = None
    specs: Optional[List[str]] = None
    category: Optional[str] = None
    active: Optional[bool] = None


class StockAdjust(BaseModel):
    quantity: Optional[int] = None
    delta: Optional[int] = None
    reason: Optional[str] = "manual adjustment"


class OrderItem(BaseModel):
    product_id: str
    quantity: int = Field(..., gt=0)


class ConfirmSaleBody(BaseModel):
    items: List[OrderItem]
    order_reference: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _format_price(amount: float) -> str:
    whole = int(amount)
    cents = int(round((amount - whole) * 100))
    formatted = f"{whole:,}".replace(",", " ")
    return f"R{formatted}.{cents:02d}"


def _to_frontend_shape(doc: dict) -> dict:
    price_num = float(doc.get("price") or 0)
    was = doc.get("was_price")
    if was is None:
        was = doc.get("wasPrice")
    stock = doc.get("stock_quantity")
    if stock is None:
        stock = doc.get("stockQuantity") or 0

    return {
        "id": doc.get("id") or str(doc.get("_id")),
        "badge": doc.get("badge"),
        "badgeTone": doc.get("badge_tone") or doc.get("badgeTone"),
        "name": doc.get("name"),
        "image": doc.get("image") or "",
        "specs": doc.get("specs") or [],
        "price": _format_price(price_num),
        "wasPrice": _format_price(float(was)) if was is not None else None,
        "stockQuantity": int(stock),
        "sku": doc.get("sku"),
        "category": doc.get("category"),
        "active": doc.get("active", True),
    }


async def _find_product(db, product_id: str) -> Optional[dict]:
    doc = await db["products"].find_one({"id": product_id})
    if doc:
        return doc
    try:
        return await db["products"].find_one({"_id": ObjectId(product_id)})
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Public – website catalogue
# ---------------------------------------------------------------------------

@router.get("/api/products")
async def list_products(
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=200),
):
    try:
        db = get_db()
        query: dict = {}
        if active_only:
            query["active"] = True

        cursor = db["products"].find(query).sort([("name", 1)]).limit(limit)
        rows = await cursor.to_list(limit)
        products = [_to_frontend_shape(r) for r in rows if isinstance(r, dict)]
        return {
            "success": True,
            "products": products,
            "count": len(products),
        }
    except Exception as exc:
        print(f"⚠️ list_products failed: {exc}")
        return {"success": True, "products": [], "count": 0, "warning": str(exc)}


@router.get("/api/products/{product_id}")
async def get_product(product_id: str):
    db = get_db()
    doc = await _find_product(db, product_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"success": True, "product": _to_frontend_shape(doc)}


@router.post("/api/products/check-stock")
async def check_stock(body: ConfirmSaleBody):
    db = get_db()
    results = []
    all_ok = True

    for item in body.items:
        doc = await db["products"].find_one(
            {"id": item.product_id},
            {"stock_quantity": 1, "name": 1},
        )
        available = int((doc or {}).get("stock_quantity") or 0)
        ok = available >= item.quantity
        if not ok:
            all_ok = False
        results.append({
            "product_id": item.product_id,
            "requested": item.quantity,
            "available": available,
            "ok": ok,
            "name": (doc or {}).get("name"),
        })

    return {"success": True, "all_ok": all_ok, "items": results}


@router.post("/api/products/confirm-sale")
async def confirm_sale(body: ConfirmSaleBody):
    db = get_db()
    updated: list[dict] = []

    for item in body.items:
        result = await db["products"].find_one_and_update(
            {
                "id": item.product_id,
                "stock_quantity": {"$gte": item.quantity},
            },
            {
                "$inc": {"stock_quantity": -item.quantity},
                "$set": {"updated_at": datetime.now(timezone.utc)},
            },
            return_document=True,
        )
        if not result:
            for prev in updated:
                await db["products"].update_one(
                    {"id": prev["product_id"]},
                    {"$inc": {"stock_quantity": prev["quantity"]}},
                )
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for product {item.product_id}",
            )
        updated.append({"product_id": item.product_id, "quantity": item.quantity})

    await db["stock_movements"].insert_one({
        "type": "sale",
        "order_reference": body.order_reference,
        "items": [i.model_dump() for i in body.items],
        "created_at": datetime.now(timezone.utc),
    })

    return {"success": True, "updated": updated}


# ---------------------------------------------------------------------------
# Admin (require session)
# ---------------------------------------------------------------------------

@router.post("/api/products")
async def create_product(body: ProductCreate, ctx: dict = Depends(require_session)):
    db = ctx["db"]
    existing = await db["products"].find_one({"id": body.id})
    if existing:
        raise HTTPException(status_code=409, detail="Product id already exists")

    now = datetime.now(timezone.utc)
    doc = {
        "id": body.id,
        "name": body.name,
        "sku": body.sku or body.id.upper().replace(" ", "-"),
        "price": body.price,
        "was_price": body.was_price,
        "stock_quantity": max(0, body.stock_quantity),
        "badge": body.badge,
        "badge_tone": body.badge_tone,
        "image": body.image,
        "specs": body.specs,
        "category": body.category,
        "active": body.active,
        "created_at": now,
        "updated_at": now,
    }
    await db["products"].insert_one(doc)
    return {"success": True, "product": _to_frontend_shape(doc)}


@router.patch("/api/products/{product_id}")
async def update_product(
    product_id: str,
    body: ProductUpdate,
    ctx: dict = Depends(require_session),
):
    db = ctx["db"]
    updates = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    updates["updated_at"] = datetime.now(timezone.utc)

    result = await db["products"].find_one_and_update(
        {"id": product_id},
        {"$set": updates},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")
    return {"success": True, "product": _to_frontend_shape(result)}


@router.patch("/api/products/{product_id}/stock")
async def adjust_stock(
    product_id: str,
    body: StockAdjust,
    ctx: dict = Depends(require_session),
):
    if body.quantity is None and body.delta is None:
        raise HTTPException(status_code=400, detail="Provide quantity or delta")

    db = ctx["db"]
    user = ctx.get("user") or {}
    now = datetime.now(timezone.utc)

    if body.delta is not None:
        min_required = max(0, -body.delta)
        result = await db["products"].find_one_and_update(
            {
                "id": product_id,
                "stock_quantity": {"$gte": min_required},
            },
            {
                "$inc": {"stock_quantity": body.delta},
                "$set": {"updated_at": now},
            },
            return_document=True,
        )
        if not result:
            raise HTTPException(
                status_code=409,
                detail="Stock adjustment would go negative or product not found",
            )
        await db["stock_movements"].insert_one({
            "type": "adjustment",
            "product_id": product_id,
            "delta": body.delta,
            "reason": body.reason,
            "user_id": str(user.get("_id") or ""),
            "created_at": now,
        })
        return {"success": True, "product": _to_frontend_shape(result)}

    if body.quantity is not None and body.quantity < 0:
        raise HTTPException(status_code=400, detail="quantity cannot be negative")

    result = await db["products"].find_one_and_update(
        {"id": product_id},
        {"$set": {"stock_quantity": body.quantity, "updated_at": now}},
        return_document=True,
    )
    if not result:
        raise HTTPException(status_code=404, detail="Product not found")

    await db["stock_movements"].insert_one({
        "type": "set",
        "product_id": product_id,
        "quantity": body.quantity,
        "reason": body.reason,
        "user_id": str(user.get("_id") or ""),
        "created_at": now,
    })
    return {"success": True, "product": _to_frontend_shape(result)}


@router.get("/api/products/{product_id}/movements")
async def stock_movements(
    product_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: dict = Depends(require_session),
):
    db = ctx["db"]
    cursor = (
        db["stock_movements"]
        .find({"product_id": product_id})
        .sort([("created_at", -1)])
        .limit(limit)
    )
    rows = await cursor.to_list(limit)
    return {
        "success": True,
        "movements": [_serialize_doc(r) for r in rows],
        "count": len(rows),
    }
