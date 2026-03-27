"""Loop Matcher Agent - Intelligent return rerouting.

Finds the smartest destination for a return: nearby customer who
ordered the same SKU, instead of shipping back to warehouse.
Evaluates recipient return risk, cost, distance, and carbon impact.
"""
import json
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base_agent import BaseAgent
from backend.services.aerospike_client import aerospike_client
from backend.services.airbyte_client import airbyte_service
from backend.services.overmind_client import overmind_client
from backend.models.return_request import ReturnRequest
from backend.models.routing_decision import RoutingDecision
from backend.models.order import Order
from backend.models.customer import Customer
from backend.utils.geo import haversine_distance, calculate_distance_saved
from backend.utils.cost import calculate_cost_savings
from backend.api.ws import ws_manager


# Central US warehouse (geographic center)
WAREHOUSE_LAT = 39.8283
WAREHOUSE_LON = -98.5795


class LoopMatcherAgent(BaseAgent):
    name = "loop_matcher"
    description = "Intelligent return rerouting agent"

    async def process(self, return_request_id: str, payload: dict) -> dict:
        """Find the optimal rerouting destination for a return.

        Steps:
        1. Query nearby orders for same SKU (geospatial search)
        2. Evaluate each candidate (distance, cost, CO2, recipient risk)
        3. Make routing decision via LLM reasoning
        4. Create routing decision record
        5. Update metrics
        """
        self.reset_steps()

        source_lat = payload.get("latitude", 0)
        source_lon = payload.get("longitude", 0)
        product_sku = payload.get("product_sku", "")
        product_name = payload.get("product_name", "")
        size = payload.get("size", "")
        customer_id = payload.get("customer_id", "")
        customer_name = payload.get("customer_name", "Customer")
        order_id = payload.get("order_id", "")

        # Step 1: Search for nearby matching orders
        await self.think(
            return_request_id=return_request_id,
            action="search_nearby_orders",
            reasoning=f"Finding nearby customers who ordered '{product_name}' (SKU: {product_sku}, size: {size}) within 2500 miles of {customer_name}'s location.",
            data_used={"customer_name": customer_name, "order_id": order_id, "product_name": product_name, "sku": product_sku, "size": size, "radius_miles": 2500},
            confidence=1.0,
        )

        # Query Aerospike (or in-memory fallback)
        nearby_orders = await aerospike_client.find_nearby_orders(
            product_sku=product_sku,
            size=size,
            latitude=source_lat,
            longitude=source_lon,
            radius_miles=2500.0,
            exclude_customer_id=customer_id,
        )

        # If no nearby orders, fall back to DB
        if not nearby_orders:
            nearby_orders = await self._fallback_db_search(
                product_sku, size, source_lat, source_lon, customer_id
            )

        match_count = len(nearby_orders)
        await self.think(
            return_request_id=return_request_id,
            action="matches_found",
            reasoning=f"Found {match_count} matching orders within 50 miles." + (
                " Evaluating candidates..." if match_count > 0 else " No nearby matches -- will route to warehouse."
            ),
            decision=f"{match_count} candidates found",
            data_used={"match_count": match_count},
            confidence=0.95,
        )

        if match_count == 0:
            return await self._route_to_warehouse(return_request_id, payload)

        # Step 2: Evaluate each candidate
        candidates = []
        for order_data in nearby_orders[:5]:  # Evaluate top 5 closest
            candidate = await self._evaluate_candidate(
                return_request_id, order_data, source_lat, source_lon
            )
            candidates.append(candidate)

        # Step 3: Select best candidate
        best = await self._select_best_candidate(return_request_id, candidates, payload)

        if best is None:
            return await self._route_to_warehouse(return_request_id, payload)

        # Step 4: Create routing decision
        savings = calculate_distance_saved(
            source_lat, source_lon,
            best["latitude"], best["longitude"],
            WAREHOUSE_LAT, WAREHOUSE_LON,
        )
        cost_data = calculate_cost_savings(savings["direct_miles"], savings["warehouse_miles"])

        decision = RoutingDecision(
            return_request_id=return_request_id,
            decision_type="reroute_to_customer",
            target_order_id=best["order_id"],
            target_customer_id=best["customer_id"],
            source_latitude=source_lat,
            source_longitude=source_lon,
            target_latitude=best["latitude"],
            target_longitude=best["longitude"],
            warehouse_latitude=WAREHOUSE_LAT,
            warehouse_longitude=WAREHOUSE_LON,
            distance_saved_miles=savings["miles_saved"],
            cost_saved_usd=cost_data["cost_saved"],
            co2_saved_kg=savings["co2_saved_kg"],
            warehouse_route_cost=cost_data["warehouse_cost"],
            actual_route_cost=cost_data["direct_cost"],
            recipient_risk_score=best.get("risk_score", 0),
            reasoning=best.get("reasoning", ""),
        )
        self.db.add(decision)

        # Update return request with savings
        result = await self.db.execute(
            select(ReturnRequest).where(ReturnRequest.id == return_request_id)
        )
        return_req = result.scalar_one_or_none()
        if return_req:
            return_req.status = "rerouted"
            return_req.cost_saved = cost_data["cost_saved"]
            return_req.miles_saved = savings["miles_saved"]
            return_req.co2_saved_kg = savings["co2_saved_kg"]
            return_req.resolution = f"Rerouted to customer {best['customer_name']} ({savings['direct_miles']} mi). Saved ${cost_data['cost_saved']}, {savings['miles_saved']} miles, {savings['co2_saved_kg']}kg CO2."

        await self.db.commit()

        # Step 5: Fulfill the target order in Shopify via Airbyte
        fulfillment_result = await airbyte_service.fulfill_rerouted_order(
            target_order_id=best["order_id"],
            return_request_id=return_request_id,
        )
        await self.think(
            return_request_id=return_request_id,
            action="shopify_fulfillment",
            reasoning=f"Triggered Shopify fulfillment for reroute target order {best['order_id']}. Result: {fulfillment_result}",
            decision="fulfilled" if "error" not in fulfillment_result else f"fulfillment_skipped: {fulfillment_result.get('error')}",
            data_used={"target_order_id": best["order_id"], "fulfillment": fulfillment_result},
            confidence=0.95,
        )

        # Step 6: Log final decision
        await self.think(
            return_request_id=return_request_id,
            action="routing_decided",
            reasoning=f"Rerouting to {best['customer_name']} ({best['city']}). Distance: {savings['direct_miles']}mi (vs {savings['warehouse_miles']}mi via warehouse). Saving ${cost_data['cost_saved']}, {savings['miles_saved']} miles, {savings['co2_saved_kg']}kg CO2.",
            decision=f"REROUTE to {best['customer_name']}",
            data_used={
                "target_customer": best["customer_name"],
                "target_city": best["city"],
                "direct_miles": savings["direct_miles"],
                "warehouse_miles": savings["warehouse_miles"],
                "miles_saved": savings["miles_saved"],
                "cost_saved": cost_data["cost_saved"],
                "co2_saved_kg": savings["co2_saved_kg"],
                "recipient_risk": best.get("risk_score", 0),
            },
            confidence=0.92,
        )

        # Broadcast metrics update
        await ws_manager.broadcast_metrics_update({
            "cost_saved": cost_data["cost_saved"],
            "miles_saved": savings["miles_saved"],
            "co2_saved_kg": savings["co2_saved_kg"],
            "decision_type": "reroute_to_customer",
            "return_request_id": return_request_id,
        })

        # Broadcast routing for map
        await ws_manager.broadcast_return_update({
            "return_request_id": return_request_id,
            "status": "rerouted",
            "source": {"lat": source_lat, "lon": source_lon},
            "target": {"lat": best["latitude"], "lon": best["longitude"], "name": best["customer_name"]},
            "warehouse": {"lat": WAREHOUSE_LAT, "lon": WAREHOUSE_LON},
            "savings": savings,
        })

        # Log to Overmind
        await overmind_client.log_decision("loop_matcher", {
            "return_request_id": return_request_id,
            "decision": "reroute",
            "savings": savings,
            "cost_data": cost_data,
        })

        return {
            "decision": "reroute_to_customer",
            "target_customer": best["customer_name"],
            "savings": savings,
            "cost_data": cost_data,
        }

    async def _evaluate_candidate(
        self, return_request_id: str, order_data: dict,
        source_lat: float, source_lon: float,
    ) -> dict:
        """Evaluate a single candidate for rerouting."""
        order_id = order_data.get("order_id", "")
        customer_id = order_data.get("customer_id", "")

        # Get customer details from DB
        result = await self.db.execute(
            select(Customer).where(Customer.id == customer_id)
        )
        customer = result.scalar_one_or_none()

        customer_name = customer.name if customer else "Unknown"
        city = customer.city if customer else "Unknown"
        risk_score = customer.risk_score if customer else 0.5
        return_rate = customer.return_rate if customer else 0.5

        target_lat = order_data.get("latitude", 0)
        target_lon = order_data.get("longitude", 0)
        distance = haversine_distance(source_lat, source_lon, target_lat, target_lon)

        # Log evaluation
        risk_emoji = "pass" if risk_score < 0.3 else ("caution" if risk_score < 0.5 else "reject")
        await self.think(
            return_request_id=return_request_id,
            action="evaluate_candidate",
            reasoning=f"Candidate: {customer_name} ({city}), {distance:.0f} mi away. Return risk: {risk_score*100:.0f}% [{risk_emoji}]. Return rate: {return_rate*100:.0f}%.",
            decision=risk_emoji,
            data_used={
                "customer_name": customer_name,
                "city": city,
                "distance_miles": round(distance, 1),
                "risk_score": risk_score,
                "return_rate": return_rate,
            },
            confidence=0.90,
        )

        return {
            "order_id": order_id,
            "customer_id": customer_id,
            "customer_name": customer_name,
            "city": city,
            "latitude": target_lat,
            "longitude": target_lon,
            "distance_miles": round(distance, 1),
            "risk_score": risk_score,
            "return_rate": return_rate,
        }

    async def _select_best_candidate(
        self, return_request_id: str, candidates: list, payload: dict
    ) -> dict:
        """Select the best candidate based on multi-factor reasoning."""
        # Filter out high-risk candidates
        viable = [c for c in candidates if c["risk_score"] < 0.5]

        if not viable:
            await self.think(
                return_request_id=return_request_id,
                action="no_viable_candidates",
                reasoning="All nearby candidates have high return risk (>50%). Routing to warehouse instead.",
                decision="warehouse",
                confidence=0.85,
            )
            return None

        # Score candidates: lower distance + lower risk = better
        for c in viable:
            c["score"] = (c["distance_miles"] * 0.4) + (c["risk_score"] * 100 * 0.6)

        viable.sort(key=lambda x: x["score"])
        best = viable[0]

        # Use LLM for final reasoning
        routing_response = await self.reason(
            system_prompt="You are the Loop Matcher agent in Return Loop. Make a routing decision.",
            user_prompt=f"""Candidates for rerouting:
{json.dumps(viable[:3], indent=2)}

Select the best candidate and explain why. Consider distance, recipient risk, and overall reliability."""
        )

        best["reasoning"] = routing_response
        return best

    async def _route_to_warehouse(self, return_request_id: str, payload: dict) -> dict:
        """Fall back to warehouse routing when no reroute is possible."""
        source_lat = payload.get("latitude", 0)
        source_lon = payload.get("longitude", 0)
        warehouse_dist = haversine_distance(source_lat, source_lon, WAREHOUSE_LAT, WAREHOUSE_LON)

        await self.think(
            return_request_id=return_request_id,
            action="route_to_warehouse",
            reasoning=f"No viable reroute candidates. Routing to central warehouse ({warehouse_dist:.0f} miles).",
            decision="WAREHOUSE",
            data_used={"warehouse_distance": round(warehouse_dist, 1)},
            confidence=0.95,
        )

        decision = RoutingDecision(
            return_request_id=return_request_id,
            decision_type="warehouse",
            source_latitude=source_lat,
            source_longitude=source_lon,
            target_latitude=WAREHOUSE_LAT,
            target_longitude=WAREHOUSE_LON,
            warehouse_latitude=WAREHOUSE_LAT,
            warehouse_longitude=WAREHOUSE_LON,
            reasoning="No nearby matching orders. Standard warehouse routing.",
        )
        self.db.add(decision)

        result = await self.db.execute(
            select(ReturnRequest).where(ReturnRequest.id == return_request_id)
        )
        return_req = result.scalar_one_or_none()
        if return_req:
            return_req.status = "warehouse"
            return_req.resolution = f"Routed to central warehouse ({warehouse_dist:.0f} miles)."
        await self.db.commit()

        return {"decision": "warehouse", "savings": {"miles_saved": 0, "cost_saved": 0}}

    async def _fallback_db_search(
        self, product_sku: str, size: str,
        lat: float, lon: float, exclude_customer_id: str
    ) -> list:
        """Fall back to DB search if Aerospike has no data."""
        from backend.models.product import Product

        # Find product by SKU
        prod_result = await self.db.execute(
            select(Product).where(Product.sku == product_sku)
        )
        product = prod_result.scalar_one_or_none()
        if not product:
            return []

        # Find matching orders
        result = await self.db.execute(
            select(Order).where(
                Order.product_id == product.id,
                Order.size == size,
                Order.status.in_(["pending", "shipped"]),
                Order.customer_id != exclude_customer_id,
            )
        )
        orders = result.scalars().all()

        nearby = []
        for order in orders:
            dist = haversine_distance(lat, lon, order.latitude, order.longitude)
            if dist <= 2500:
                nearby.append({
                    "order_id": order.id,
                    "customer_id": order.customer_id,
                    "product_sku": product_sku,
                    "size": size,
                    "latitude": order.latitude,
                    "longitude": order.longitude,
                    "status": order.status,
                    "distance_miles": round(dist, 1),
                })

        nearby.sort(key=lambda x: x["distance_miles"])
        return nearby
