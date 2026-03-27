from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

from backend.db.database import get_db
from backend.models.return_request import ReturnRequest
from backend.models.order import Order
from backend.models.customer import Customer
from backend.models.product import Product
from backend.orchestrator.event_bus import event_bus, Event, RETURN_INITIATED

router = APIRouter()


class ReturnInitiateRequest(BaseModel):
    order_id: str
    reason_category: Optional[str] = ""
    reason_detail: Optional[str] = ""
    item_condition: Optional[str] = "like_new"


class ReturnResponse(BaseModel):
    id: str
    order_id: str
    customer_id: str
    product_id: str
    status: str
    reason_category: str
    reason_detail: str
    item_condition: str
    negotiation_outcome: str
    resolution: str
    cost_saved: float
    miles_saved: float
    co2_saved_kg: float
    initiated_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


@router.post("/initiate", response_model=ReturnResponse)
async def initiate_return(req: ReturnInitiateRequest, db: AsyncSession = Depends(get_db)):
    """Initiate a return request -- triggers the full agent pipeline."""
    # Get the order with customer and product
    result = await db.execute(
        select(Order)
        .options(selectinload(Order.customer), selectinload(Order.product))
        .where(Order.id == req.order_id)
    )
    order = result.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {req.order_id} not found")

    # Create return request
    return_request = ReturnRequest(
        order_id=order.id,
        customer_id=order.customer_id,
        product_id=order.product_id,
        status="initiated",
        reason_category=req.reason_category,
        reason_detail=req.reason_detail,
        item_condition=req.item_condition,
    )
    db.add(return_request)
    await db.commit()
    await db.refresh(return_request)

    # Update order status
    order.status = "return_requested"
    await db.commit()

    # Emit event to trigger the agent pipeline
    await event_bus.emit(Event(
        event_type=RETURN_INITIATED,
        return_request_id=return_request.id,
        payload={
            "order_id": order.id,
            "customer_id": order.customer_id,
            "product_id": order.product_id,
            "customer_name": order.customer.name,
            "customer_phone": order.customer.phone,
            "customer_ltv": order.customer.lifetime_value,
            "customer_return_rate": order.customer.return_rate,
            "product_name": order.product.name,
            "product_sku": order.product.sku,
            "product_price": order.product.price,
            "product_return_rate": order.product.return_rate,
            "size": order.size,
            "latitude": order.latitude,
            "longitude": order.longitude,
            "reason_category": req.reason_category,
            "reason_detail": req.reason_detail,
            "item_condition": req.item_condition,
        },
    ))

    return return_request


@router.get("/", response_model=list[ReturnResponse])
async def list_returns(
    status: Optional[str] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """List return requests with optional status filter."""
    query = select(ReturnRequest).order_by(ReturnRequest.initiated_at.desc()).limit(limit)
    if status:
        query = query.where(ReturnRequest.status == status)
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{return_id}", response_model=ReturnResponse)
async def get_return(return_id: str, db: AsyncSession = Depends(get_db)):
    """Get a specific return request with full details."""
    result = await db.execute(
        select(ReturnRequest).where(ReturnRequest.id == return_id)
    )
    return_request = result.scalar_one_or_none()
    if not return_request:
        raise HTTPException(status_code=404, detail="Return request not found")
    return return_request
