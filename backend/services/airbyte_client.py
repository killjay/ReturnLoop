"""Airbyte data sync from Shopify using the official airbyte-agent-shopify SDK.

Pulls customers, products, and orders via the Airbyte Shopify connector,
mapping records into the Return Loop data model.
"""
import json
import os
import uuid
import logging
from datetime import datetime
from typing import Optional

_STATE_FILE = os.path.join(os.path.dirname(__file__), "../../.airbyte_state.json")

from airbyte_agent_shopify import ShopifyConnector
from airbyte_agent_shopify.models import ShopifyAuthConfig

from backend.db.database import async_session
from backend.models.customer import Customer
from backend.models.product import Product
from backend.models.order import Order
from backend.api.ws import ws_manager

logger = logging.getLogger(__name__)


class AirbyteService:
    """Manages data sync from Shopify via the Airbyte agent connector SDK."""

    def __init__(self):
        self._last_sync = None
        self._sync_status = "idle"
        self._sync_counts = {"customers": 0, "products": 0, "orders": 0}
        self._connected_sources = []
        self._connector: Optional[ShopifyConnector] = None
        self._load_state()

    def _load_state(self):
        """Restore sync state from disk so restarts don't wipe the status."""
        try:
            with open(_STATE_FILE) as f:
                state = json.load(f)
            self._sync_status = state.get("sync_status", "idle")
            self._sync_counts = state.get("sync_counts", self._sync_counts)
            self._connected_sources = state.get("connected_sources", [])
            last_sync = state.get("last_sync")
            self._last_sync = datetime.fromisoformat(last_sync) if last_sync else None
        except (FileNotFoundError, Exception):
            pass  # first run or corrupt file — start fresh

    def _save_state(self):
        """Persist sync state to disk."""
        try:
            with open(_STATE_FILE, "w") as f:
                json.dump({
                    "sync_status": self._sync_status,
                    "sync_counts": self._sync_counts,
                    "connected_sources": self._connected_sources,
                    "last_sync": self._last_sync.isoformat() if self._last_sync else None,
                }, f)
        except Exception as e:
            logger.warning("Could not save Airbyte state: %s", e)

    @property
    def status(self) -> dict:
        return {
            "airbyte_available": True,
            "sync_status": self._sync_status,
            "last_sync": self._last_sync.isoformat() if self._last_sync else None,
            "record_counts": self._sync_counts,
            "connected_sources": self._connected_sources,
        }

    def _get_connector(self, shop_name: str, api_key: str) -> ShopifyConnector:
        """Create a ShopifyConnector instance with the given credentials."""
        # The SDK appends .myshopify.com internally, so strip it if present
        shop_name = shop_name.replace(".myshopify.com", "").strip()

        return ShopifyConnector(
            auth_config=ShopifyAuthConfig(api_key=api_key),
            shop=shop_name,
        )

    async def _fetch_all_records(self, connector: ShopifyConnector, entity: str, **params) -> list:
        """Fetch all records for an entity, handling pagination via since_id."""
        all_records = []
        query_params = {"limit": 250, **params}

        while True:
            result = await connector.execute(entity, "list", query_params)

            # Result is ShopifyExecuteResultWithMeta with .data (list) and .meta
            records = result.data if hasattr(result, "data") else result
            if not records:
                break

            all_records.extend(records)

            # Paginate using since_id (last record's ID)
            has_next = (
                hasattr(result, "meta")
                and result.meta
                and getattr(result.meta, "next_page_url", None)
            )
            if has_next and records:
                last_id = records[-1].get("id") if isinstance(records[-1], dict) else getattr(records[-1], "id", None)
                if last_id:
                    query_params["since_id"] = last_id
                else:
                    break
            else:
                break

        return all_records

    async def sync_from_shopify(self, shop_name: str, api_key: str) -> dict:
        """Sync orders, customers, and products from Shopify via the Airbyte SDK."""
        self._sync_status = "syncing"
        await ws_manager.broadcast({
            "type": "airbyte_sync",
            "data": {"status": "syncing", "source": "shopify"},
        })

        if not shop_name or not api_key:
            logger.info("Shopify credentials missing, falling back to demo sync")
            return await self._mock_shopify_sync()

        try:
            connector = self._get_connector(shop_name, api_key)

            # Verify connection first
            check_result = await connector.check()
            logger.info("Shopify connection check: %s", check_result)

            # Fetch all data via SDK (handles API calls internally)
            customers_data = await self._fetch_all_records(connector, "customers")
            products_data = await self._fetch_all_records(connector, "products")
            orders_data = await self._fetch_all_records(connector, "orders")

            counts = {"customers": 0, "products": 0, "orders": 0}

            async with async_session() as db:
                from sqlalchemy import select

                for record in customers_data:
                    customer = self._map_shopify_customer(record)
                    if not customer:
                        continue
                    existing = await db.execute(select(Customer).where(Customer.id == customer.id))
                    if not existing.scalar_one_or_none():
                        db.add(customer)
                        counts["customers"] += 1

                await db.flush()

                for record in products_data:
                    product = self._map_shopify_product(record)
                    if not product:
                        continue
                    existing = await db.execute(select(Product).where(Product.id == product.id))
                    if not existing.scalar_one_or_none():
                        sku_check = await db.execute(select(Product).where(Product.sku == product.sku))
                        if sku_check.scalar_one_or_none():
                            product.sku = f"{product.sku}-{product.id[-6:]}"
                        db.add(product)
                        counts["products"] += 1

                await db.flush()

                for record in orders_data:
                    order = self._map_shopify_order(record)
                    if not order:
                        continue
                    existing = await db.execute(select(Order).where(Order.id == order.id))
                    if not existing.scalar_one_or_none():
                        db.add(order)
                        counts["orders"] += 1

                await db.commit()

            self._sync_counts = counts
            self._last_sync = datetime.utcnow()
            self._sync_status = "completed"
            if "shopify" not in self._connected_sources:
                self._connected_sources.append("shopify")
            self._save_state()

            await ws_manager.broadcast({
                "type": "airbyte_sync",
                "data": {"status": "completed", "source": "shopify", "counts": counts},
            })

            logger.info("Shopify sync completed: %s", counts)
            return {"status": "completed", "counts": counts}

        except Exception as e:
            self._sync_status = "error"
            self._save_state()
            logger.exception("Shopify sync failed")
            await ws_manager.broadcast({
                "type": "airbyte_sync",
                "data": {"status": "error", "source": "shopify", "error": str(e)},
            })
            return {"status": "error", "error": str(e)}

    def _val(self, data, key, default=""):
        """Get a value from a dict or Pydantic model, coercing None to default."""
        if isinstance(data, dict):
            val = data.get(key, default)
        else:
            val = getattr(data, key, default)
        return val if val is not None else default

    def _map_shopify_customer(self, data) -> Optional[Customer]:
        """Map an Airbyte Shopify Customer record to our Customer model."""
        default_addr = self._val(data, "default_address", None)
        addresses = self._val(data, "addresses", None) or []
        addr = default_addr or (addresses[0] if addresses else None)

        return Customer(
            id=str(self._val(data, "id", uuid.uuid4())),
            name=f"{self._val(data, 'first_name', '') or ''} {self._val(data, 'last_name', '') or ''}".strip(),
            email=self._val(data, "email", "") or "",
            phone=self._val(data, "phone", "") or "",
            address=self._val(addr, "address1", "") if addr else "",
            city=self._val(addr, "city", "") if addr else "",
            state=self._val(addr, "province", "") if addr else "",
            zip_code=self._val(addr, "zip", "") if addr else "",
            latitude=0.0,
            longitude=0.0,
            lifetime_value=float(self._val(data, "total_spent", 0) or 0),
            total_orders=int(self._val(data, "orders_count", 0) or 0),
        )

    def _map_shopify_product(self, data) -> Optional[Product]:
        """Map an Airbyte Shopify Product record to our Product model."""
        variants = self._val(data, "variants", None) or []
        first_variant = variants[0] if variants else None

        price = float(self._val(first_variant, "price", 0) or 0) if first_variant else 0
        sku = (self._val(first_variant, "sku", "") or "") if first_variant else ""
        if not sku:
            sku = f"SKU-{self._val(data, 'id', uuid.uuid4().hex[:8])}"

        return Product(
            id=str(self._val(data, "id", uuid.uuid4())),
            sku=sku,
            name=self._val(data, "title", "") or "",
            category=self._val(data, "product_type", "general") or "general",
            brand=self._val(data, "vendor", "") or "",
            price=price,
            cost=price * 0.4,
            sizes_available=[self._val(v, "title", "") or "" for v in variants],
        )

    def _map_shopify_order(self, data) -> Optional[Order]:
        """Map an Airbyte Shopify Order record to our Order model."""
        shipping = self._val(data, "shipping_address", None)
        customer = self._val(data, "customer", None)
        customer_id = str(self._val(customer, "id", "")) if customer else ""

        # Extract product_id and size from first line item
        line_items = self._val(data, "line_items", []) or []
        product_id = ""
        size = "OS"  # Default to "One Size"
        if line_items:
            first_item = line_items[0]
            product_id = str(self._val(first_item, "product_id", ""))
            size = self._val(first_item, "variant_title", "") or "OS"

        return Order(
            id=str(self._val(data, "id", uuid.uuid4())),
            customer_id=customer_id,
            product_id=product_id,
            status="delivered",
            size=size,
            total_price=float(self._val(data, "total_price", 0) or 0),
            shipping_address=self._val(shipping, "address1", "") if shipping else "",
            latitude=0.0,
            longitude=0.0,
        )

    async def fulfill_rerouted_order(self, target_order_id: str, return_request_id: str = "") -> dict:
        """Fulfill the reroute target order in Shopify via the Admin API.

        Called after the Loop Matcher decides to reroute a return to a nearby customer.
        Extracts the Shopify order ID from our internal ID (format: 'shopify-{id}'),
        creates a fulfillment, then re-syncs to keep our DB in sync with Shopify.

        Returns fulfillment result dict.
        """
        from backend.services.shopify_client import shopify_service

        # Extract the raw Shopify numeric ID from 'shopify-{id}'
        shopify_order_id = target_order_id.replace("shopify-", "") if target_order_id.startswith("shopify-") else target_order_id

        if not shopify_order_id or not shopify_service.is_configured:
            logger.warning("Cannot fulfill order %s — Shopify not configured or ID invalid", target_order_id)
            return {"error": "Shopify not configured or invalid order ID"}

        note = f"Rerouted return — ReturnLoop request {return_request_id}" if return_request_id else "Rerouted return via ReturnLoop"
        result = await shopify_service.fulfill_order(shopify_order_id, note=note)

        if "error" not in result:
            logger.info("Fulfilled rerouted order %s via Shopify: %s", target_order_id, result)
            # Re-sync to reflect the updated fulfillment status in our DB
            await ws_manager.broadcast({
                "type": "airbyte_sync",
                "data": {"status": "fulfillment_created", "order_id": target_order_id, "shopify_order_id": shopify_order_id},
            })

        return result

    async def _mock_shopify_sync(self) -> dict:
        """Mock Shopify sync for demo -- simulates Airbyte pulling data."""
        import asyncio
        await asyncio.sleep(1)  # Simulate sync delay

        counts = {"customers": 25, "products": 15, "orders": 30}
        self._sync_counts = counts
        self._last_sync = datetime.utcnow()
        self._sync_status = "completed"
        if "shopify" not in self._connected_sources:
            self._connected_sources.append("shopify (demo)")
        self._save_state()

        await ws_manager.broadcast({
            "type": "airbyte_sync",
            "data": {"status": "completed", "source": "shopify", "counts": counts, "mode": "demo"},
        })

        return {"status": "completed", "counts": counts, "mode": "demo_seed_data"}


# Singleton
airbyte_service = AirbyteService()
