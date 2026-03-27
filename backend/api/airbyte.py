"""Airbyte data pipeline API endpoints.

Allows triggering data syncs from Shopify and checking sync status.
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from backend.services.airbyte_client import airbyte_service
from backend.config import get_settings

router = APIRouter()


class ShopifySyncRequest(BaseModel):
    shop_name: Optional[str] = None
    api_key: Optional[str] = None


@router.get("/status")
async def get_sync_status():
    """Get current Airbyte sync status and connected sources."""
    settings = get_settings()
    status = airbyte_service.status
    status["shopify_config"] = {
        "shop_name": settings.shopify_store_url or "",
        "has_api_key": bool(settings.shopify_api_token),
    }
    return status


@router.post("/sync-shopify")
async def sync_shopify(req: ShopifySyncRequest):
    """Trigger a Shopify data sync via the Airbyte agent connector.

    Pulls customers, products, and orders from Shopify store.
    Falls back to env vars when credentials are not provided.
    """
    settings = get_settings()
    shop_name = req.shop_name or settings.shopify_store_url
    api_key = req.api_key or settings.shopify_api_token
    result = await airbyte_service.sync_from_shopify(shop_name, api_key)
    return result


@router.post("/sync-demo")
async def sync_demo():
    """Trigger a demo sync using existing seed data.

    Simulates Airbyte pulling from Shopify -- uses seed data already in DB.
    """
    result = await airbyte_service._mock_shopify_sync()
    return result
