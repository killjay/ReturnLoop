"""Whisperer Agent - Voice intake & negotiation via Bland AI.

Handles return calls, deeply understands why, and negotiates
the optimal outcome: exchange, keep+refund, discount, or full return.

When Bland AI API key is configured, makes a REAL phone call and
waits for the result via webhook. Otherwise, simulates the outcome.
"""
import json
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.agents.base_agent import BaseAgent
from backend.services.bland_ai import bland_ai_service
from backend.services.claude_client import claude_client
from backend.models.return_request import ReturnRequest
from backend.models.customer import Customer
from backend.models.product import Product
from backend.api.ws import ws_manager


class WhispererAgent(BaseAgent):
    name = "whisperer"
    description = "Voice intake & negotiation agent"

    async def process(self, return_request_id: str, payload: dict) -> dict:
        """Process a return request through voice negotiation.

        Steps:
        1. Pull full customer context
        2. Analyze return reason + customer value
        3. Determine negotiation strategy
        4. Initiate voice call via Bland AI
        5. Wait for real call result (webhook) or simulate
        """
        self.reset_steps()

        # Step 1: Pull customer context
        await self.think(
            return_request_id=return_request_id,
            action="pull_customer_context",
            reasoning=f"Loading customer profile for {payload.get('customer_name', 'unknown')}",
            data_used={"customer_id": payload.get("customer_id")},
            confidence=1.0,
        )

        customer_ltv = payload.get("customer_ltv", 0)
        customer_return_rate = payload.get("customer_return_rate", 0)
        product_price = payload.get("product_price", 0)
        product_return_rate = payload.get("product_return_rate", 0)

        # Step 2: Analyze customer history
        ltv_tier = "high-value" if customer_ltv > 1500 else "standard"
        await self.think(
            return_request_id=return_request_id,
            action="analyze_customer_history",
            reasoning=f"Customer LTV: ${customer_ltv:.2f} ({ltv_tier}). Return rate: {customer_return_rate*100:.0f}%. Product return rate: {product_return_rate*100:.0f}%.",
            decision=f"Customer tier: {ltv_tier}",
            data_used={
                "lifetime_value": customer_ltv,
                "return_rate": customer_return_rate,
                "product_return_rate": product_return_rate,
            },
            confidence=0.95,
        )

        # Step 3: Check product reviews for patterns
        reason_category = payload.get("reason_category", "preference")
        common_reasons = []
        result = await self.db.execute(
            select(Product).where(Product.id == payload.get("product_id"))
        )
        product = result.scalar_one_or_none()
        if product:
            common_reasons = product.common_return_reasons or []

        sizing_complaint_pct = common_reasons.count("sizing") / max(len(common_reasons), 1) * 100

        await self.think(
            return_request_id=return_request_id,
            action="check_product_reviews",
            reasoning=f"Product '{payload.get('product_name')}' has {product_return_rate*100:.0f}% return rate. {sizing_complaint_pct:.0f}% of returns cite sizing issues. Size chart accuracy: {product.size_chart_accuracy*100:.0f}%." if product else "Product data unavailable.",
            data_used={
                "product_return_rate": product_return_rate,
                "sizing_complaint_pct": sizing_complaint_pct,
                "size_chart_accuracy": product.size_chart_accuracy if product else None,
            },
            confidence=0.90,
        )

        # Step 4: Determine negotiation strategy via LLM
        strategy_response = await self.reason(
            system_prompt="You are the Whisperer agent in Return Loop. Determine the best negotiation strategy.",
            user_prompt=f"""Customer: {payload.get('customer_name')}
LTV: ${customer_ltv:.2f} ({ltv_tier})
Product: {payload.get('product_name')} (${product_price:.2f})
Return reason: {reason_category} - {payload.get('reason_detail', '')}
Product return rate: {product_return_rate*100:.0f}%
Sizing complaints: {sizing_complaint_pct:.0f}%

What negotiation strategy should I use?"""
        )

        try:
            strategy_data = json.loads(strategy_response)
        except json.JSONDecodeError:
            strategy_data = {"strategy": "exchange_and_keep", "reasoning": strategy_response, "confidence": 0.80}

        strategy = strategy_data.get("strategy", "exchange_and_keep")
        await self.think(
            return_request_id=return_request_id,
            action="determine_strategy",
            reasoning=strategy_data.get("reasoning", "Analyzing optimal negotiation strategy..."),
            decision=f"Strategy: {strategy}",
            data_used=strategy_data,
            confidence=strategy_data.get("confidence", 0.85),
        )

        # Step 5: Initiate Bland AI call
        customer_phone = payload.get("customer_phone", "")
        if not customer_phone or len(customer_phone) < 5:
            print(f"WARNING: No valid phone number for customer. Phone='{customer_phone}'. Skipping real call.")
            await self.think(
                return_request_id=return_request_id,
                action="no_phone_number",
                reasoning=f"No valid phone number available for {payload.get('customer_name')}. Proceeding with simulated negotiation.",
                data_used={"phone": customer_phone},
                confidence=0.50,
            )
            customer_phone = ""  # Force mock path

        await self.think(
            return_request_id=return_request_id,
            action="initiate_voice_call",
            reasoning=f"Initiating voice call to {payload.get('customer_name')} at {customer_phone or 'N/A'}. Strategy: {strategy}.",
            data_used={"phone": customer_phone, "strategy": strategy},
            confidence=0.95,
        )

        call_result = await bland_ai_service.initiate_call(
            phone=customer_phone,
            context={
                "return_request_id": return_request_id,
                "order_id": payload.get("order_id"),
                "customer_id": payload.get("customer_id"),
                "customer_name": payload.get("customer_name"),
                "customer_ltv": customer_ltv,
                "product_name": payload.get("product_name"),
                "product_price": product_price,
                "reason_category": reason_category,
            },
        )

        call_id = call_result.get("call_id", "")
        is_real_call = call_result.get("is_real", False)

        # Broadcast voice call status
        await ws_manager.broadcast_voice_update({
            "status": "active",
            "call_id": call_id,
            "customer_name": payload.get("customer_name"),
            "product_name": payload.get("product_name"),
            "strategy": strategy,
            "is_real": is_real_call,
        })

        # Step 6: Get negotiation outcome
        if is_real_call:
            # REAL CALL: Wait for Bland AI webhook to deliver the result
            await self.think(
                return_request_id=return_request_id,
                action="waiting_for_call",
                reasoning=f"Real Bland AI call initiated (ID: {call_id}). Waiting for call to complete and webhook to deliver results...",
                data_used={"call_id": call_id, "is_real": True},
                confidence=0.95,
            )

            # Wait up to 2 minutes for the call to complete
            call_outcome = await bland_ai_service.wait_for_call_result(call_id, timeout=120.0)

            if call_outcome:
                negotiation_outcome = call_outcome.get("outcome", "full_return")
                prevented = call_outcome.get("prevented", False)
                suggested_response = call_outcome.get("summary", "") or call_outcome.get("transcript", "")[:200]
                duration = call_outcome.get("duration", 0)
                source = call_outcome.get("source", "unknown")

                await self.think(
                    return_request_id=return_request_id,
                    action="negotiation_result",
                    reasoning=f"Bland AI call completed ({duration:.0f}s, via {source}). Outcome: {negotiation_outcome}. {'Customer accepted alternative -- return prevented!' if prevented else 'Customer prefers full return -- proceeding to Loop Matcher for smart rerouting.'}",
                    decision=negotiation_outcome,
                    data_used={
                        "outcome": negotiation_outcome,
                        "prevented": prevented,
                        "call_id": call_id,
                        "duration": duration,
                        "source": source,
                        "transcript_preview": suggested_response[:100],
                    },
                    confidence=0.92,
                )
            else:
                # Call timed out or failed -- default to full return
                negotiation_outcome = "full_return"
                prevented = False
                suggested_response = "Call timed out -- defaulting to standard return processing."

                await self.think(
                    return_request_id=return_request_id,
                    action="negotiation_timeout",
                    reasoning="Bland AI call did not complete within timeout. Proceeding with standard return flow.",
                    decision="full_return (timeout)",
                    data_used={"call_id": call_id, "timeout": True},
                    confidence=0.70,
                )
        else:
            # MOCK CALL: Simulate outcome for demo
            await asyncio.sleep(1)

            prevented = False
            negotiation_outcome = "full_return"

            if strategy in ("exchange_and_keep", "exchange"):
                if customer_ltv > 2000 or product_price < 60:
                    negotiation_outcome = "keep_with_refund"
                    prevented = True
                else:
                    negotiation_outcome = "full_return"
            elif strategy == "discount_keep":
                negotiation_outcome = "full_return"

            suggested_response = strategy_data.get("suggested_response", "")

            await self.think(
                return_request_id=return_request_id,
                action="negotiation_result",
                reasoning=f"Negotiation outcome: {negotiation_outcome}. {'Customer accepted exchange offer.' if prevented else 'Customer prefers full return -- proceeding to Loop Matcher for smart rerouting.'}",
                decision=negotiation_outcome,
                data_used={
                    "outcome": negotiation_outcome,
                    "prevented": prevented,
                    "call_id": call_id,
                    "suggested_response": suggested_response,
                    "mode": "simulated",
                },
                confidence=0.90,
            )

        # Broadcast voice call ended (for mock calls; real calls are broadcast by webhook)
        if not is_real_call:
            await ws_manager.broadcast_voice_update({
                "status": "ended",
                "call_id": call_id,
                "outcome": negotiation_outcome,
                "prevented": prevented,
                "transcript_summary": suggested_response,
            })

        # Update return request in DB (for mock calls; real calls are updated by webhook)
        if not is_real_call:
            result = await self.db.execute(
                select(ReturnRequest).where(ReturnRequest.id == return_request_id)
            )
            return_req = result.scalar_one_or_none()
            if return_req:
                return_req.negotiation_outcome = negotiation_outcome
                if prevented:
                    return_req.status = "prevented"
                    return_req.resolution = f"Return prevented via {negotiation_outcome}. {suggested_response}"
                else:
                    return_req.status = "accepted"
                await self.db.commit()

        return {
            "outcome": negotiation_outcome,
            "prevented": prevented,
            "strategy": strategy,
            "call_id": call_id,
            "is_real": is_real_call,
        }
