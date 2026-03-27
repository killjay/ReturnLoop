from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from backend.db.database import get_db
from backend.models.agent_trace import AgentTrace

router = APIRouter()


@router.get("/traces/{return_id}")
async def get_agent_traces(return_id: str, db: AsyncSession = Depends(get_db)):
    """Get all agent traces for a return request."""
    result = await db.execute(
        select(AgentTrace)
        .where(AgentTrace.return_request_id == return_id)
        .order_by(AgentTrace.created_at)
    )
    traces = result.scalars().all()
    return [
        {
            "id": t.id,
            "agent_name": t.agent_name,
            "step_number": t.step_number,
            "action": t.action,
            "reasoning": t.reasoning,
            "decision": t.decision,
            "data_used": t.data_used,
            "confidence": t.confidence,
            "duration_ms": t.duration_ms,
            "created_at": t.created_at.isoformat(),
        }
        for t in traces
    ]
