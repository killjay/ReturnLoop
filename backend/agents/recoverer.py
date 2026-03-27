"""Recoverer Agent - Value maximization for non-routable returns.

When an item can't be rerouted to another customer, the Recoverer
decides the best way to maximize its recovery value:
refurbish, discount sell, donate, or liquidate.
"""
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base_agent import BaseAgent
from backend.models.return_request import ReturnRequest
from backend.models.product import Product
from backend.api.ws import ws_manager


class RecovererAgent(BaseAgent):
    name = "recoverer"
    description = "Value maximization for non-routable returns"

    async def process(self, return_request_id: str, payload: dict) -> dict:
        """Determine the best recovery strategy for a returned item.

        Steps:
        1. Assess item condition
        2. Check demand for this product
        3. Evaluate recovery options
        4. Make decision
        """
        self.reset_steps()

        item_condition = payload.get("item_condition", "like_new")
        product_id = payload.get("product_id", "")

        # Get product details
        prod_result = await self.db.execute(
            select(Product).where(Product.id == product_id)
        )
        product = prod_result.scalar_one_or_none()

        product_name = product.name if product else "Unknown product"
        product_price = product.price if product else 0
        trending = product.trending if product else False

        # Step 1: Assess condition
        condition_scores = {
            "new": 1.0, "like_new": 0.9, "good": 0.7, "fair": 0.5, "poor": 0.2
        }
        condition_value = condition_scores.get(item_condition, 0.5)

        await self.think(
            return_request_id=return_request_id,
            action="assess_condition",
            reasoning=f"Item condition: {item_condition} (value retention: {condition_value*100:.0f}%). Product: {product_name}, original price: ${product_price:.2f}.",
            data_used={"condition": item_condition, "value_retention": condition_value},
            confidence=0.90,
        )

        # Step 2: Check demand
        demand_level = "high" if trending else "moderate"
        await self.think(
            return_request_id=return_request_id,
            action="check_demand",
            reasoning=f"Demand for {product_name}: {demand_level}. {'Currently trending.' if trending else 'Standard demand levels.'}",
            data_used={"trending": trending, "demand_level": demand_level},
            confidence=0.85,
        )

        # Step 3: Decide recovery strategy
        recovery_price = round(product_price * condition_value, 2)

        if condition_value >= 0.9 and trending:
            strategy = "resell_full"
            recovery_price = round(product_price * 0.90, 2)
            reasoning = f"Item is in excellent condition and trending. Resell at 90% price (${recovery_price})."
        elif condition_value >= 0.7:
            strategy = "resell_discounted"
            recovery_price = round(product_price * condition_value * 0.85, 2)
            reasoning = f"Good condition. List on second-life storefront at ${recovery_price} ({condition_value*85:.0f}% of original)."
        elif condition_value >= 0.5:
            strategy = "refurbish"
            refurb_cost = 3.00
            recovery_price = round(product_price * 0.7 - refurb_cost, 2)
            reasoning = f"Fair condition. Refurbish (cost: ${refurb_cost}) then resell at ${recovery_price}."
        else:
            strategy = "liquidate"
            recovery_price = round(product_price * 0.15, 2)
            reasoning = f"Poor condition. Liquidate to bulk buyer at ${recovery_price}."

        await self.think(
            return_request_id=return_request_id,
            action="recovery_decision",
            reasoning=reasoning,
            decision=f"{strategy} at ${recovery_price}",
            data_used={
                "strategy": strategy,
                "recovery_price": recovery_price,
                "original_price": product_price,
                "recovery_rate": round(recovery_price / max(product_price, 1) * 100, 1),
            },
            confidence=0.88,
        )

        return {
            "strategy": strategy,
            "recovery_price": recovery_price,
            "original_price": product_price,
        }
