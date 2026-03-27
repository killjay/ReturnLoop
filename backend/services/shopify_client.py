"""Shopify integration for syncing data and registering webhooks.

Uses curl subprocess to bypass Python 3.9 LibreSSL TLS issues.
"""
import asyncio
import json
import subprocess
from typing import Optional
from backend.config import get_settings

settings = get_settings()

SHOPIFY_API_VERSION = "2024-10"


class ShopifyService:
    def __init__(self):
        self.store_url = settings.shopify_store_url
        self.api_token = settings.shopify_api_token
        self.api_secret = settings.shopify_api_secret
        self._connected = False

    @property
    def is_configured(self) -> bool:
        return bool(self.store_url and self._get_token())

    def _get_token(self) -> str:
        """Get the best available token -- prefer OAuth over static."""
        if self.api_token:
            return self.api_token
        # Fall back to OAuth token
        try:
            from backend.api.shopify_oauth import get_oauth_token
            return get_oauth_token()
        except Exception:
            return ""

    @property
    def base_url(self) -> str:
        return f"https://{self.store_url}/admin/api/{SHOPIFY_API_VERSION}"

    async def _api_call(self, method: str, endpoint: str, data: dict = None) -> Optional[dict]:
        """Make a Shopify API call via curl (bypasses SSL issues)."""
        url = f"{self.base_url}/{endpoint}"
        token = self._get_token()
        cmd = ["curl", "-s", "-X", method, url,
               "-H", f"X-Shopify-Access-Token: {token}",
               "-H", "Content-Type: application/json"]

        if data:
            cmd.extend(["-d", json.dumps(data)])

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
            return None
        except Exception as e:
            print(f"Shopify API error: {e}")
            return None

    async def check_connection(self) -> dict:
        """Test the Shopify API connection."""
        if not self.is_configured:
            return {"connected": False, "reason": "Shopify credentials not configured"}

        data = await self._api_call("GET", "shop.json")
        if data and "shop" in data:
            self._connected = True
            shop = data["shop"]
            return {
                "connected": True,
                "shop_name": shop.get("name", ""),
                "domain": shop.get("domain", ""),
                "plan": shop.get("plan_name", ""),
            }
        return {"connected": False, "reason": "API call failed"}

    async def register_return_webhook(self, ngrok_url: str) -> dict:
        """Register a webhook for returns/request events."""
        webhook_url = f"{ngrok_url}/api/webhooks/shopify/returns"

        data = await self._api_call("POST", "webhooks.json", {
            "webhook": {
                "topic": "orders/updated",
                "address": webhook_url,
                "format": "json",
            }
        })

        if data and "webhook" in data:
            return {"registered": True, "webhook_id": data["webhook"]["id"], "url": webhook_url}

        # Also try to register refunds webhook
        refund_data = await self._api_call("POST", "webhooks.json", {
            "webhook": {
                "topic": "refunds/create",
                "address": f"{ngrok_url}/api/webhooks/shopify/refunds",
                "format": "json",
            }
        })

        return {
            "registered": bool(data or refund_data),
            "orders_webhook": data,
            "refunds_webhook": refund_data,
            "url": webhook_url,
        }

    async def get_orders(self, limit: int = 50, status: str = "any") -> list:
        """Fetch orders from Shopify."""
        data = await self._api_call("GET", f"orders.json?limit={limit}&status={status}")
        if data and "orders" in data:
            return data["orders"]
        return []

    async def get_order(self, order_id: str) -> Optional[dict]:
        """Fetch a single order by ID."""
        data = await self._api_call("GET", f"orders/{order_id}.json")
        if data and "order" in data:
            return data["order"]
        return None

    async def get_customers(self, limit: int = 50) -> list:
        """Fetch customers from Shopify."""
        data = await self._api_call("GET", f"customers.json?limit={limit}")
        if data and "customers" in data:
            return data["customers"]
        return []

    async def get_customer(self, customer_id: str) -> Optional[dict]:
        """Fetch a single customer by ID."""
        data = await self._api_call("GET", f"customers/{customer_id}.json")
        if data and "customer" in data:
            return data["customer"]
        return None

    async def fulfill_order(self, shopify_order_id: str, tracking_number: str = "", note: str = "") -> dict:
        """Fulfill a Shopify order by creating a fulfillment via the Admin API.

        Uses the fulfillment orders flow (required since API 2022-07):
        1. GET fulfillment_orders for the order to get the fulfillment_order_id
        2. POST /fulfillments.json with that ID to create the fulfillment

        Returns the created fulfillment dict, or an error dict.
        """
        # Step 1: Get the open fulfillment order(s) for this order
        fo_data = await self._api_call("GET", f"orders/{shopify_order_id}/fulfillment_orders.json")
        if not fo_data or "fulfillment_orders" not in fo_data:
            return {"error": f"Could not fetch fulfillment orders for Shopify order {shopify_order_id}"}

        open_fos = [
            fo for fo in fo_data["fulfillment_orders"]
            if fo.get("status") in ("open", "in_progress")
        ]
        if not open_fos:
            return {"error": "No open fulfillment orders found — order may already be fulfilled"}

        # Step 2: Create fulfillment against all open fulfillment orders
        line_items_by_fo = [{"fulfillment_order_id": fo["id"]} for fo in open_fos]
        payload = {
            "fulfillment": {
                "line_items_by_fulfillment_order": line_items_by_fo,
                "notify_customer": True,
            }
        }
        if tracking_number:
            payload["fulfillment"]["tracking_info"] = {"number": tracking_number}
        if note:
            payload["fulfillment"]["message"] = note

        result = await self._api_call("POST", "fulfillments.json", payload)
        if result and "fulfillment" in result:
            fulfillment = result["fulfillment"]
            print(f"Shopify fulfillment created: id={fulfillment.get('id')} status={fulfillment.get('status')} order={shopify_order_id}")
            return {"fulfillment_id": fulfillment.get("id"), "status": fulfillment.get("status")}

        return {"error": f"Fulfillment creation failed: {result}"}

    async def get_products(self, limit: int = 50) -> list:
        """Fetch products from Shopify."""
        data = await self._api_call("GET", f"products.json?limit={limit}")
        if data and "products" in data:
            return data["products"]
        return []

    async def sync_to_db(self) -> dict:
        """Sync Shopify orders, customers, products into Return Loop DB."""
        from backend.db.database import async_session
        from backend.models.customer import Customer
        from backend.models.product import Product
        from backend.models.order import Order
        from backend.utils.geo import geocode_address
        from sqlalchemy import select
        import uuid

        counts = {"customers": 0, "products": 0, "orders": 0}

        async with async_session() as db:
            # Sync customers
            customers = await self.get_customers()
            for c in customers:
                cid = f"shopify-{c.get('id', '')}"
                existing_result = await db.execute(select(Customer).where(Customer.id == cid))
                existing_customer = existing_result.scalar_one_or_none()
                addr = c.get("default_address") or {}
                lat = float(addr.get("latitude", 0) or 0)
                lng = float(addr.get("longitude", 0) or 0)
                if not lat or not lng:
                    lat, lng = await geocode_address(
                        addr.get("address1", ""),
                        addr.get("city", ""),
                        addr.get("province") or "",
                        addr.get("zip") or "",
                        addr.get("country_code", "US"),
                    )
                if existing_customer:
                    if (not existing_customer.latitude or not existing_customer.longitude) and (lat or lng):
                        existing_customer.latitude = lat
                        existing_customer.longitude = lng
                    continue
                customer = Customer(
                    id=cid,
                    name=f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    email=c.get("email", ""),
                    phone=c.get("phone", ""),
                    address=addr.get("address1", ""),
                    city=addr.get("city", ""),
                    state=addr.get("province") or "",
                    zip_code=addr.get("zip") or "",
                    latitude=lat,
                    longitude=lng,
                    lifetime_value=float(c.get("total_spent", 0) or 0),
                    total_orders=int(c.get("orders_count", 0) or 0),
                )
                db.add(customer)
                counts["customers"] += 1

            # Sync products
            products = await self.get_products()
            for p in products:
                pid = f"shopify-{p.get('id', '')}"
                existing = await db.execute(select(Product).where(Product.id == pid))
                if existing.scalar_one_or_none():
                    continue
                variants = p.get("variants", [])
                price = float(variants[0].get("price", 0)) if variants else 0
                sku = (variants[0].get("sku", "") if variants else "") or f"SHOP-{p.get('id', '')}"
                # Check SKU uniqueness
                sku_exists = await db.execute(select(Product).where(Product.sku == sku))
                if sku_exists.scalar_one_or_none():
                    sku = f"{sku}-{pid[-6:]}"
                product = Product(
                    id=pid,
                    sku=sku,
                    name=p.get("title", ""),
                    category=p.get("product_type", "general") or "general",
                    brand=p.get("vendor", ""),
                    price=price,
                    cost=price * 0.4,
                    sizes_available=[v.get("title", "") for v in variants],
                )
                db.add(product)
                await db.flush()
                counts["products"] += 1

            # Sync orders
            orders = await self.get_orders()
            for o in orders:
                if not (o.get("customer") or {}).get("id"):
                    continue  # skip guest checkout orders — no customer to link
                oid = f"shopify-{o.get('id', '')}"
                existing_result = await db.execute(select(Order).where(Order.id == oid))
                existing_order = existing_result.scalar_one_or_none()
                shipping = o.get("shipping_address") or {}
                line_items = o.get("line_items", [])
                first_item = line_items[0] if line_items else {}
                o_lat = float(shipping.get("latitude", 0) or 0)
                o_lng = float(shipping.get("longitude", 0) or 0)
                if not o_lat or not o_lng:
                    # Fall back to customer coords if shipping has no address
                    cid = f"shopify-{(o.get('customer') or {}).get('id', '')}"
                    cust_result = await db.execute(select(Customer).where(Customer.id == cid))
                    cust = cust_result.scalar_one_or_none()
                    if cust and (cust.latitude or cust.longitude):
                        o_lat, o_lng = cust.latitude, cust.longitude
                    else:
                        o_lat, o_lng = await geocode_address(
                            shipping.get("address1", ""),
                            shipping.get("city", ""),
                            shipping.get("province") or "",
                            shipping.get("zip") or "",
                            shipping.get("country_code", "US"),
                        )
                if existing_order:
                    if (not existing_order.latitude or not existing_order.longitude) and (o_lat or o_lng):
                        existing_order.latitude = o_lat
                        existing_order.longitude = o_lng
                    continue
                order = Order(
                    id=oid,
                    customer_id=f"shopify-{(o.get('customer') or {}).get('id', '')}",
                    product_id=f"shopify-{first_item.get('product_id', '')}" if first_item.get("product_id") else "",
                    status="delivered" if o.get("fulfillment_status") == "fulfilled" else "pending",
                    size=(first_item.get("variant_title") or "M")[:10],
                    quantity=int(first_item.get("quantity", 1)) if first_item else 1,
                    total_price=float(o.get("total_price", 0) or 0),
                    shipping_address=shipping.get("address1", ""),
                    latitude=o_lat,
                    longitude=o_lng,
                )
                db.add(order)
                counts["orders"] += 1

            await db.commit()

        return counts


# Singleton
shopify_service = ShopifyService()
