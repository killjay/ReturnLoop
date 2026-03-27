"""Bland AI voice integration for the Whisperer agent."""
import asyncio
import json
import subprocess
import httpx
from typing import Optional
from backend.config import get_settings

settings = get_settings()

BLAND_AI_BASE_URL = "https://api.bland.ai/v1"


class BlandAIService:
    def __init__(self):
        self.api_key = settings.bland_ai_api_key
        self.webhook_url = settings.bland_ai_webhook_url
        self.pathway_id = settings.bland_ai_pathway_id
        # Maps call_id → return context (return_request_id, payload, etc.)
        self._call_map: dict[str, dict] = {}
        # Maps call_id → call result (set by webhook or polling)
        self._call_results: dict[str, dict] = {}
        # Events for signaling call completion
        self._call_events: dict[str, asyncio.Event] = {}

    def register_call(self, call_id: str, context: dict):
        """Register a call_id → return context mapping so webhooks can route events."""
        self._call_map[call_id] = context
        self._call_events[call_id] = asyncio.Event()

    def get_call_context(self, call_id: str) -> Optional[dict]:
        """Look up the return context for a Bland AI call."""
        return self._call_map.get(call_id)

    def set_call_result(self, call_id: str, result: dict):
        """Set the call result (called by webhook handler) and signal completion."""
        self._call_results[call_id] = result
        event = self._call_events.get(call_id)
        if event:
            event.set()

    async def wait_for_call_result(self, call_id: str, timeout: float = 120.0) -> Optional[dict]:
        """Wait for the call to complete (via webhook or polling).

        Returns call result dict or None on timeout.
        """
        event = self._call_events.get(call_id)
        if not event:
            return None

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._call_results.get(call_id)
        except asyncio.TimeoutError:
            # Timeout -- poll Bland AI directly for status
            print(f"Call {call_id} timed out waiting for webhook, polling API...")
            return await self._poll_call_result(call_id)

    async def initiate_call(self, phone: str, context: dict) -> dict:
        """Initiate an outbound call to a customer for return negotiation.

        Returns call_id and status. Falls back to mock if API key not set.
        """
        if not self.api_key:
            return self._mock_call(phone, context)

        try:
            # Build payload -- use pathway if configured, otherwise fall back to task
            call_payload = {
                "phone_number": phone,
                "voice": "maya",
                "record": True,
                "webhook": self.webhook_url,
                "analysis_schema": {
                    "outcome": "string - one of: exchange, keep_with_refund, discount_keep, store_credit, full_return",
                    "customer_sentiment": "string - one of: happy, neutral, frustrated",
                    "return_prevented": "boolean - true if customer agreed to keep the item",
                    "summary": "string - brief summary of the conversation",
                },
                "metadata": {
                    "return_request_id": context.get("return_request_id", ""),
                    "order_id": context.get("order_id", ""),
                    "customer_id": context.get("customer_id", ""),
                },
            }

            if self.pathway_id:
                # Use Conversational Pathway (built with Norm)
                call_payload["pathway_id"] = self.pathway_id
                print(f"  BLAND AI: Using pathway {self.pathway_id}")
            else:
                # Fall back to plain text task prompt
                system_prompt = self._build_system_prompt(context)
                call_payload["task"] = system_prompt
                call_payload["first_sentence"] = f"Hi, this is Return Loop support. I can see you're calling about your order for the {context.get('product_name', 'item')}. How can I help you today?"
                call_payload["wait_for_greeting"] = False
                print(f"  BLAND AI: Using task prompt (no pathway configured)")

            payload = json.dumps(call_payload)

            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "curl", "-s", "-X", "POST",
                        f"{BLAND_AI_BASE_URL}/calls",
                        "-H", f"Authorization: {self.api_key}",
                        "-H", "Content-Type: application/json",
                        "-d", payload,
                    ],
                    capture_output=True, text=True, timeout=30,
                )
            )

            if result.returncode != 0:
                print(f"Bland AI curl error: {result.stderr}")
                return self._mock_call(phone, context)

            data = json.loads(result.stdout)

            if data.get("status") != "success":
                print(f"Bland AI API error: {data}")
                return self._mock_call(phone, context)

            call_id = data.get("call_id", "")

            # Register call for webhook routing
            if call_id:
                self.register_call(call_id, context)

            return {
                "call_id": call_id,
                "status": data.get("status", "initiated"),
                "is_real": True,
            }
        except Exception as e:
            print(f"Bland AI error, using mock: {e}")
            return self._mock_call(phone, context)

    async def get_call_status(self, call_id: str) -> dict:
        """Get the status of an ongoing or completed call."""
        if not self.api_key:
            return {"status": "completed", "duration": 45}

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "curl", "-s",
                        f"{BLAND_AI_BASE_URL}/calls/{call_id}",
                        "-H", f"Authorization: {self.api_key}",
                    ],
                    capture_output=True, text=True, timeout=10,
                )
            )
            if result.returncode == 0 and result.stdout:
                return json.loads(result.stdout)
            return {"status": "unknown"}
        except Exception as e:
            print(f"Bland AI status error: {e}")
            return {"status": "unknown"}

    async def _poll_call_result(self, call_id: str) -> Optional[dict]:
        """Poll Bland AI API for call result when webhook didn't fire."""
        for _ in range(5):
            data = await self.get_call_status(call_id)
            status = data.get("status", "")
            if status in ("completed", "ended"):
                analysis = data.get("analysis", {})
                return {
                    "outcome": analysis.get("outcome", "full_return"),
                    "prevented": analysis.get("return_prevented", False),
                    "summary": analysis.get("summary", ""),
                    "transcript": data.get("concatenated_transcript", ""),
                    "duration": data.get("call_length", 0),
                    "source": "poll",
                }
            await asyncio.sleep(3)
        return None

    def _build_system_prompt(self, context: dict) -> str:
        """Build the system prompt for the Bland AI voice agent."""
        customer_name = context.get("customer_name", "the customer")
        product_name = context.get("product_name", "the item")
        product_price = context.get("product_price", 0)
        customer_ltv = context.get("customer_ltv", 0)
        reason = context.get("reason_category", "")

        return f"""You are a friendly, empathetic customer support agent for Return Loop.
You're speaking with {customer_name} about returning their {product_name} (${product_price:.2f}).

CUSTOMER CONTEXT:
- Lifetime value: ${customer_ltv:.2f}
- This is a {"high-value" if customer_ltv > 1500 else "standard"} customer
- Return reason: {reason}

YOUR GOAL: Find the best outcome for both the customer and the business.
Priority order:
1. Exchange for correct size/color (if sizing issue)
2. Offer partial refund + keep item (if item value < $60 or customer LTV > $2000)
3. Offer discount to keep (10-20% off)
4. Accept the return gracefully

RULES:
- Be warm, empathetic, and conversational
- Never pressure the customer
- If they insist on returning, accept gracefully and mention we'll route it sustainably
- Keep responses concise (2-3 sentences max)
- Always acknowledge their concern before offering alternatives"""

    def _mock_call(self, phone: str, context: dict) -> dict:
        """Mock call for demo without Bland AI credentials."""
        mock_id = f"mock-call-{context.get('return_request_id', 'unknown')}"
        # Register mock call so the simulated flow still works
        self.register_call(mock_id, context)
        return {
            "call_id": mock_id,
            "status": "mock_initiated",
            "is_real": False,
            "message": "Mock call -- Bland AI key not configured",
        }


# Singleton
bland_ai_service = BlandAIService()
