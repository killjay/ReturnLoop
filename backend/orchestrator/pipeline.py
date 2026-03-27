"""Return processing pipeline - orchestrates all agents via the event bus.

This is the brain that coordinates:
  RETURN_INITIATED → Whisperer → Loop Matcher → Learner
                          ↓ (if prevented)
                        Learner
"""
import asyncio
from backend.orchestrator.event_bus import (
    event_bus, Event,
    RETURN_INITIATED, NEGOTIATION_COMPLETE, RETURN_PREVENTED,
    ROUTING_DECIDED, REROUTE_IMPOSSIBLE,
)
from backend.agents.whisperer import WhispererAgent
from backend.agents.loop_matcher import LoopMatcherAgent
from backend.agents.prophet import ProphetAgent
from backend.agents.learner import LearnerAgent
from backend.agents.recoverer import RecovererAgent
from backend.db.database import async_session
from backend.services.aerospike_client import aerospike_client


class ReturnPipeline:
    """Orchestrates the multi-agent return processing pipeline."""

    def __init__(self):
        self._registered = False

    def register(self):
        """Register event handlers with the event bus."""
        if self._registered:
            return
        event_bus.subscribe(RETURN_INITIATED, self._handle_return_initiated)
        event_bus.subscribe(NEGOTIATION_COMPLETE, self._handle_negotiation_complete)
        event_bus.subscribe(RETURN_PREVENTED, self._handle_return_prevented)
        event_bus.subscribe(ROUTING_DECIDED, self._handle_routing_decided)
        event_bus.subscribe(REROUTE_IMPOSSIBLE, self._handle_reroute_impossible)
        self._registered = True

    async def _handle_return_initiated(self, event: Event):
        """Handle a new return request -- run Whisperer agent."""
        async with async_session() as db:
            # First, run Prophet to assess risk
            prophet = ProphetAgent(db)
            risk_data = await prophet.analyze_order(
                event.return_request_id,
                event.payload.get("order_id", ""),
            )

            # Then run Whisperer for negotiation
            whisperer = WhispererAgent(db)
            result = await whisperer.process(
                event.return_request_id,
                event.payload,
            )

            if result.get("prevented"):
                # Return was prevented through negotiation
                await event_bus.emit(Event(
                    event_type=RETURN_PREVENTED,
                    return_request_id=event.return_request_id,
                    payload={**event.payload, **result},
                ))
            else:
                # Return confirmed -- move to Loop Matcher
                await event_bus.emit(Event(
                    event_type=NEGOTIATION_COMPLETE,
                    return_request_id=event.return_request_id,
                    payload={**event.payload, **result},
                ))

    async def _handle_negotiation_complete(self, event: Event):
        """Return confirmed -- run Loop Matcher to find reroute."""
        async with async_session() as db:
            loop_matcher = LoopMatcherAgent(db)
            result = await loop_matcher.process(
                event.return_request_id,
                event.payload,
            )

            if result.get("decision") == "reroute_to_customer":
                await event_bus.emit(Event(
                    event_type=ROUTING_DECIDED,
                    return_request_id=event.return_request_id,
                    payload={**event.payload, **result},
                ))
            else:
                await event_bus.emit(Event(
                    event_type=REROUTE_IMPOSSIBLE,
                    return_request_id=event.return_request_id,
                    payload={**event.payload, **result},
                ))

    async def _handle_return_prevented(self, event: Event):
        """Return was prevented -- log to Learner."""
        async with async_session() as db:
            learner = LearnerAgent(db)
            await learner.analyze_return(
                event.return_request_id,
                event.payload,
            )

    async def _handle_routing_decided(self, event: Event):
        """Rerouting decided -- log to Learner for pattern analysis."""
        async with async_session() as db:
            learner = LearnerAgent(db)
            await learner.analyze_return(
                event.return_request_id,
                event.payload,
            )

    async def _handle_reroute_impossible(self, event: Event):
        """No reroute possible -- run Recoverer then Learner."""
        async with async_session() as db:
            recoverer = RecovererAgent(db)
            await recoverer.process(
                event.return_request_id,
                event.payload,
            )

            learner = LearnerAgent(db)
            await learner.analyze_return(
                event.return_request_id,
                event.payload,
            )


# Singleton
pipeline = ReturnPipeline()


async def init_pipeline():
    """Initialize the pipeline and load orders into Aerospike."""
    pipeline.register()

    # Load active orders into Aerospike for geospatial matching
    async with async_session() as db:
        from sqlalchemy import select
        from backend.models.order import Order
        from backend.models.product import Product

        result = await db.execute(
            select(Order).where(Order.status.in_(["pending", "shipped"]))
        )
        orders = result.scalars().all()

        for order in orders:
            prod_result = await db.execute(
                select(Product).where(Product.id == order.product_id)
            )
            product = prod_result.scalar_one_or_none()
            if product:
                await aerospike_client.store_active_order(order.id, {
                    "customer_id": order.customer_id,
                    "product_sku": product.sku,
                    "size": order.size,
                    "latitude": order.latitude,
                    "longitude": order.longitude,
                    "status": order.status,
                })

    print(f"Pipeline initialized. Loaded {len(orders)} active orders into cache.")
