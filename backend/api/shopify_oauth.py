"""Shopify OAuth flow for the 'Return Stuff' Partner app.

This gets us an OAuth access token with full Protected Customer Data access,
bypassing the PII redaction that static custom app tokens have on dev stores.

Flow:
1. GET /shopify/auth?shop=xxx.myshopify.com → redirect to Shopify consent
2. Shopify redirects back to GET /shopify/auth/callback?code=xxx
3. We exchange the code for an access token
4. Token is stored and used for all Shopify API calls
5. Webhooks are auto-registered after successful auth
"""
import hashlib
import hmac
import json
import secrets
import subprocess
import asyncio
from urllib.parse import urlencode, parse_qs, urlparse

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse

from backend.config import get_settings
from backend.api.ws import ws_manager

settings = get_settings()
router = APIRouter()

# In-memory storage for OAuth state and tokens
_oauth_states = {}  # state → shop mapping (CSRF protection)
_oauth_tokens = {}  # shop → access_token mapping

# Persist tokens to file so they survive restarts
import os as _os
_TOKEN_FILE = _os.path.join(_os.path.dirname(__file__), "..", "..", ".shopify_oauth_tokens.json")

def _load_tokens():
    """Load persisted OAuth tokens from file."""
    import json as _j
    try:
        with open(_TOKEN_FILE) as f:
            tokens = _j.load(f)
            _oauth_tokens.update(tokens)
            if tokens:
                print(f"  Loaded {len(tokens)} persisted Shopify OAuth token(s)")
    except (FileNotFoundError, Exception):
        pass

def _save_tokens():
    """Persist OAuth tokens to file."""
    import json as _j
    try:
        with open(_TOKEN_FILE, "w") as f:
            _j.dump(_oauth_tokens, f)
    except Exception as e:
        print(f"  Failed to save OAuth tokens: {e}")

# Load tokens on import
_load_tokens()

SCOPES = "read_orders,read_customers,write_orders,read_products,read_returns,write_returns"


def get_oauth_token(shop: str = None) -> str:
    """Get the OAuth token for a shop. Returns empty string if not authenticated."""
    if shop:
        return _oauth_tokens.get(shop, "")
    # Return any stored token
    for token in _oauth_tokens.values():
        return token
    return ""


def get_ngrok_url() -> str:
    """Extract ngrok base URL from the Bland AI webhook URL."""
    webhook_url = settings.bland_ai_webhook_url
    if "ngrok" in webhook_url:
        # e.g. https://74af-47-141-252-105.ngrok-free.app/api/webhooks/bland-ai
        parts = urlparse(webhook_url)
        return f"{parts.scheme}://{parts.netloc}"
    return "http://localhost:8000"


@router.get("/auth")
async def shopify_auth(shop: str):
    """Step 1: Redirect merchant to Shopify OAuth consent screen."""
    if not settings.shopify_oauth_client_id:
        raise HTTPException(status_code=500, detail="SHOPIFY_OAUTH_CLIENT_ID not configured")

    # Generate random state for CSRF protection
    state = secrets.token_hex(16)
    _oauth_states[state] = shop

    ngrok_url = get_ngrok_url()
    redirect_uri = f"{ngrok_url}/shopify/auth/callback"

    params = urlencode({
        "client_id": settings.shopify_oauth_client_id,
        "scope": SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
    })

    auth_url = f"https://{shop}/admin/oauth/authorize?{params}"
    return RedirectResponse(url=auth_url)


@router.get("/auth/callback")
async def shopify_auth_callback(request: Request):
    """Step 2: Handle OAuth callback, exchange code for access token."""
    params = dict(request.query_params)
    code = params.get("code", "")
    state = params.get("state", "")
    shop = params.get("shop", "")
    hmac_param = params.get("hmac", "")

    # Validate state (CSRF protection)
    if state not in _oauth_states:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    expected_shop = _oauth_states.pop(state)

    # Validate HMAC
    if hmac_param and settings.shopify_oauth_client_secret:
        # Build the message from all params except hmac
        message_params = {k: v for k, v in sorted(params.items()) if k != "hmac"}
        message = urlencode(message_params)
        computed = hmac.new(
            settings.shopify_oauth_client_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(computed, hmac_param):
            raise HTTPException(status_code=400, detail="Invalid HMAC signature")

    # Exchange authorization code for access token (via curl for TLS compat)
    token_url = f"https://{shop}/admin/oauth/access_token"
    payload = json.dumps({
        "client_id": settings.shopify_oauth_client_id,
        "client_secret": settings.shopify_oauth_client_secret,
        "code": code,
    })

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    "curl", "-s", "-X", "POST", token_url,
                    "-H", "Content-Type: application/json",
                    "-d", payload,
                ],
                capture_output=True, text=True, timeout=30,
            )
        )

        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=f"Token exchange failed: {result.stderr}")

        data = json.loads(result.stdout)
        access_token = data.get("access_token", "")

        if not access_token:
            raise HTTPException(status_code=500, detail=f"No access token in response: {data}")

        # Store the token (in memory + persist to file)
        _oauth_tokens[shop] = access_token
        _save_tokens()
        print(f"\n{'='*60}")
        print(f"SHOPIFY OAUTH SUCCESS!")
        print(f"Shop: {shop}")
        print(f"Token: {access_token[:20]}...")
        print(f"Scopes: {data.get('scope', '')}")
        print(f"{'='*60}\n")

        # Also update the shopify_client to use this token
        from backend.services.shopify_client import shopify_service
        shopify_service.api_token = access_token
        shopify_service.store_url = shop

        # Auto-register webhooks
        await _register_webhooks(shop, access_token)

        # Broadcast to dashboard
        await ws_manager.broadcast({
            "type": "shopify_oauth",
            "data": {"status": "authenticated", "shop": shop},
        })

        return HTMLResponse(f"""
        <html>
        <body style="background:#111;color:#fff;font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
            <div style="text-align:center">
                <h1 style="color:#10b981">Return Stuff Connected!</h1>
                <p>Shop: {shop}</p>
                <p>Scopes: {data.get('scope', '')}</p>
                <p style="color:#6b7280;margin-top:20px">You can close this tab. The Return Loop dashboard is now connected to your store.</p>
                <a href="{settings.frontend_url}" style="color:#10b981;margin-top:10px;display:inline-block">Go to Dashboard</a>
            </div>
        </body>
        </html>
        """)

    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"Invalid response from Shopify: {result.stdout[:200]}")


async def _register_webhooks(shop: str, token: str):
    """Register all webhooks after successful OAuth."""
    ngrok_url = get_ngrok_url()
    topics = [
        "orders/updated",
        "customers/create",
        "customers/update",
    ]

    for topic in topics:
        webhook_url = f"{ngrok_url}/api/webhooks/shopify/returns"
        payload = json.dumps({
            "webhook": {
                "topic": topic,
                "address": webhook_url,
                "format": "json",
            }
        })

        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda t=topic, p=payload: subprocess.run(
                    [
                        "curl", "-s", "-X", "POST",
                        f"https://{shop}/admin/api/2024-10/webhooks.json",
                        "-H", f"X-Shopify-Access-Token: {token}",
                        "-H", "Content-Type: application/json",
                        "-d", p,
                    ],
                    capture_output=True, text=True, timeout=15,
                )
            )
            print(f"  Registered webhook: {t}")
        except Exception as e:
            print(f"  Failed to register webhook {topic}: {e}")


@router.get("/status")
async def oauth_status():
    """Check OAuth status -- is a token stored?"""
    has_token = bool(_oauth_tokens)
    shops = list(_oauth_tokens.keys())
    return {
        "authenticated": has_token,
        "shops": shops,
        "client_id_configured": bool(settings.shopify_oauth_client_id),
    }


@router.get("/test-pii")
async def test_pii():
    """Test if the OAuth token gives us unredacted customer PII."""
    token = get_oauth_token()
    if not token:
        return {"error": "No OAuth token. Visit /shopify/auth?shop=your-store.myshopify.com first"}

    shop = list(_oauth_tokens.keys())[0]

    # Fetch customers with the OAuth token
    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [
                    "curl", "-s",
                    f"https://{shop}/admin/api/2024-10/customers.json?limit=3",
                    "-H", f"X-Shopify-Access-Token: {token}",
                ],
                capture_output=True, text=True, timeout=15,
            )
        )
        data = json.loads(result.stdout)
        customers = data.get("customers", [])

        results = []
        for c in customers:
            results.append({
                "id": c.get("id"),
                "first_name": c.get("first_name"),
                "last_name": c.get("last_name"),
                "email": c.get("email"),
                "phone": c.get("phone"),
                "address": (c.get("default_address") or {}).get("address1"),
                "city": (c.get("default_address") or {}).get("city"),
                "pii_visible": bool(c.get("first_name") or c.get("email") or c.get("phone")),
            })

        return {
            "shop": shop,
            "customers_checked": len(results),
            "pii_visible": any(r["pii_visible"] for r in results),
            "customers": results,
        }
    except Exception as e:
        return {"error": str(e)}
