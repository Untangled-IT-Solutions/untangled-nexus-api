from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends

from app.config import get_settings
from app.security import require_session, today_south_africa, serialize_id

router = APIRouter(tags=["dashboard"])

_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _as_date(value: Any) -> Optional[datetime]:
    """Parse to timezone-aware UTC. Naive values are treated as UTC (Mongo / isoformat)."""
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _format_sast_hm(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("Africa/Johannesburg")).strftime("%H:%M")


def _is_late(clock_in: datetime, status: str = "") -> bool:
    """Late if clock-in after 09:00 Africa/Johannesburg on that calendar day."""
    sast = clock_in.astimezone(ZoneInfo("Africa/Johannesburg"))
    work_start = sast.replace(hour=9, minute=0, second=0, microsecond=0)
    return sast > work_start or "late" in (status or "").lower()


def _activity_from_attendance(record: dict, meta: Optional[dict]) -> Optional[dict]:
    cin = _as_date(record.get("clock_in_at"))
    if not cin:
        return None
    hh_in = _format_sast_hm(cin)
    status_raw = str(record.get("status") or "").lower()
    late = _is_late(cin, status_raw)
    name = (meta or {}).get("name") or record.get("employee_name") or "Employee"
    department = (meta or {}).get("department") or record.get("department") or "—"
    cout = _as_date(record.get("clock_out_at"))

    if cout:
        activity = f"Clocked Out · {_format_sast_hm(cout)} · In {hh_in}"
        status = "clocked_out"
        event_at = cout
    elif "break" in status_raw:
        activity = f"On Break · In {hh_in} (Late)" if late else f"On Break · In {hh_in}"
        status = "on_break"
        event_at = cin
    else:
        activity = f"Clocked In · Late · {hh_in}" if late else f"Clocked In · {hh_in}"
        status = "late" if late else "on_time"
        event_at = cin

    return {
        "employee_id": str(record.get("employee_id") or ""),
        "employee": name,
        "employee_name": name,
        "name": name,
        "department": department,
        "description": activity,
        "action": activity,
        "activity": activity,
        "created_at": event_at.isoformat(),
        "timestamp": event_at.isoformat(),
        "status": status,
        "work_date": record.get("work_date") or today_south_africa(),
        "clock_in_at": record.get("clock_in_at"),
        "clock_out_at": record.get("clock_out_at"),
    }


async def _build_summary(db) -> dict:
    today = today_south_africa()
    now = datetime.now(timezone.utc)

    open_match = {
        "work_date": today,
        "employee_id": {"$exists": True, "$nin": [None, ""]},
        "clock_in_at": {"$exists": True, "$nin": [None, ""]},
        "$and": [
            {
                "$or": [
                    {"clock_out_at": {"$exists": False}},
                    {"clock_out_at": None},
                    {"clock_out_at": ""},
                ]
            },
            {
                "$or": [
                    {"status": {"$in": ["clocked_in", "on_break", "Clocked In", "On Break"]}},
                    {"status": {"$exists": False}},
                    {"status": None},
                    {"status": ""},
                ]
            },
        ],
    }

    # Working now (distinct employees)
    working_ids = await db["attendance"].distinct("employee_id", open_match)
    people_working = len([x for x in working_ids if x not in (None, "")])

    # Today's attendance rows for activity + late
    cursor = (
        db["attendance"]
        .find(
            {
                "work_date": today,
                "employee_id": {"$exists": True, "$nin": [None, ""]},
                "clock_in_at": {"$exists": True, "$nin": [None, ""]},
            },
            projection={
                "_id": 1,
                "employee_id": 1,
                "employee_name": 1,
                "department": 1,
                "status": 1,
                "clock_in_at": 1,
                "clock_out_at": 1,
                "work_date": 1,
            },
        )
        .sort("clock_in_at", -1)
        .limit(100)
    )
    today_rows = await cursor.to_list(length=100)

    emp_ids = list({str(r.get("employee_id")) for r in today_rows if r.get("employee_id") is not None})
    meta: dict[str, dict] = {}
    if emp_ids:
        emps = await db["employees"].find(
            {
                "$or": [
                    {"employee_id": {"$in": emp_ids}},
                    {"id": {"$in": emp_ids}},
                ]
            },
            projection={
                "_id": 1,
                "employee_id": 1,
                "id": 1,
                "full_name": 1,
                "first_name": 1,
                "surname": 1,
                "last_name": 1,
                "department": 1,
            },
        ).to_list(length=500)
        for e in emps:
            name = (
                e.get("full_name")
                or " ".join(filter(None, [e.get("first_name"), e.get("surname") or e.get("last_name")]))
                or "Employee"
            )
            department = str(e.get("department") or "")
            for key in (str(e.get("_id")), str(e.get("employee_id") or ""), str(e.get("id") or "")):
                if key and key not in ("None", "null"):
                    meta[key] = {"name": name, "department": department}

    activity_items: list[dict] = []
    seen: set[str] = set()
    late_seen: set[str] = set()
    clocked_seen: set[str] = set()
    late_count = 0
    clocked_in_today = 0

    for row in today_rows:
        eid = str(row.get("employee_id") or "")
        if not eid:
            continue
        if eid not in clocked_seen:
            clocked_seen.add(eid)
            clocked_in_today += 1
        cin = _as_date(row.get("clock_in_at"))
        if cin and _is_late(cin, str(row.get("status") or "")) and eid not in late_seen:
            late_seen.add(eid)
            late_count += 1
        if eid in seen:
            continue
        seen.add(eid)
        item = _activity_from_attendance(row, meta.get(eid))
        if item:
            activity_items.append(item)

    activity_items.sort(
        key=lambda a: (0 if a.get("clock_out_at") else 1, str(a.get("created_at") or "")),
        reverse=True,
    )

    total_employees = await db["employees"].count_documents({})
    active_employees = await db["employees"].count_documents(
        {"status": {"$in": ["Active", "active", "enabled", "approved"]}}
    )
    on_leave = await db["employees"].count_documents(
        {"status": {"$in": ["On Leave", "on leave", "Leave", "leave"]}}
    )
    total_people = active_employees or total_employees

    day_start = datetime.fromisoformat(f"{today}T00:00:00+00:00")
    day_end = datetime.fromisoformat(f"{today}T23:59:59.999+00:00")
    closed = ["Completed", "Cancelled", "Canceled", "Closed", "closed", "Done", "done"]

    tasks_due = await db["work_assignments"].count_documents(
        {"due_date": {"$gte": day_start, "$lt": day_end}, "status": {"$nin": closed}}
    )
    tasks_overdue = await db["work_assignments"].count_documents(
        {"due_date": {"$lt": now}, "status": {"$nin": closed}}
    )
    tasks_in_progress = await db["work_assignments"].count_documents(
        {"status": {"$in": ["In Progress", "in progress", "in_progress"]}}
    )
    # High-priority tasks due today (single query – avoids client enrichment)
    tasks_high_priority = await db["work_assignments"].count_documents(
        {
            "due_date": {"$gte": day_start, "$lt": day_end},
            "status": {"$nin": closed},
            "priority": {"$in": ["High", "high", "Urgent", "urgent", "Critical", "critical", "H", "1"]},
        }
    )
    pending_approvals = await db["approvals"].count_documents(
        {"status": {"$in": ["Pending", "pending"]}}
    )
    # Small approvals queue for the dashboard card (max 8)
    pending_rows = (
        await db["approvals"]
        .find({"status": {"$in": ["Pending", "pending"]}})
        .sort("submitted_at", -1)
        .limit(8)
        .to_list(length=8)
    )
    approvals_queue = []
    for raw in pending_rows:
        approvals_queue.append(
            {
                "type": raw.get("request_type") or raw.get("type") or raw.get("title") or "Request",
                "title": raw.get("title") or raw.get("request_type") or "Request",
                "employee": raw.get("requested_by") or raw.get("requester") or "—",
                "when": raw.get("submitted_at") or raw.get("created_at") or "",
                "status": raw.get("status") or "Pending",
            }
        )

    present_count = people_working
    absent_count = max(0, total_people - present_count)
    attendance_pct = int(round((present_count / total_people) * 100)) if total_people > 0 else None

    return {
        "success": True,
        "people_working": people_working,
        "people_on_leave": on_leave,
        "people_on_site": people_working,
        "tasks_due_today": tasks_due,
        "tasks_high_priority": tasks_high_priority,
        "tasks_overdue": tasks_overdue,
        "tasks_waiting_review": 0,
        "completed_this_week": 0,
        "pending_approvals": pending_approvals,
        "approvals_queue": approvals_queue,
        "upcoming_deadlines": tasks_due,
        "latest_activity": activity_items,
        "recent_activity": activity_items,
        "activity": activity_items,
        "total_employees": total_employees,
        "total_people": total_people,
        "active_employees": active_employees,
        "tasks_in_progress": tasks_in_progress,
        "pending_tasks": 0,
        "present_count": present_count,
        "absent_count": absent_count,
        "late_count": late_count,
        "attendance_total": total_people,
        "attendance_pct": attendance_pct,
        "tasks": {
            "due_today": tasks_due,
            "high_priority_due_today": tasks_high_priority,
        },
        "attendance": {
            "present": present_count,
            "absent": absent_count,
            "late": late_count,
            "total": total_people,
            "percentage": attendance_pct,
            "pct": attendance_pct,
            "clocked_in_today": clocked_in_today,
        },
    }


@router.get("/api/dashboard/summary")
@router.get("/api/v1/nexus/dashboard")
async def dashboard_summary(ctx: dict = Depends(require_session)):
    settings = get_settings()
    now = time.time() * 1000
    if _cache["payload"] and (now - _cache["at"]) < settings.dashboard_cache_ms:
        return _cache["payload"]
    payload = await _build_summary(ctx["db"])
    _cache["at"] = time.time() * 1000
    _cache["payload"] = payload
    return payload
