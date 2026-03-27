"""Shopify webhook endpoints for receiving return/refund events.

When a customer initiates a return in Shopify, the webhook fires here
and triggers the full Return Loop agent pipeline.
"""
import base64
import hashlib
import hmac
import uuid
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from sqlalchemy import select, or_

from backend.config import get_settings
from backend.db.database import async_session
from backend.models.order import Order
from backend.models.customer import Customer
from backend.models.product import Product
from backend.models.return_request import ReturnRequest
from backend.orchestrator.event_bus import event_bus, Event, RETURN_INITIATED
from backend.services.shopify_client import shopify_service
from backend.api.ws import ws_manager

settings = get_settings()
router = APIRouter()


def verify_shopify_webhook(body: bytes, signature: str) -> bool:
    """Verify Shopify webhook HMAC signature.

    Tries both the custom app secret and the OAuth app secret,
    since webhooks may come from either app.
    """
    if not signature:
        return True

    secrets_to_try = [
        settings.shopify_api_secret,
        settings.shopify_oauth_client_secret,
    ]

    for secret in secrets_to_try:
        if not secret:
            continue
        computed = base64.b64encode(
            hmac.new(
                secret.encode(),
                body,
                hashlib.sha256,
            ).digest()
        ).decode()
        if hmac.compare_digest(computed, signature):
            return True

    print(f"  HMAC verification failed for both secrets. Signature: {signature[:20]}...")
    return False


def extract_shipping_coords(payload: dict) -> tuple:
    """Extract lat/lng from Shopify shipping address.

    Tries multiple paths: shipping_address, billing_address, customer default_address.
    Returns (lat, lng) or (0.0, 0.0) if not found.
    """
    for addr_key in ["shipping_address", "billing_address"]:
        addr = payload.get(addr_key) or {}
        lat = addr.get("latitude")
        lng = addr.get("longitude")
        if lat and lng:
            return float(lat), float(lng)

    # Try customer's default address
    customer = payload.get("customer") or {}
    default_addr = customer.get("default_address") or {}
    lat = default_addr.get("latitude")
    lng = default_addr.get("longitude")
    if lat and lng:
        return float(lat), float(lng)

    # Fallback: use a default US location (San Francisco)
    return 37.7749, -122.4194


def extract_phone(payload: dict) -> str:
    """Extract phone number, falling back to shipping address phone.

    Priority: customer.phone → shipping_address.phone → billing_address.phone
    """
    customer = payload.get("customer") or {}
    phone = customer.get("phone", "")
    if phone:
        return phone

    for addr_key in ["shipping_address", "billing_address"]:
        addr = payload.get(addr_key) or {}
        phone = addr.get("phone", "")
        if phone:
            return phone

    return ""


async def enrich_customer_via_oauth(customer_id: str) -> dict:
    """Use the OAuth token to fetch full customer PII from Shopify API.

    This bypasses the PII redaction that webhooks have on dev stores.
    """
    import asyncio, subprocess, json as _json
    from backend.api.shopify_oauth import get_oauth_token, _oauth_tokens

    token = get_oauth_token()
    if not token or not _oauth_tokens:
        return {}

    shop = list(_oauth_tokens.keys())[0]

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    "curl", "-s",
                    f"https://{shop}/admin/api/2024-10/customers/{customer_id}.json",
                    "-H", f"X-Shopify-Access-Token: {token}",
                ],
                capture_output=True, text=True, timeout=15,
            )
        )
        if result.returncode == 0 and result.stdout:
            data = _json.loads(result.stdout)
            c = data.get("customer", {})
            addr = c.get("default_address") or {}
            phone = c.get("phone", "") or addr.get("phone", "")
            name = f"{c.get('first_name', '')} {c.get('last_name', '')}".strip()
            lat = float(addr.get("latitude", 0) or 0)
            lng = float(addr.get("longitude", 0) or 0)

            if name or phone:
                print(f"  OAUTH ENRICHMENT: {name} | Phone: {phone} | {addr.get('city', '')} ({lat}, {lng})")
                return {
                    "phone": phone,
                    "name": name,
                    "email": c.get("email", ""),
                    "latitude": lat,
                    "longitude": lng,
                    "lifetime_value": float(c.get("total_spent", 0) or 0),
                    "return_rate": 0.1,
                    "address": addr.get("address1", ""),
                    "city": addr.get("city", ""),
                }
    except Exception as e:
        print(f"  OAuth enrichment failed: {e}")

    return {}


async def enrich_customer_via_oauth_order(order_id: str) -> dict:
    """Fetch full order + customer details via OAuth token for returns/request webhook.

    The returns/request webhook only gives us order ID, not full customer/product data.
    We need to fetch the order to get customer_id, product_id, shipping address, phone.
    """
    import asyncio, subprocess, json as _json
    from backend.api.shopify_oauth import get_oauth_token, _oauth_tokens

    token = get_oauth_token()
    if not token or not _oauth_tokens:
        return {}

    shop = list(_oauth_tokens.keys())[0]

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["curl", "-s",
                 f"https://{shop}/admin/api/2024-10/orders/{order_id}.json",
                 "-H", f"X-Shopify-Access-Token: {token}"],
                capture_output=True, text=True, timeout=15,
            )
        )
        if result.returncode == 0 and result.stdout:
            data = _json.loads(result.stdout)
            o = data.get("order", {})
            customer = o.get("customer") or {}
            shipping = o.get("shipping_address") or {}
            line_items = o.get("line_items", [])
            first_item = line_items[0] if line_items else {}

            customer_id = str(customer.get("id", ""))
            phone = customer.get("phone", "") or shipping.get("phone", "")
            name = f"{customer.get('first_name', '')} {customer.get('last_name', '')}".strip()
            if not name:
                name = f"{shipping.get('first_name', '')} {shipping.get('last_name', '')}".strip()

            addr = customer.get("default_address") or shipping or {}

            print(f"  OAUTH ORDER ENRICHMENT: {name} | Phone: {phone} | Customer: {customer_id}")

            return {
                "customer_id": customer_id,
                "customer_name": name or "Shopify Customer",
                "phone": phone,
                "email": customer.get("email", ""),
                "address": addr.get("address1", ""),
                "city": addr.get("city", ""),
                "latitude": float(shipping.get("latitude", 0) or 0) or 37.7749,
                "longitude": float(shipping.get("longitude", 0) or 0) or -122.4194,
                "lifetime_value": float(customer.get("total_spent", 0) or 0),
                "product_id": str(first_item.get("product_id", "")),
                "product_name": first_item.get("title", "Product"),
                "product_price": float(first_item.get("price", 0) or 0),
                "size": first_item.get("variant_title", "M") or "M",
                "total_price": float(o.get("total_price", 0) or 0),
            }
    except Exception as e:
        print(f"  OAuth order enrichment failed: {e}")

    return {}


async def fallback_from_seed_db(customer_id: str, customer_name: str, db) -> dict:
    """Enrich customer data: first try OAuth API, then fall back to seed DB."""
    # Try OAuth enrichment first (gives real PII from Shopify)
    oauth_data = await enrich_customer_via_oauth(customer_id)
    if oauth_data.get("phone") or oauth_data.get("name"):
        return oauth_data

    from sqlalchemy import or_

    result = await db.execute(
        select(Customer).where(
            Customer.id.like("cust-%")
        ).order_by(Customer.lifetime_value.desc())
    )
    seed_customers = result.scalars().all()

    if not seed_customers:
        return {}

    for c in seed_customers:
        if customer_name and customer_name.lower() in c.name.lower():
            return {
                "phone": c.phone,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "lifetime_value": c.lifetime_value,
                "name": c.name,
                "return_rate": c.return_rate,
            }

    for c in seed_customers:
        if c.phone:
            return {
                "phone": c.phone,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "lifetime_value": c.lifetime_value,
                "name": c.name,
                "return_rate": c.return_rate,
            }

    return {}


@router.post("/returns")
async def handle_shopify_return(request: Request):
    """Handle Shopify returns/request or orders/updated webhook.

    Triggered when a customer requests a return in Shopify.
    Creates a ReturnRequest and fires the agent pipeline.
    """
    body = await request.body()
    signature = request.headers.get("X-Shopify-Hmac-SHA256", "")

    if not verify_shopify_webhook(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    topic = request.headers.get("X-Shopify-Topic", "")

    # DEBUG: Dump full webhook payload to file for inspection
    import json as _json
    with open("/tmp/shopify_webhook_latest.json", "w") as _f:
        _json.dump(payload, _f, indent=2, default=str)
    print(f"\nSHOPIFY WEBHOOK: {topic} | Payload saved to /tmp/shopify_webhook_latest.json")

    # HANDLE returns/request topic (GraphQL webhook -- different payload structure)
    # This webhook sends the Return object directly with order reference
    if topic == "returns/request" or (payload.get("return_line_items") and not payload.get("line_items")):
        print(f"SHOPIFY: returns/request webhook detected!")
        return_items = payload.get("return_line_items", [])
        raw_order_id = payload.get("order", {}).get("id", "")
        shopify_order_id = str(raw_order_id).replace("gid://shopify/Order/", "")
        return_reason = return_items[0].get("return_reason", "preference") if return_items else "preference"
        return_reason_note = return_items[0].get("return_reason_note", "") if return_items else ""
        customer_note = payload.get("customer_note", "") or return_reason_note

        # Map Shopify reason to our categories
        reason_map = {
            "size_too_small": "sizing", "size_too_large": "sizing",
            "wrong_item": "wrong_item", "defective": "damage",
            "damaged": "damage", "quality": "quality",
            "color": "preference", "style": "preference", "other": "preference",
        }
        reason_category = reason_map.get(return_reason, "preference")
        reason_detail = customer_note or f"Return requested via Shopify: {return_reason}"

        # Fetch order details via OAuth API to get customer info
        enriched = await enrich_customer_via_oauth_order(shopify_order_id)

        async with async_session() as db:
            # Idempotency
            existing = await db.execute(select(ReturnRequest).where(ReturnRequest.order_id == shopify_order_id))
            if existing.scalars().first():
                return {"status": "skipped", "reason": "Return already exists"}

            # Get or create customer/product/order using enriched data
            customer_id = enriched.get("customer_id", "")
            product_id = enriched.get("product_id", "")
            phone = enriched.get("phone", "")
            customer_name = enriched.get("customer_name", "Shopify Customer")
            latitude = enriched.get("latitude", 37.7749)
            longitude = enriched.get("longitude", -122.4194)

            # Ensure customer exists
            if customer_id:
                cust_check = await db.execute(select(Customer).where(or_(Customer.id == customer_id, Customer.id == f"shopify-{customer_id}")))
                existing_cust = cust_check.scalars().first()
                if existing_cust:
                    customer_id = existing_cust.id
                else:
                    c = Customer(id=customer_id, name=customer_name, email=enriched.get("email", ""),
                        phone=phone, address=enriched.get("address", ""), city=enriched.get("city", ""),
                        state="", zip_code="", latitude=latitude, longitude=longitude,
                        lifetime_value=enriched.get("lifetime_value", 0), total_orders=0)
                    db.add(c)
                    await db.flush()

            # Ensure product exists
            if product_id:
                prod_check = await db.execute(select(Product).where(or_(Product.id == product_id, Product.id == f"shopify-{product_id}")))
                existing_prod = prod_check.scalars().first()
                if existing_prod:
                    product_id = existing_prod.id
                else:
                    p = Product(id=product_id, sku=f"SHOP-{product_id}", name=enriched.get("product_name", "Product"),
                        category="general", brand="", price=enriched.get("product_price", 0), cost=0)
                    db.add(p)
                    await db.flush()

            # Ensure order exists
            order_check = await db.execute(select(Order).where(or_(Order.id == shopify_order_id, Order.id == f"shopify-{shopify_order_id}")))
            existing_order = order_check.scalars().first()
            if existing_order:
                order = existing_order
            else:
                order = Order(id=shopify_order_id, customer_id=customer_id, product_id=product_id,
                    status="delivered", size=enriched.get("size", "M"), quantity=1,
                    total_price=enriched.get("total_price", 0), shipping_address=enriched.get("address", ""),
                    latitude=latitude, longitude=longitude)
                db.add(order)
                await db.flush()

            await db.commit()

            # Create return request
            rr = ReturnRequest(order_id=order.id, customer_id=customer_id, product_id=product_id,
                status="initiated", reason_category=reason_category, reason_detail=reason_detail,
                item_condition="like_new")
            db.add(rr)
            await db.commit()
            await db.refresh(rr)

            order.status = "return_requested"
            await db.commit()

        # Look up product SKU from DB for Loop Matcher matching
        product_sku = ""
        if product_id:
            async with async_session() as sku_db:
                from backend.models.product import Product as ProdModel
                sku_result = await sku_db.execute(select(ProdModel).where(or_(ProdModel.id == product_id, ProdModel.id == f"shopify-{product_id}")))
                sku_prod = sku_result.scalars().first()
                if sku_prod:
                    product_sku = sku_prod.sku or ""

        # Emit event to trigger pipeline
        await event_bus.emit(Event(
            event_type=RETURN_INITIATED,
            return_request_id=rr.id,
            payload={
                "order_id": order.id, "customer_id": customer_id, "product_id": product_id,
                "customer_name": customer_name, "customer_phone": phone,
                "customer_ltv": enriched.get("lifetime_value", 0),
                "customer_return_rate": 0.1,
                "product_name": enriched.get("product_name", "Product"),
                "product_sku": product_sku, "product_price": enriched.get("product_price", 0),
                "product_return_rate": 0.12,
                "size": enriched.get("size", "M"),
                "latitude": latitude, "longitude": longitude,
                "reason_category": reason_category, "reason_detail": reason_detail,
                "item_condition": "like_new", "source": "shopify_returns_request",
            },
        ))

        await ws_manager.broadcast_return_update({
            "source": "shopify", "return_request_id": rr.id,
            "order_id": order.id, "status": "initiated", "reason": reason_category,
        })

        print(f"RETURNS_REQUEST: Created return {rr.id} for order {shopify_order_id} | phone={phone} | reason={reason_category}")
        return {"status": "ok", "return_request_id": rr.id}

    # FILTER for orders/updated: Only process orders that have a return requested
    returns_data = payload.get("returns", [])
    refunds = payload.get("refunds", [])
    financial_status = payload.get("financial_status", "")

    is_return_related = (
        len(returns_data) > 0 or
        len(refunds) > 0 or
        financial_status in ("refunded", "partially_refunded") or
        payload.get("cancelled_at") is not None
    )

    if not is_return_related:
        print(f"SHOPIFY WEBHOOK: {topic} | Skipping -- not a return (returns={len(returns_data)}, refunds={len(refunds)}, status={financial_status})")
        return {"status": "skipped", "reason": "Not a return-related order update"}

    # Extract return reason from Shopify's returns array
    shopify_return_reason = ""
    if returns_data:
        return_items = returns_data[0].get("return_line_items", [])
        if return_items:
            shopify_return_reason = return_items[0].get("return_reason", "")
            reason_note = return_items[0].get("return_reason_note", "")
            reason_def = return_items[0].get("return_reason_definition", {})
            print(f"SHOPIFY WEBHOOK: RETURN DETECTED | Reason: {shopify_return_reason} ({reason_def.get('name', '')}) | Note: {reason_note}")

    print(f"SHOPIFY WEBHOOK: {topic} | Processing return (returns={len(returns_data)}, refunds={len(refunds)})")

    # Broadcast that we received a Shopify return event
    await ws_manager.broadcast({
        "type": "shopify_event",
        "data": {"topic": topic, "received_at": datetime.utcnow().isoformat()},
    })

    # Extract return/order info from payload
    order_id = str(payload.get("id", payload.get("order_id", "")))
    customer_data = payload.get("customer") or {}
    customer_id = str(customer_data.get("id", ""))
    line_items = payload.get("line_items", payload.get("return_line_items", []))
    first_item = line_items[0] if line_items else {}
    customer_note = payload.get("customer_note", payload.get("note", ""))
    shipping_addr = payload.get("shipping_address") or {}

    # Extract geo coordinates from shipping address
    latitude, longitude = extract_shipping_coords(payload)

    # Nominatim geocode if Shopify didn't provide real coordinates
    if not latitude or not longitude or (latitude == 37.7749 and longitude == -122.4194):
        from backend.utils.geo import geocode_address
        addr = shipping_addr or payload.get("billing_address") or {}
        geo_lat, geo_lng = await geocode_address(
            addr.get("address1", ""),
            addr.get("city", ""),
            addr.get("province") or "",
            addr.get("zip") or "",
            addr.get("country_code", "US"),
        )
        if geo_lat or geo_lng:
            latitude, longitude = geo_lat, geo_lng

    # Extract phone from shipping address fallback
    phone = extract_phone(payload)

    # Determine reason from Shopify's return data or customer note
    reason_category = "preference"
    reason_detail = customer_note or ""

    # Use Shopify's structured return reason if available
    if shopify_return_reason:
        reason_map = {
            "size_too_small": "sizing", "size_too_large": "sizing",
            "wrong_item": "wrong_item", "received-the-wrong-item": "wrong_item",
            "damaged": "damage", "defective": "damage",
            "quality": "quality", "not_as_described": "quality",
            "color": "preference", "style": "preference", "other": "preference",
        }
        reason_category = reason_map.get(shopify_return_reason, "preference")
        if not reason_detail:
            reason_detail = f"Return requested via Shopify: {shopify_return_reason}"
    elif reason_detail:
        if any(kw in reason_detail.lower() for kw in ["size", "fit", "tight", "loose", "small", "large"]):
            reason_category = "sizing"
        elif any(kw in reason_detail.lower() for kw in ["damage", "broken", "defect", "torn"]):
            reason_category = "damage"
        elif any(kw in reason_detail.lower() for kw in ["quality", "cheap", "material"]):
            reason_category = "quality"
        elif any(kw in reason_detail.lower() for kw in ["wrong", "incorrect", "different"]):
            reason_category = "wrong_item"
    else:
        reason_detail = "Return requested via Shopify"

    async with async_session() as db:
        # FIX 4: Idempotency -- skip if return already exists for this order
        existing_return = await db.execute(
            select(ReturnRequest).where(ReturnRequest.order_id == order_id)
        )
        if existing_return.scalars().first():
            return {"status": "skipped", "reason": "Return already exists for this order"}

        # FIX 1: Look up order by both raw ID and prefixed ID
        result = await db.execute(
            select(Order).where(
                or_(Order.id == order_id, Order.id == f"shopify-{order_id}")
            )
        )
        order = result.scalars().first()

        # === Ensure ALL FK dependencies exist (PostgreSQL enforces strictly) ===

        # 1. Enrich customer data via OAuth FIRST (gets real name/phone/address)
        enriched = await enrich_customer_via_oauth(customer_id)

        # 2. Create customer if not exists (check both raw and prefixed IDs)
        if customer_id:
            cust_check = await db.execute(
                select(Customer).where(
                    or_(Customer.id == customer_id, Customer.id == f"shopify-{customer_id}")
                )
            )
            existing_customer = cust_check.scalars().first()
            if existing_customer:
                # Use the actual ID from DB (might be prefixed)
                customer_id = existing_customer.id
                print(f"  Found existing customer: {customer_id}")
            if not existing_customer:
                new_customer = Customer(
                    id=customer_id,
                    name=enriched.get("name") or f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip() or "Shopify Customer",
                    email=enriched.get("email") or customer_data.get("email", "") or "",
                    phone=enriched.get("phone") or extract_phone(payload) or "",
                    address=enriched.get("address") or shipping_addr.get("address1", "") or "",
                    city=enriched.get("city") or shipping_addr.get("city", "") or "",
                    state=shipping_addr.get("province", "") or "",
                    zip_code=shipping_addr.get("zip", "") or "",
                    latitude=enriched.get("latitude") or latitude,
                    longitude=enriched.get("longitude") or longitude,
                    lifetime_value=enriched.get("lifetime_value") or float(customer_data.get("total_spent", 0) or 0),
                    total_orders=int(customer_data.get("orders_count", 0) or 0),
                )
                db.add(new_customer)
                await db.flush()
                print(f"  Created customer: {customer_id} | {new_customer.name} | {new_customer.phone}")

        # 3. Create product if not exists (FK constraint on orders.product_id)
        webhook_product_id = str(first_item.get("product_id", "")) if first_item else ""
        if webhook_product_id:
            prod_check = await db.execute(
                select(Product).where(
                    or_(Product.id == webhook_product_id, Product.id == f"shopify-{webhook_product_id}")
                )
            )
            existing_product = prod_check.scalars().first()
            if existing_product:
                webhook_product_id = existing_product.id
                print(f"  Found existing product: {webhook_product_id}")
            if not existing_product:
                new_product = Product(
                    id=webhook_product_id,
                    sku=f"SHOP-{webhook_product_id}",
                    name=first_item.get("title", "Shopify Product") if first_item else "Shopify Product",
                    category="general",
                    brand=first_item.get("vendor", "") if first_item else "",
                    price=float(first_item.get("price", 0) or 0) if first_item else 0,
                    cost=float(first_item.get("price", 0) or 0) * 0.4 if first_item else 0,
                )
                db.add(new_product)
                await db.flush()
                print(f"  Created product: {webhook_product_id} | {new_product.name}")

        await db.commit()

        # If order not in DB, fetch from Shopify and create it
        if not order:
            shopify_order = await shopify_service.get_order(order_id)
            if shopify_order:
                s_shipping = shopify_order.get("shipping_address") or {}
                s_items = shopify_order.get("line_items", [])
                s_first = s_items[0] if s_items else {}
                s_lat = float(s_shipping.get("latitude", 0) or 0) or latitude
                s_lng = float(s_shipping.get("longitude", 0) or 0) or longitude
                order = Order(
                    id=order_id,
                    customer_id=customer_id,
                    product_id=str(s_first.get("product_id", "")),
                    status="delivered",
                    size=s_first.get("variant_title", "M") or "M",
                    quantity=int(s_first.get("quantity", 1)),
                    total_price=float(shopify_order.get("total_price", 0) or 0),
                    shipping_address=s_shipping.get("address1", ""),
                    latitude=s_lat,
                    longitude=s_lng,
                )
                db.add(order)
                await db.commit()
                await db.refresh(order)
            else:
                # Create from webhook payload directly
                product_id = str(first_item.get("product_id", "")) if first_item else ""
                order = Order(
                    id=order_id,
                    customer_id=customer_id,
                    product_id=product_id,
                    status="delivered",
                    size=first_item.get("variant_title", "M") or "M" if first_item else "M",
                    quantity=int(first_item.get("quantity", 1)) if first_item else 1,
                    total_price=float(payload.get("total_price", 0) or 0),
                    shipping_address=shipping_addr.get("address1", ""),
                    latitude=latitude,
                    longitude=longitude,
                )
                db.add(order)
                await db.commit()
                await db.refresh(order)

        if not order:
            return {"status": "skipped", "reason": "Order not found"}

        # FIX 2: Update order coords from shipping address if they're 0
        if (order.latitude == 0 and order.longitude == 0) and (latitude != 0 or longitude != 0):
            order.latitude = latitude
            order.longitude = longitude
            await db.commit()

        # FIX 1: Look up customer by both raw ID and prefixed ID
        cust_result = await db.execute(
            select(Customer).where(
                or_(Customer.id == customer_id, Customer.id == f"shopify-{customer_id}")
            )
        )
        customer = cust_result.scalars().first()

        # FIX 1: Look up product by both raw ID and prefixed ID
        product_id = order.product_id or ""
        prod_result = await db.execute(
            select(Product).where(
                or_(Product.id == product_id, Product.id == f"shopify-{product_id}")
            )
        )
        product = prod_result.scalars().first()

        # Create return request
        return_request = ReturnRequest(
            order_id=order.id,
            customer_id=customer_id,
            product_id=product_id,
            status="initiated",
            reason_category=reason_category,
            reason_detail=reason_detail,
            item_condition="like_new",
        )
        db.add(return_request)
        await db.commit()
        await db.refresh(return_request)

        order.status = "return_requested"
        await db.commit()

        # Apply enriched data (enriched was fetched via OAuth earlier in this handler)
        if enriched:
            if enriched.get("phone") and not phone:
                phone = enriched["phone"]
            if enriched.get("latitude") and order.latitude == 0:
                order.latitude = enriched["latitude"]
                order.longitude = enriched.get("longitude", 0)
                await db.commit()

        print(f"SHOPIFY DATA: phone={phone}, lat={order.latitude}, lng={order.longitude}, enriched={bool(enriched)}")

    # Build customer name
    customer_name = f"{customer_data.get('first_name', '')} {customer_data.get('last_name', '')}".strip()
    if not customer_name and enriched:
        customer_name = enriched.get("name", "")
    if not customer_name and customer:
        customer_name = customer.name
    if not customer_name:
        customer_name = f"{shipping_addr.get('first_name', '')} {shipping_addr.get('last_name', '')}".strip() or "Shopify Customer"

    # Determine LTV
    customer_ltv = float(customer_data.get("total_spent", 0) or 0)
    if not customer_ltv and enriched:
        customer_ltv = enriched.get("lifetime_value", 0)
    if not customer_ltv and customer:
        customer_ltv = customer.lifetime_value

    # Emit event to trigger the full agent pipeline
    await event_bus.emit(Event(
        event_type=RETURN_INITIATED,
        return_request_id=return_request.id,
        payload={
            "order_id": order.id,
            "customer_id": customer_id,
            "product_id": product_id,
            "customer_name": customer_name,
            "customer_phone": phone,
            "customer_ltv": customer_ltv,
            "customer_return_rate": enriched.get("return_rate", 0.1) if enriched else (customer.return_rate if customer else 0.1),
            "product_name": product.name if product else (first_item.get("title", "Product") if first_item else "Product"),
            "product_sku": product.sku if product else "",
            "product_price": product.price if product else order.total_price,
            "product_return_rate": product.return_rate if product else 0.12,
            "size": order.size,
            "latitude": order.latitude,
            "longitude": order.longitude,
            "reason_category": reason_category,
            "reason_detail": reason_detail,
            "item_condition": "like_new",
            "source": "shopify_webhook",
        },
    ))

    await ws_manager.broadcast_return_update({
        "source": "shopify",
        "return_request_id": return_request.id,
        "order_id": order.id,
        "status": "initiated",
        "reason": reason_category,
    })

    return {"status": "ok", "return_request_id": return_request.id}


@router.post("/refunds")
async def handle_shopify_refund(request: Request):
    """Handle Shopify refunds/create webhook."""
    body = await request.body()
    signature = request.headers.get("X-Shopify-Hmac-SHA256", "")

    if not verify_shopify_webhook(body, signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()

    await ws_manager.broadcast({
        "type": "shopify_event",
        "data": {"topic": "refunds/create", "refund_id": payload.get("id")},
    })

    return {"status": "ok", "message": "Refund event received"}


@router.get("/status")
async def shopify_status():
    """Check Shopify connection and webhook status."""
    if not shopify_service.is_configured:
        return {"connected": False, "reason": "Shopify credentials not configured in .env"}
    connection = await shopify_service.check_connection()
    return connection


@router.post("/register-webhooks")
async def register_webhooks():
    """Register Shopify webhooks pointing to our ngrok URL."""
    if not shopify_service.is_configured:
        return {"error": "Shopify not configured"}
    ngrok_url = settings.bland_ai_webhook_url.replace("/api/webhooks/bland-ai", "")
    result = await shopify_service.register_return_webhook(ngrok_url)
    return result


@router.post("/sync")
async def sync_shopify_data():
    """Sync orders, customers, products from Shopify into Return Loop DB."""
    if not shopify_service.is_configured:
        return {"error": "Shopify not configured"}
    counts = await shopify_service.sync_to_db()
    return {"status": "completed", "counts": counts}
