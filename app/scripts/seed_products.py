"""
Seed products into MongoDB.

  export MONGODB_URI="mongodb+srv://..."
  python -m scripts.seed_products
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import connect_db, get_db, close_db

PRODUCTS = [
    {
        "id": "dell-latitude-5410",
        "name": "Dell Latitude 5410",
        "sku": "DELL-LAT-5410",
        "price": 15950.0,
        "was_price": 18500.0,
        "stock_quantity": 10,
        "badge": "Ready now!",
        "badge_tone": "lime",
        "image": "https://i.imgur.com/l5t9FE6.png",
        "specs": [
            "Intel Core i5 10th Gen",
            "8GB RAM",
            "256GB NVMe SSD",
            '14" FHD Display',
            "Windows 11 Pro",
            "3 Month Back-to-base Warranty",
        ],
        "category": "Laptops",
        "active": True,
    },
    {
        "id": "dell-latitude-5420",
        "name": "Dell Latitude 5420",
        "sku": "DELL-LAT-5420",
        "price": 15950.0,
        "was_price": None,
        "stock_quantity": 8,
        "badge": "Ready now!",
        "badge_tone": "lime",
        "image": "https://i.imgur.com/l5t9FE6.png",
        "specs": [
            "Intel Core i5 11th Gen",
            "16GB RAM",
            "512GB NVMe SSD",
            '14" FHD Display',
            "Backlit Keyboard",
            "3 Month Back-to-base Warranty",
        ],
        "category": "Laptops",
        "active": True,
    },
    {
        "id": "dell-latitude-5440",
        "name": "Dell Latitude 5440",
        "sku": "DELL-LAT-5440",
        "price": 18950.0,
        "was_price": None,
        "stock_quantity": 5,
        "badge": "Ready now!",
        "badge_tone": "lime",
        "image": "https://i.imgur.com/l5t9FE6.png",
        "specs": [
            "Intel Core i7 12th Gen",
            "16GB RAM",
            "512GB NVMe SSD",
            '14" FHD Display',
            "Thunderbolt 4",
            "3 Month Back-to-base Warranty",
        ],
        "category": "Laptops",
        "active": True,
    },
    {
        "id": "dell-monitor-p2422h",
        "name": 'Dell 24" Monitor - P2422H',
        "sku": "DELL-P2422H",
        "price": 2950.0,
        "was_price": 3600.0,
        "stock_quantity": 15,
        "badge": "Special price!",
        "badge_tone": "deal",
        "image": "https://i.imgur.com/l5t9FE6.png",
        "specs": [
            '24" IPS FHD Panel',
            "1920 x 1080 @ 60Hz",
            "HDMI, DisplayPort, VGA",
            "Height adjustable stand",
            "Ultrathin bezels",
        ],
        "category": "Monitors",
        "active": True,
    },
]


async def main() -> None:
    await connect_db()
    db = get_db()
    now = datetime.now(timezone.utc)

    for p in PRODUCTS:
        doc = {**p, "created_at": now, "updated_at": now}
        await db["products"].update_one(
            {"id": p["id"]},
            {"$set": doc},
            upsert=True,
        )
        print(f"✅ upserted {p['id']}  stock={p['stock_quantity']}")

    await db["products"].create_index("id", unique=True)
    await db["products"].create_index("active")
    await db["stock_movements"].create_index([("product_id", 1), ("created_at", -1)])

    count = await db["products"].count_documents({})
    print(f"Done. products collection has {count} document(s).")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
