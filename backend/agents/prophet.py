"""Prophet Agent - Pre-return prediction and prevention.

Monitors orders and predicts which ones are likely to be returned,
then takes proactive action (sizing tips, exchange offers).
"""
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base_agent import BaseAgent
from backend.models.order import Order
from backend.models.customer import Customer
from backend.models.product import Product
from backend.api.ws import ws_manager


class ProphetAgent(BaseAgent):
    name = "prophet"
    description = "Pre-return prediction agent"

    async def scan_orders(self) -> list:
        """Scan recent orders and predict return likelihood.

        Returns list of high-risk orders with recommendations.
        """
        self.reset_steps()

        # Get recent pending/shipped orders
        result = await self.db.execute(
            select(Order).where(Order.status.in_(["pending", "shipped"])).limit(30)
        )
        orders = result.scalars().all()

        high_risk_orders = []

        for order in orders:
            # Get customer
            cust_result = await self.db.execute(
                select(Customer).where(Customer.id == order.customer_id)
            )
            customer = cust_result.scalar_one_or_none()

            # Get product
            prod_result = await self.db.execute(
                select(Product).where(Product.id == order.product_id)
            )
            product = prod_result.scalar_one_or_none()

            if not customer or not product:
                continue

            # Calculate return probability
            risk_score = self._calculate_risk(customer, product, order)

            if risk_score >= 0.6:
                high_risk_orders.append({
                    "order_id": order.id,
                    "customer_name": customer.name,
                    "product_name": product.name,
                    "size": order.size,
                    "risk_score": risk_score,
                    "reasons": self._get_risk_reasons(customer, product),
                    "recommended_action": self._recommend_action(risk_score, customer, product),
                })

        return high_risk_orders

    async def analyze_order(self, return_request_id: str, order_id: str) -> dict:
        """Analyze a specific order for return risk -- used in the pipeline."""
        self.reset_steps()

        result = await self.db.execute(select(Order).where(Order.id == order_id))
        order = result.scalar_one_or_none()
        if not order:
            return {"risk_score": 0.5, "reasoning": "Order not found"}

        cust_result = await self.db.execute(
            select(Customer).where(Customer.id == order.customer_id)
        )
        customer = cust_result.scalar_one_or_none()

        prod_result = await self.db.execute(
            select(Product).where(Product.id == order.product_id)
        )
        product = prod_result.scalar_one_or_none()

        if not customer or not product:
            return {"risk_score": 0.5}

        risk_score = self._calculate_risk(customer, product, order)

        await self.think(
            return_request_id=return_request_id,
            action="predict_return_risk",
            reasoning=f"Order #{order_id[:8]}: {product.name} size {order.size} for {customer.name}. Risk score: {risk_score*100:.0f}%.",
            decision=f"Risk: {risk_score*100:.0f}%",
            data_used={
                "customer_return_rate": customer.return_rate,
                "product_return_rate": product.return_rate,
                "size_chart_accuracy": product.size_chart_accuracy,
                "risk_score": risk_score,
            },
            confidence=risk_score,
        )

        if risk_score >= 0.7:
            reasons = self._get_risk_reasons(customer, product)
            await self.think(
                return_request_id=return_request_id,
                action="high_risk_alert",
                reasoning=f"HIGH RISK DETECTED. Reasons: {', '.join(reasons)}. Recommending proactive intervention.",
                decision=self._recommend_action(risk_score, customer, product),
                data_used={"reasons": reasons},
                confidence=risk_score,
            )

        return {
            "risk_score": risk_score,
            "reasons": self._get_risk_reasons(customer, product),
            "recommended_action": self._recommend_action(risk_score, customer, product),
        }

    def _calculate_risk(self, customer: Customer, product: Product, order: Order) -> float:
        """Calculate return probability using multiple signals."""
        score = 0.0

        # Customer return history (weight: 0.3)
        score += customer.return_rate * 0.3

        # Product return rate (weight: 0.3)
        score += product.return_rate * 0.3

        # Size chart accuracy inverse (weight: 0.25)
        score += (1 - product.size_chart_accuracy) * 0.25

        # Review rating inverse (weight: 0.15)
        rating_risk = max(0, (5 - product.avg_review_rating) / 5)
        score += rating_risk * 0.15

        return round(min(score, 0.99), 2)

    def _get_risk_reasons(self, customer: Customer, product: Product) -> list:
        reasons = []
        if product.return_rate > 0.25:
            reasons.append(f"Product has {product.return_rate*100:.0f}% return rate")
        if product.size_chart_accuracy < 0.75:
            reasons.append(f"Size chart accuracy only {product.size_chart_accuracy*100:.0f}%")
        if customer.return_rate > 0.4:
            reasons.append(f"Customer return rate: {customer.return_rate*100:.0f}%")
        if "sizing" in (product.common_return_reasons or []):
            sizing_pct = (product.common_return_reasons or []).count("sizing") / max(len(product.common_return_reasons or []), 1) * 100
            reasons.append(f"{sizing_pct:.0f}% of returns cite sizing")
        if product.avg_review_rating < 4.0:
            reasons.append(f"Low review rating: {product.avg_review_rating}")
        return reasons or ["General risk factors"]

    def _recommend_action(self, risk_score: float, customer: Customer, product: Product) -> str:
        if risk_score >= 0.8:
            return "proactive_size_suggestion"
        elif risk_score >= 0.6:
            if product.size_chart_accuracy < 0.75:
                return "send_sizing_guide"
            return "offer_exchange_before_ship"
        return "monitor"
