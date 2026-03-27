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
    """Verify Shopify webhook HMAC signature."""
    if not settings.shopify_api_secret:
        return True
    computed = base64.b64encode(
        hmac.new(
            settings.shopify_api_secret.encode(),
            body,
            hashlib.sha256,
        ).digest()
    ).decode()
    return hmac.compare_digest(computed, signature)


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

    # FILTER: Only process orders that have a return requested
    # orders/updated fires on ANY change (fulfillment, payment, edit, return)
    # We only trigger when: returns[] is non-empty, or refunds exist, or cancelled
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
        if existing_return.scalar_one_or_none():
            return {"status": "skipped", "reason": "Return already exists for this order"}

        # FIX 1: Look up order by both raw ID and prefixed ID
        result = await db.execute(
            select(Order).where(
                or_(Order.id == order_id, Order.id == f"shopify-{order_id}")
            )
        )
        order = result.scalar_one_or_none()

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
        customer = cust_result.scalar_one_or_none()

        # FIX 1: Look up product by both raw ID and prefixed ID
        product_id = order.product_id or ""
        prod_result = await db.execute(
            select(Product).where(
                or_(Product.id == product_id, Product.id == f"shopify-{product_id}")
            )
        )
        product = prod_result.scalar_one_or_none()

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

        # Enrich customer data via OAuth API (bypasses PII redaction)
        enriched = await fallback_from_seed_db(customer_id, "", db)

        # Apply enriched data
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
