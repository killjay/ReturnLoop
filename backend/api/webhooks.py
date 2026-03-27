from fastapi import APIRouter, Request
from sqlalchemy import select

from backend.api.ws import ws_manager
from backend.services.bland_ai import bland_ai_service
from backend.db.database import async_session
from backend.models.return_request import ReturnRequest

router = APIRouter()


@router.post("/bland-ai")
async def bland_ai_webhook(request: Request):
    """Handle real Bland AI call webhooks.

    Bland AI sends events at different stages:
    - Call initiated/in-progress: status updates
    - Call completed: full transcript + analysis

    See: https://docs.bland.ai/api-v1/post/calls-id-analyze
    """
    payload = await request.json()

    call_id = payload.get("call_id", "")
    status = payload.get("status", "")
    completed = payload.get("completed", False)

    # Look up which return this call belongs to
    context = bland_ai_service.get_call_context(call_id)
    return_request_id = context.get("return_request_id", "") if context else ""
    customer_name = context.get("customer_name", "Customer") if context else "Customer"
    product_name = context.get("product_name", "Item") if context else "Item"

    # Stream transcript updates live to dashboard
    transcript = payload.get("concatenated_transcript", "")
    if transcript:
        await ws_manager.broadcast_voice_update({
            "status": "active",
            "call_id": call_id,
            "customer_name": customer_name,
            "product_name": product_name,
            "transcript": transcript,
            "return_request_id": return_request_id,
        })

    # Handle call completion
    if completed or status in ("completed", "ended"):
        # Extract analysis from Bland AI
        analysis = payload.get("analysis", {})
        concatenated_transcript = payload.get("concatenated_transcript", "")
        call_length = payload.get("call_length", 0)

        # Parse the outcome from Bland AI's analysis
        outcome = "full_return"
        prevented = False

        if isinstance(analysis, dict):
            raw_outcome = analysis.get("outcome", "full_return")
            prevented = analysis.get("return_prevented", False)
            # Normalize outcome string
            if isinstance(raw_outcome, str):
                raw_outcome = raw_outcome.lower().strip()
                if "exchange" in raw_outcome:
                    outcome = "exchange"
                    prevented = True
                elif "keep" in raw_outcome:
                    outcome = "keep_with_refund"
                    prevented = True
                elif "discount" in raw_outcome:
                    outcome = "discount_keep"
                    prevented = True
                elif "credit" in raw_outcome:
                    outcome = "store_credit"
                    prevented = True
                else:
                    outcome = "full_return"
                    prevented = False

            if analysis.get("return_prevented") is True:
                prevented = True
            elif analysis.get("return_prevented") is False:
                prevented = False

        summary = ""
        if isinstance(analysis, dict):
            summary = analysis.get("summary", "")

        # Signal the call result to the waiting Whisperer agent
        bland_ai_service.set_call_result(call_id, {
            "outcome": outcome,
            "prevented": prevented,
            "summary": summary,
            "transcript": concatenated_transcript,
            "duration": call_length,
            "source": "webhook",
        })

        # Update return request in DB
        if return_request_id:
            async with async_session() as db:
                result = await db.execute(
                    select(ReturnRequest).where(ReturnRequest.id == return_request_id)
                )
                return_req = result.scalar_one_or_none()
                if return_req:
                    return_req.negotiation_outcome = outcome
                    if prevented:
                        return_req.status = "prevented"
                        return_req.resolution = f"Return prevented via voice negotiation ({outcome}). {summary}"
                    else:
                        return_req.status = "accepted"
                        return_req.resolution = f"Customer chose full return. {summary}"
                    await db.commit()

        # Broadcast call ended to dashboard
        await ws_manager.broadcast_voice_update({
            "status": "ended",
            "call_id": call_id,
            "customer_name": customer_name,
            "product_name": product_name,
            "outcome": outcome,
            "prevented": prevented,
            "transcript_summary": summary or concatenated_transcript[:200] if concatenated_transcript else "",
            "duration": call_length,
            "return_request_id": return_request_id,
        })

        # Broadcast agent trace for the Whisperer
        await ws_manager.broadcast_agent_trace({
            "id": f"webhook-{call_id}",
            "return_request_id": return_request_id,
            "agent_name": "whisperer",
            "step_number": 99,
            "action": "voice_call_completed",
            "reasoning": f"Bland AI call completed ({call_length:.0f}s). Outcome: {outcome}. {'Return prevented!' if prevented else 'Customer proceeding with return -- handing off to Loop Matcher.'}",
            "decision": outcome,
            "data_used": {
                "call_id": call_id,
                "duration": call_length,
                "outcome": outcome,
                "prevented": prevented,
                "source": "bland_ai_webhook",
            },
            "confidence": 0.95,
            "duration_ms": int(call_length * 1000) if call_length else 0,
            "created_at": "",
        })

    return {"status": "ok"}


@router.post("/return-event")
async def return_event_webhook(request: Request):
    """Handle external return event triggers (e.g., from Shopify, Airbyte)."""
    payload = await request.json()

    await ws_manager.broadcast_return_update({
        "source": "external",
        "event": payload.get("event", ""),
        "data": payload.get("data", {}),
    })

    return {"status": "ok"}
