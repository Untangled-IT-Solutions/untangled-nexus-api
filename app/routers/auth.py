from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.security import (
    find_employee_for_user,
    is_active,
    make_token,
    normalise_login,
    require_session,
    serialize_id,
    verify_password,
)
from app.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: Optional[str] = None
    email: Optional[str] = None
    password: str = Field(default="")


@router.post("/login")
async def login(body: LoginBody):
    username = normalise_login(body.username or body.email)
    password = str(body.password or "")
    if not username or not password:
        return {"success": False, "error": "Username and password are required."}

    db = get_db()
    users = db["users"]
    user = await users.find_one(
        {"$or": [{"username": username}, {"email": username}]},
        projection={
            "_id": 1,
            "username": 1,
            "email": 1,
            "role": 1,
            "status": 1,
            "employee_id": 1,
            "password_hash": 1,
            "hashed_password": 1,
            "password": 1,
            "full_name": 1,
            "department": 1,
            "position": 1,
        },
    )
    if not user:
        escaped = re.escape(username)
        user = await users.find_one(
            {
                "$or": [
                    {"username": {"$regex": f"^{escaped}$", "$options": "i"}},
                    {"email": {"$regex": f"^{escaped}$", "$options": "i"}},
                ]
            },
            projection={
                "_id": 1,
                "username": 1,
                "email": 1,
                "role": 1,
                "status": 1,
                "employee_id": 1,
                "password_hash": 1,
                "hashed_password": 1,
                "password": 1,
                "full_name": 1,
                "department": 1,
                "position": 1,
            },
        )

    if not user:
        return {"success": False, "error": "Invalid username or password."}
    if not is_active(user.get("status"), True):
        return {
            "success": False,
            "error": "This user account is inactive. Contact a manager.",
            "code": "USER_INACTIVE",
        }

    employee = await find_employee_for_user(user)
    if not employee:
        return {
            "success": False,
            "error": "Your login account is not linked to an employee record in MongoDB.",
            "code": "EMPLOYEE_NOT_FOUND",
        }
    if not is_active(employee.get("status"), True):
        return {
            "success": False,
            "error": "This employee is inactive and cannot log in. Contact a manager.",
            "code": "EMPLOYEE_INACTIVE",
        }

    stored = user.get("password_hash") or user.get("hashed_password") or user.get("password")
    if not verify_password(password, stored):
        return {"success": False, "error": "Invalid username or password."}

    token = make_token()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=14)
    await db["api_sessions"].insert_one(
        {
            "token": token,
            "user_id": user["_id"],
            "employee_id": employee.get("_id") or employee.get("employee_id"),
            "status": "active",
            "created_at": now,
            "expires_at": expires,
            "last_activity_at": now,
        }
    )

    safe_user = {
        "id": str(user["_id"]),
        "_id": str(user["_id"]),
        "username": user.get("username"),
        "email": user.get("email"),
        "role": user.get("role"),
        "status": user.get("status"),
        "employee_id": str(user.get("employee_id") or employee.get("_id") or ""),
        "full_name": user.get("full_name")
        or employee.get("full_name")
        or " ".join(
            filter(
                None,
                [employee.get("first_name"), employee.get("surname") or employee.get("last_name")],
            )
        ),
        "department": user.get("department") or employee.get("department"),
        "position": user.get("position") or employee.get("position"),
    }

    return {
        "success": True,
        "token": token,
        "access_token": token,
        "user": safe_user,
        "employee": serialize_id(employee),
        "expires_at": expires.isoformat(),
    }


@router.get("/me")
async def me(ctx: dict = Depends(require_session)):
    user = serialize_id(ctx["user"])
    employee = serialize_id(ctx["employee"])
    return {"success": True, "user": user, "employee": employee}


@router.post("/logout")
async def logout(ctx: dict = Depends(require_session)):
    db = ctx["db"]
    await db["api_sessions"].update_one(
        {"_id": ctx["session"]["_id"]},
        {"$set": {"status": "revoked", "revoked_at": datetime.now(timezone.utc)}},
    )
    return {"success": True, "message": "Logged out."}
