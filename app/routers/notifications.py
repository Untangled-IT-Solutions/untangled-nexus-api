from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from fastapi import APIRouter, Depends, Query, Body, HTTPException

from app.security import require_session, serialize_id, safe_object_id

router = APIRouter(tags=["notifications"])


def _user_role(user: dict, employee: dict) -> str:
    role = (
        user.get("role")
        or employee.get("role")
        or employee.get("position")
        or ""
    )
    return str(role).strip()


def _identity_or_clause(user: dict, employee: dict) -> list[dict]:
    """Match personal notifications for this user/employee."""
    or_clause: list[dict] = []
    user_id = user.get("_id")
    emp_id = employee.get("_id") or employee.get("employee_id")
    username = str(user.get("username") or "").strip()
    email = str(user.get("email") or "").strip()
    full_name = str(
        user.get("full_name") or employee.get("full_name") or employee.get("name") or ""
    ).strip()

    if user_id is not None:
        or_clause += [{"user_id": user_id}, {"user_id": str(user_id)}]
    if emp_id is not None:
        or_clause += [{"employee_id": emp_id}, {"employee_id": str(emp_id)}]
    if username:
        or_clause += [
            {"recipient_username": username},
            {"recipient_name": username},
            {"recipient": username},
            {"user_name": username},
            {"username": username},
        ]
    if email:
        or_clause += [
            {"recipient_email": email},
            {"recipient": email},
            {"email": email},
        ]
    if full_name:
        or_clause += [
            {"recipient_name": full_name},
            {"recipient": full_name},
            {"user_name": full_name},
        ]
    return or_clause


def _list_query(
    user: dict,
    employee: dict,
    *,
    unread: Optional[Any] = None,
    role_filter: Optional[str] = None,
) -> dict:
    """
    Notifications visible to this session:
      - personal (user_id / employee_id / username / name), OR
      - role-targeted to the user's role, OR
      - broadcast (recipient_role in All / empty / missing)
    Optional client role filter further narrows role-targeted items.
    """
    personal = _identity_or_clause(user, employee)
    my_role = _user_role(user, employee)

    role_targets: list[dict] = [
        {"recipient_role": {"$in": ["All", "all", "", None]}},
        {"recipient_role": {"$exists": False}},
    ]
    if my_role:
        role_targets.append({"recipient_role": my_role})
        # common aliases
        role_targets.append({"recipient_role": my_role.title()})
        role_targets.append({"role": my_role})

    visibility: list[dict] = []
    if personal:
        visibility.append({"$or": personal})
    visibility.append({"$or": role_targets})

    query: dict = {"$or": visibility} if visibility else {}

    # Client optional role filter (UI dropdown) – only when not "All"
    if role_filter and str(role_filter).strip() not in ("", "All", "all"):
        rf = str(role_filter).strip()
        query = {
            "$and": [
                query,
                {
                    "$or": [
                        {"recipient_role": rf},
                        {"recipient_role": "All"},
                        {"recipient_role": {"$exists": False}},
                        *(_identity_or_clause(user, employee) or []),
                    ]
                },
            ]
        }

    if unread in (1, "1", True, "true", "True"):
        # Unread = NONE of the read flags are true.
        # Must use $and of $ne, NOT $or — otherwise a missing isRead
        # makes every document match forever (badge stuck at 9+).
        query = {
            "$and": [
                query,
                {"read": {"$ne": True}},
                {"is_read": {"$ne": True}},
                {"isRead": {"$ne": True}},
            ]
        }

    return query


@router.get("/api/notifications")
async def list_notifications(
    unread: Optional[int] = Query(default=None),
    role: Optional[str] = Query(default=None),
    scope: Optional[str] = Query(default=None),
    ctx: dict = Depends(require_session),
):
    db, user, employee = ctx["db"], ctx["user"], ctx["employee"]
    role_filter = role or scope
    query = _list_query(user, employee, unread=unread, role_filter=role_filter)

    rows = (
        await db["notifications"]
        .find(query)
        .sort("created_at", -1)
        .limit(100)
        .to_list(100)
    )
    items = [serialize_id(r) for r in rows]
    # Normalise read flag for the desktop client
    for item in items:
        if not item:
            continue
        read_val = item.get("read")
        if "is_read" not in item:
            item["is_read"] = bool(read_val) if read_val is not None else bool(item.get("isRead"))
        if "read" not in item:
            item["read"] = bool(item.get("is_read"))
    return {"success": True, "notifications": items, "items": items}


@router.get("/api/notifications/unread-count")
async def unread_count(ctx: dict = Depends(require_session)):
    db, user, employee = ctx["db"], ctx["user"], ctx["employee"]
    query = _list_query(user, employee, unread=1)
    count = await db["notifications"].count_documents(query)
    return {"success": True, "count": count, "unread": count}


@router.post("/api/notifications/read-all")
@router.post("/api/notifications/mark-all-read")
async def mark_all_read(ctx: dict = Depends(require_session)):
    db, user, employee = ctx["db"], ctx["user"], ctx["employee"]
    query = _list_query(user, employee, unread=1)
    result = await db["notifications"].update_many(
        query,
        {
            "$set": {
                "read": True,
                "is_read": True,
                "read_at": datetime.now(timezone.utc),
            }
        },
    )
    return {
        "success": True,
        "modified": result.modified_count,
        "count": result.modified_count,
    }


@router.post("/api/notifications/{notification_id}/read")
async def mark_one_read(notification_id: str, ctx: dict = Depends(require_session)):
    db = ctx["db"]
    oid = safe_object_id(notification_id)
    filt = {"_id": oid} if oid else {"_id": notification_id}
    result = await db["notifications"].update_one(
        filt,
        {
            "$set": {
                "read": True,
                "is_read": True,
                "read_at": datetime.now(timezone.utc),
            }
        },
    )
    return {
        "success": True,
        "matched": result.matched_count,
        "modified": result.modified_count,
    }


@router.post("/api/notifications")
async def create_notification(
    payload: dict[str, Any] = Body(default_factory=dict),
    ctx: dict = Depends(require_session),
):
    """Create a notification (used by desktop for quote/task alerts)."""
    db = ctx["db"]
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid payload")

    title = str(payload.get("title") or "Notification").strip()
    message = str(payload.get("message") or payload.get("body") or "").strip()
    if not message and not title:
        raise HTTPException(status_code=400, detail="title or message is required")

    doc: dict[str, Any] = {
        "title": title,
        "message": message,
        "category": str(payload.get("category") or "General"),
        "recipient_role": payload.get("recipient_role") or payload.get("role") or "All",
        "recipient_name": payload.get("recipient_name")
        or payload.get("recipient")
        or payload.get("user_name")
        or payload.get("recipient_username")
        or "",
        "recipient_username": payload.get("recipient_username")
        or payload.get("username")
        or "",
        "recipient_email": payload.get("recipient_email") or payload.get("email") or "",
        "user_id": payload.get("user_id"),
        "employee_id": payload.get("employee_id"),
        "reference_type": payload.get("reference_type") or "",
        "reference_id": payload.get("reference_id"),
        "is_executive": bool(payload.get("is_executive")),
        "read": False,
        "is_read": False,
        "created_at": datetime.now(timezone.utc),
        "created_by": str((ctx.get("user") or {}).get("username") or ""),
    }
    # Drop empty optional keys
    for key in ("user_id", "employee_id", "reference_id"):
        if doc.get(key) in (None, ""):
            doc.pop(key, None)

    result = await db["notifications"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "notification": serialize_id(doc), "id": str(result.inserted_id)}
