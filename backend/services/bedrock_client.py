"""AWS Bedrock client for agent reasoning via Claude."""
import json
import boto3
from backend.config import get_settings

settings = get_settings()


class BedrockClient:
    def __init__(self):
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = boto3.client(
                "bedrock-runtime",
                region_name=settings.aws_region,
                aws_access_key_id=settings.aws_access_key_id or None,
                aws_secret_access_key=settings.aws_secret_access_key or None,
            )
        return self._client

    async def reason(self, system_prompt: str, user_prompt: str, max_tokens: int = 1024) -> str:
        """Call Claude via Bedrock for agent reasoning.

        Falls back to a structured mock response if Bedrock is not configured,
        so the demo works without AWS credentials.
        """
        if not settings.aws_access_key_id:
            return self._mock_reason(system_prompt, user_prompt)

        try:
            body = json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_prompt}
                ],
            })

            response = self.client.invoke_model(
                modelId=settings.bedrock_model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )

            result = json.loads(response["body"].read())
            return result["content"][0]["text"]
        except Exception as e:
            print(f"Bedrock error, falling back to mock: {e}")
            return self._mock_reason(system_prompt, user_prompt)

    def _mock_reason(self, system_prompt: str, user_prompt: str) -> str:
        """Mock reasoning for demo without AWS credentials."""
        if "negotiation" in system_prompt.lower() or "whisperer" in system_prompt.lower():
            return json.dumps({
                "strategy": "exchange_and_keep",
                "reasoning": "Customer has high lifetime value ($2,400) and the product has known sizing issues (38% of reviews mention 'runs small'). Best strategy: offer to keep current item and send correct size free of charge.",
                "confidence": 0.87,
                "suggested_response": "I can see this jacket runs a bit narrow in the shoulders -- many customers have mentioned that. How about this: keep that one, maybe gift it to someone, and I'll send you the right size with express shipping, completely free of charge."
            })
        elif "routing" in system_prompt.lower() or "matcher" in system_prompt.lower():
            return json.dumps({
                "decision": "reroute_to_customer",
                "reasoning": "Found nearby customer with matching order. Direct route saves significant shipping distance and cost. Recipient has low return risk (8%), making reroute reliable.",
                "confidence": 0.92,
            })
        elif "prediction" in system_prompt.lower() or "prophet" in system_prompt.lower():
            return json.dumps({
                "return_probability": 0.84,
                "reasoning": "High return probability based on: (1) Product has 34% return rate, (2) Size chart accuracy is only 65%, (3) Customer ordered size M but historical purchases suggest L fits better.",
                "recommended_action": "proactive_size_suggestion",
                "confidence": 0.84,
            })
        elif "pattern" in system_prompt.lower() or "learner" in system_prompt.lower():
            return json.dumps({
                "patterns_found": [
                    {
                        "pattern": "sizing_issue",
                        "sku": "JKT-ALPINE-BLK",
                        "detail": "34% return rate with 78% citing sizing issues. Size chart accuracy is 65%.",
                        "recommendation": "Update size chart -- recommend sizing up. Current size M fits like S.",
                        "impact": "Could reduce returns by ~25% for this SKU"
                    }
                ],
                "confidence": 0.89,
            })
        else:
            return json.dumps({
                "reasoning": "Analysis complete.",
                "confidence": 0.80,
            })


# Singleton
bedrock_client = BedrockClient()
