"""Seed the database with demo data for the Return Loop hackathon demo."""
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime

from backend.db.database import engine, async_session, Base
from backend.models.customer import Customer
from backend.models.product import Product
from backend.models.order import Order

SEED_DIR = Path(__file__).parent.parent.parent / "data" / "seed"


def parse_datetime(val):
    if val is None:
        return None
    return datetime.fromisoformat(val)


async def seed():
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    async with async_session() as session:
        # Seed customers
        with open(SEED_DIR / "customers.json") as f:
            customers_data = json.load(f)
        for c in customers_data:
            session.add(Customer(**c))
        await session.commit()
        print(f"Seeded {len(customers_data)} customers")

        # Seed products
        with open(SEED_DIR / "products.json") as f:
            products_data = json.load(f)
        for p in products_data:
            session.add(Product(**p))
        await session.commit()
        print(f"Seeded {len(products_data)} products")

        # Seed orders
        with open(SEED_DIR / "orders.json") as f:
            orders_data = json.load(f)
        for o in orders_data:
            o["ordered_at"] = parse_datetime(o["ordered_at"])
            o["delivered_at"] = parse_datetime(o.get("delivered_at"))
            o.setdefault("shipped_from", "warehouse")
            o.setdefault("tracking_number", "")
            session.add(Order(**o))
        await session.commit()
        print(f"Seeded {len(orders_data)} orders")

    print("Database seeded successfully!")


if __name__ == "__main__":
    asyncio.run(seed())
