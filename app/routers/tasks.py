"""Tasks / work_assignments – list, create, update, assign (Mongo on server)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.security import require_session, serialize_id, safe_object_id, employee_reference_values

router = APIRouter(tags=["tasks"])


class TaskCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    assigned_employee: Optional[str] = ""
    assignee: Optional[str] = None
    assigned_to: Optional[Any] = None
    assigned_by: Optional[str] = ""
    priority: Optional[str] = "Medium"
    status: Optional[str] = None
    due_date: Optional[str] = None
    department: Optional[str] = ""
    category: Optional[str] = "Administration"
    estimated_hours: Optional[float] = 0
    story_points: Optional[int] = 1
    sprint_bucket: Optional[str] = "Backlog"
    hardware_serial: Optional[str] = ""
    external_reference: Optional[str] = ""
    attachments: Optional[Any] = None
    notes: Optional[str] = None


class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[float] = None
    assigned_to: Optional[Any] = None
    assigned_employee: Optional[str] = None
    assignee: Optional[str] = None
    assigned_by: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    department: Optional[str] = None
    category: Optional[str] = None
    sprint_bucket: Optional[str] = None
    story_points: Optional[int] = None
    hardware_serial: Optional[str] = None
    external_reference: Optional[str] = None
    notes: Optional[str] = None
    estimated_hours: Optional[float] = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_manager(role: Any) -> bool:
    r = str(role or "").lower()
    return any(k in r for k in ("admin", "manager", "director", "executive", "super user", "business lead"))


def _assignee_value(body: TaskCreate | TaskUpdate) -> str:
    for key in ("assigned_to", "assigned_employee", "assignee"):
        val = getattr(body, key, None)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


@router.get("/api/tasks")
async def list_tasks(
    scope: Optional[str] = Query(default=None),
    ctx: dict = Depends(require_session),
):
    db, user, employee = ctx["db"], ctx["user"], ctx["employee"]
    query: dict[str, Any] = {}
    role = str(user.get("role") or "")
    is_mgr = _is_manager(role)
    if not is_mgr or (scope or "").lower() in ("personal", "mine"):
        values = employee_reference_values(employee.get("_id") or employee.get("employee_id"))
        # Also match by display name / email for desktop clients that store names
        name = (
            employee.get("full_name")
            or employee.get("name")
            or user.get("full_name")
            or user.get("username")
            or user.get("email")
            or ""
        )
        or_clause: list[dict[str, Any]] = [
            {"assigned_to": {"$in": values}},
            {"assignee_id": {"$in": values}},
            {"employee_id": {"$in": values}},
        ]
        if name:
            or_clause.extend(
                [
                    {"assigned_to": name},
                    {"assigned_employee": name},
                    {"assignee": name},
                ]
            )
        query["$or"] = or_clause
    rows = await db["work_assignments"].find(query).sort("updated_at", -1).limit(300).to_list(300)
    tasks = [serialize_id(r) for r in rows]
    return {"success": True, "tasks": tasks, "items": tasks}


@router.post("/api/tasks")
async def create_task(body: TaskCreate, ctx: dict = Depends(require_session)):
    """Create a task / work assignment in MongoDB."""
    db, user, employee = ctx["db"], ctx["user"], ctx["employee"]
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    assignee = _assignee_value(body)
    status = (body.status or "").strip()
    if not status:
        status = "Assigned" if assignee else "Inbox"

    creator = (
        body.assigned_by
        or user.get("full_name")
        or user.get("username")
        or user.get("email")
        or ""
    )
    now = _now()
    doc: dict[str, Any] = {
        "title": title,
        "description": body.description or "",
        "assigned_to": assignee or None,
        "assigned_employee": assignee or "",
        "assignee": assignee or "",
        "assigned_by": creator,
        "priority": body.priority or "Medium",
        "status": status,
        "due_date": body.due_date,
        "department": body.department or "",
        "category": body.category or "Administration",
        "estimated_hours": float(body.estimated_hours or 0),
        "story_points": int(body.story_points or 1),
        "sprint_bucket": body.sprint_bucket or "Backlog",
        "hardware_serial": body.hardware_serial or "",
        "external_reference": body.external_reference or "",
        "attachments": body.attachments if body.attachments is not None else [],
        "notes": body.notes or "",
        "progress": 0,
        "created_at": now,
        "updated_at": now,
        "created_by": {
            "user_id": str(user.get("_id") or user.get("id") or ""),
            "name": creator,
            "role": user.get("role"),
        },
    }
    result = await db["work_assignments"].insert_one(doc)
    doc["_id"] = result.inserted_id
    return {"success": True, "task": serialize_id(doc), "id": str(result.inserted_id)}


@router.get("/api/tasks/{task_id}")
async def get_task(task_id: str, ctx: dict = Depends(require_session)):
    db = ctx["db"]
    oid = safe_object_id(task_id)
    task = await db["work_assignments"].find_one({"_id": oid} if oid else {"_id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True, "task": serialize_id(task)}


@router.post("/api/tasks/{task_id}/start")
async def start_task(task_id: str, ctx: dict = Depends(require_session)):
    db = ctx["db"]
    oid = safe_object_id(task_id)
    filt = {"_id": oid} if oid else {"_id": task_id}
    result = await db["work_assignments"].update_one(
        filt,
        {
            "$set": {
                "status": "In Progress",
                "started_at": _now(),
                "updated_at": _now(),
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await db["work_assignments"].find_one(filt)
    return {"success": True, "task": serialize_id(task)}


@router.post("/api/tasks/{task_id}/assign")
async def assign_task(task_id: str, body: TaskUpdate, ctx: dict = Depends(require_session)):
    db = ctx["db"]
    oid = safe_object_id(task_id)
    filt = {"_id": oid} if oid else {"_id": task_id}
    assignee = _assignee_value(body)
    if not assignee:
        raise HTTPException(status_code=400, detail="assigned_to is required")
    result = await db["work_assignments"].update_one(
        filt,
        {
            "$set": {
                "assigned_to": assignee,
                "assigned_employee": assignee,
                "assignee": assignee,
                "status": "Assigned",
                "updated_at": _now(),
            }
        },
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await db["work_assignments"].find_one(filt)
    return {"success": True, "task": serialize_id(task)}


@router.patch("/api/tasks/{task_id}")
@router.put("/api/tasks/{task_id}")
async def update_task(task_id: str, body: TaskUpdate, ctx: dict = Depends(require_session)):
    db = ctx["db"]
    oid = safe_object_id(task_id)
    filt = {"_id": oid} if oid else {"_id": task_id}
    updates: dict[str, Any] = {"updated_at": _now()}
    data = body.model_dump(exclude_unset=True)
    field_map = {
        "title": "title",
        "description": "description",
        "status": "status",
        "progress": "progress",
        "priority": "priority",
        "due_date": "due_date",
        "department": "department",
        "category": "category",
        "sprint_bucket": "sprint_bucket",
        "story_points": "story_points",
        "hardware_serial": "hardware_serial",
        "external_reference": "external_reference",
        "notes": "notes",
        "estimated_hours": "estimated_hours",
        "assigned_by": "assigned_by",
    }
    for src, dest in field_map.items():
        if src in data and data[src] is not None:
            updates[dest] = data[src]

    assignee = _assignee_value(body)
    if assignee:
        updates["assigned_to"] = assignee
        updates["assigned_employee"] = assignee
        updates["assignee"] = assignee

    result = await db["work_assignments"].update_one(filt, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    task = await db["work_assignments"].find_one(filt)
    return {"success": True, "task": serialize_id(task)}


@router.delete("/api/tasks/{task_id}")
async def delete_task(task_id: str, ctx: dict = Depends(require_session)):
    db, user = ctx["db"], ctx["user"]
    if not _is_manager(user.get("role")):
        raise HTTPException(status_code=403, detail="Only managers can delete tasks")
    oid = safe_object_id(task_id)
    filt = {"_id": oid} if oid else {"_id": task_id}
    result = await db["work_assignments"].delete_one(filt)
    if result.deleted_count == 0:
        # soft cancel
        upd = await db["work_assignments"].update_one(
            filt, {"$set": {"status": "Cancelled", "updated_at": _now()}}
        )
        if upd.matched_count == 0:
            raise HTTPException(status_code=404, detail="Task not found")
    return {"success": True}
