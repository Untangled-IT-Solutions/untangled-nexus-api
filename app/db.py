from __future__ import annotations

import os
import re
from urllib.parse import urlparse, unquote

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from app.config import get_settings

_client: AsyncIOMotorClient | None = None
_db_name: str | None = None


def _resolve_db_name(uri: str) -> str:
    """
    Resolve MongoDB database name safely.

    Atlas URIs often look like:
      mongodb+srv://user:pass@cluster.mongodb.net/?retryWrites=true
    (no path DB) — host contains dots and must NOT be used as the DB name.
    """
    # Explicit override wins
    explicit = (os.getenv("MONGODB_DB") or os.getenv("MONGO_DB") or "").strip()
    if explicit and "." not in explicit and "/" not in explicit:
        return explicit

    try:
        # urlparse works for mongodb:// ; for mongodb+srv:// path is still usable
        parsed = urlparse(uri)
        path = (parsed.path or "").lstrip("/")
        if path:
            # path may be "dbname" or "dbname/extra"
            name = unquote(path.split("/")[0]).strip()
            if name and "." not in name and name.lower() not in ("mongodb", "mongodb+srv"):
                return name
    except Exception:
        pass

    # Regex fallback: ...mongodb.net/dbname?...
    m = re.search(r"mongodb(?:\+srv)?://[^/]+/([^/?]+)", uri)
    if m:
        name = unquote(m.group(1)).strip()
        if name and "." not in name:
            return name

    # Safe default used by this project
    return "untangled_its"


async def connect_db() -> None:
    global _client, _db_name
    settings = get_settings()
    if not settings.mongodb_uri:
        raise RuntimeError("MONGODB_URI is required")

    _db_name = _resolve_db_name(settings.mongodb_uri)
    _client = AsyncIOMotorClient(
        settings.mongodb_uri,
        maxPoolSize=settings.mongodb_max_pool_size,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
    )
    # Verify connectivity
    await _client.admin.command("ping")
    print(f"✅ MongoDB connected (database={_db_name})")


async def close_db() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


def get_db() -> AsyncIOMotorDatabase:
    if _client is None:
        raise RuntimeError("Database not connected")
    name = _db_name or "untangled_its"
    # Final guard — never allow host-like names
    if not name or "." in name or " " in name:
        name = "untangled_its"
    return _client[name]
