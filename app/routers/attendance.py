from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.security import (
    employee_reference_values,
    require_session,
    serialize_id,
    today_south_africa,
)

router = APIRouter(tags=["attendance"])


def _serialize_attendance(record: Optional[dict]) -> Optional[dict]:
    if not record:
        return None
    out = serialize_id(record)
    return out


async def _get_today_record(db, employee_id: Any) -> Optional[dict]:
    values = employee_reference_values(employee_id)
    return await db["attendance"].find_one(
        {"employee_id": {"$in": values}, "work_date": today_south_africa()}
    )


def _is_manager(role: Any) -> bool:
    r = str(role or "").lower()
    return any(k in r for k in ("admin", "manager", "director", "executive"))


@router.get("/api/attendance/today")
@router.get("/api/admin/attendance/today")
async def attendance_today(ctx: dict = Depends(require_session)):
    db, employee, user = ctx["db"], ctx["employee"], ctx["user"]
    if not _is_manager(user.get("role")):
        record = await _get_today_record(db, employee.get("_id") or employee.get("employee_id"))
        ser = _serialize_attendance(record)
        return {"success": True, "attendance": ser, "records": [ser] if ser else []}

    rows = await db["attendance"].find({"work_date": today_south_africa()}).limit(500).to_list(500)
    records = [_serialize_attendance(r) for r in rows]
    return {"success": True, "attendance": records[0] if records else None, "records": records}


@router.get("/api/attendance/team")
@router.get("/api/admin/attendance/team")
async def attendance_team(ctx: dict = Depends(require_session)):
    db = ctx["db"]
    today = today_south_africa()
    rows = await db["attendance"].find({"work_date": today}).limit(500).to_list(500)
    return {"success": True, "records": [_serialize_attendance(r) for r in rows], "date": today}


@router.get("/api/admin/attendance")
@router.get("/api/attendance/records")
async def attendance_by_date(
    date: Optional[str] = Query(default=None),
    ctx: dict = Depends(require_session),
):
    db = ctx["db"]
    work_date = date or today_south_africa()
    rows = await db["attendance"].find({"work_date": work_date}).limit(500).to_list(500)
    return {
        "success": True,
        "records": [_serialize_attendance(r) for r in rows],
        "date": work_date,
    }


@router.get("/api/attendance/history")
@router.get("/api/admin/attendance/history")
async def attendance_history(
    days: int = Query(default=1, ge=1, le=31),
    ctx: dict = Depends(require_session),
):
    db, employee, user = ctx["db"], ctx["employee"], ctx["user"]
    from datetime import timedelta

    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    query: dict[str, Any] = {"work_date": {"$gte": since}}
    if not _is_manager(user.get("role")):
        values = employee_reference_values(employee.get("_id") or employee.get("employee_id"))
        query["employee_id"] = {"$in": values}
    rows = (
        await db["attendance"]
        .find(query)
        .sort([("work_date", -1), ("clock_in_at", -1)])
        .limit(1000)
        .to_list(1000)
    )
    return {"success": True, "records": [_serialize_attendance(r) for r in rows], "days": days}


@router.get("/api/attendance/working-now")
async def working_now(ctx: dict = Depends(require_session)):
    db = ctx["db"]
    today = today_south_africa()
    open_match = {
        "work_date": today,
        "clock_in_at": {"$exists": True, "$nin": [None, ""]},
        "$or": [
            {"clock_out_at": {"$exists": False}},
            {"clock_out_at": None},
            {"clock_out_at": ""},
        ],
    }
    rows = await db["attendance"].find(open_match).limit(500).to_list(500)
    return {"success": True, "records": [_serialize_attendance(r) for r in rows], "count": len(rows)}


async def _attendance_action(ctx: dict, action: str):
    db, employee = ctx["db"], ctx["employee"]
    emp_id = employee.get("_id") or employee.get("employee_id")
    values = employee_reference_values(emp_id)
    today = today_south_africa()
    record = await db["attendance"].find_one({"employee_id": {"$in": values}, "work_date": today})
    now = datetime.now(timezone.utc)

    if action == "clock_in":
        if record and record.get("clock_in_at") and not record.get("clock_out_at"):
            raise HTTPException(status_code=409, detail="Already clocked in.")
        doc = {
            "employee_id": emp_id,
            "employee_name": employee.get("full_name"),
            "department": employee.get("department"),
            "work_date": today,
            "clock_in_at": now,
            "status": "clocked_in",
            "created_at": now,
            "updated_at": now,
        }
        if record:
            await db["attendance"].update_one(
                {"_id": record["_id"]},
                {"$set": {**doc, "clock_out_at": None}},
            )
            record = await db["attendance"].find_one({"_id": record["_id"]})
        else:
            ins = await db["attendance"].insert_one(doc)
            record = await db["attendance"].find_one({"_id": ins.inserted_id})
        return {"success": True, "attendance": _serialize_attendance(record)}

    if not record or not record.get("clock_in_at"):
        raise HTTPException(status_code=400, detail="Not clocked in.")

    if action == "clock_out":
        await db["attendance"].update_one(
            {"_id": record["_id"]},
            {"$set": {"clock_out_at": now, "status": "clocked_out", "updated_at": now}},
        )
    elif action == "break_start":
        if str(record.get("status") or "").lower() in ("on_break", "break"):
            raise HTTPException(status_code=409, detail="A break is already in progress.")
        await db["attendance"].update_one(
            {"_id": record["_id"]},
            {"$set": {"status": "on_break", "break_started_at": now, "updated_at": now}},
        )
    elif action == "break_end":
        # Must clear break_started_at — otherwise status still looks like on_break
        # and the desktop never leaves the break state.
        started = record.get("break_started_at")
        extra_mins = 0
        if started is not None:
            try:
                if isinstance(started, datetime):
                    st = started if started.tzinfo else started.replace(tzinfo=timezone.utc)
                else:
                    st = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                extra_mins = max(0, int((now - st.astimezone(timezone.utc)).total_seconds() // 60))
            except Exception:
                extra_mins = 0
        prev = int(record.get("break_duration_minutes") or 0)
        await db["attendance"].update_one(
            {"_id": record["_id"]},
            {
                "$set": {
                    "status": "clocked_in",
                    "break_ended_at": now,
                    "break_duration_minutes": prev + extra_mins,
                    "updated_at": now,
                    "break_started_at": None,
                }
            },
        )
    else:
        raise HTTPException(status_code=400, detail="Unknown action")

    record = await db["attendance"].find_one({"_id": record["_id"]})
    return {"success": True, "attendance": _serialize_attendance(record)}


@router.post("/api/attendance/clock-in")
async def clock_in(ctx: dict = Depends(require_session)):
    return await _attendance_action(ctx, "clock_in")


@router.post("/api/attendance/clock-out")
async def clock_out(ctx: dict = Depends(require_session)):
    return await _attendance_action(ctx, "clock_out")


@router.post("/api/attendance/break-start")
@router.post("/api/attendance/break/start")
async def break_start(ctx: dict = Depends(require_session)):
    return await _attendance_action(ctx, "break_start")


@router.post("/api/attendance/break-end")
@router.post("/api/attendance/break/end")
async def break_end(ctx: dict = Depends(require_session)):
    return await _attendance_action(ctx, "break_end")


@router.get("/api/attendance/status")
async def attendance_status(ctx: dict = Depends(require_session)):
    """Current user's today attendance status (desktop header polls this)."""
    db, employee = ctx["db"], ctx["employee"]
    emp_id = employee.get("_id") or employee.get("employee_id")
    record = await _get_today_record(db, emp_id)
    ser = _serialize_attendance(record)

    # IMPORTANT: no record for today means not_started — never default to clocked_out.
    # The previous fallback made the desktop disable Clock In on a fresh day.
    if not record:
        status = "not_started"
        state = "not_started"
    else:
        raw = str(record.get("status") or "").strip().lower().replace(" ", "_").replace("-", "_")
        has_in = bool(record.get("clock_in_at") or record.get("started_at"))
        has_out = bool(record.get("clock_out_at"))
        # Active break: explicit status, OR break_started_at with no completed end after it
        bs = record.get("break_started_at")
        be = record.get("break_ended_at")
        break_open = False
        if bs and has_in and not has_out:
            if not be:
                break_open = True
            else:
                try:
                    def _ts(v):
                        if isinstance(v, datetime):
                            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
                        t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
                    break_open = _ts(bs) > _ts(be)
                except Exception:
                    break_open = raw in ("on_break", "break", "paused")
        on_break = raw in ("on_break", "break", "paused") or break_open

        if has_in and not has_out:
            if on_break:
                status = "on_break"
                state = "on_break"
            else:
                status = "clocked_in"
                state = "working"
        elif has_in and has_out:
            status = "clocked_out"
            state = "completed"
        elif raw in ("clocked_in", "working", "in", "active", "checked_in"):
            status = "clocked_in"
            state = "working"
        elif raw in ("on_break", "break"):
            status = "on_break"
            state = "on_break"
        elif raw in ("clocked_out", "completed", "out", "checked_out", "done"):
            status = "clocked_out"
            state = "completed"
        else:
            status = "not_started"
            state = "not_started"

    return {
        "success": True,
        "status": status,
        "state": state if record else "not_started",
        "attendance": ser,
        "record": ser,
        "clocked_in": bool(record and record.get("clock_in_at") and not record.get("clock_out_at")),
        "on_break": status == "on_break",
    }
