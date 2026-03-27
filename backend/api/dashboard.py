from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from backend.db.database import get_db
from backend.models.return_request import ReturnRequest
from backend.models.routing_decision import RoutingDecision
from backend.models.agent_trace import AgentTrace

router = APIRouter()


@router.get("/metrics")
async def get_metrics(db: AsyncSession = Depends(get_db)):
    """Get aggregate savings metrics for the dashboard."""
    # Total returns
    total_result = await db.execute(select(func.count(ReturnRequest.id)))
    total_returns = total_result.scalar() or 0

    # Returns by status
    status_counts = {}
    for status in ["initiated", "negotiating", "accepted", "rerouted", "warehouse", "prevented", "resolved"]:
        result = await db.execute(
            select(func.count(ReturnRequest.id)).where(ReturnRequest.status == status)
        )
        count = result.scalar() or 0
        if count > 0:
            status_counts[status] = count

    # Savings totals
    cost_result = await db.execute(select(func.sum(ReturnRequest.cost_saved)))
    total_cost_saved = round(cost_result.scalar() or 0, 2)

    miles_result = await db.execute(select(func.sum(ReturnRequest.miles_saved)))
    total_miles_saved = round(miles_result.scalar() or 0, 1)

    co2_result = await db.execute(select(func.sum(ReturnRequest.co2_saved_kg)))
    total_co2_saved = round(co2_result.scalar() or 0, 2)

    # Rerouted count
    rerouted_result = await db.execute(
        select(func.count(RoutingDecision.id)).where(RoutingDecision.decision_type == "reroute_to_customer")
    )
    total_rerouted = rerouted_result.scalar() or 0

    # Prevented count
    prevented = status_counts.get("prevented", 0)

    return {
        "total_returns": total_returns,
        "status_counts": status_counts,
        "total_rerouted": total_rerouted,
        "total_prevented": prevented,
        "total_cost_saved": total_cost_saved,
        "total_miles_saved": total_miles_saved,
        "total_co2_saved_kg": total_co2_saved,
    }


@router.get("/active")
async def get_active_returns(db: AsyncSession = Depends(get_db)):
    """Get currently processing returns."""
    active_statuses = ["initiated", "negotiating", "accepted"]
    result = await db.execute(
        select(ReturnRequest)
        .where(ReturnRequest.status.in_(active_statuses))
        .order_by(ReturnRequest.initiated_at.desc())
    )
    returns = result.scalars().all()
    return [
        {
            "id": r.id,
            "order_id": r.order_id,
            "status": r.status,
            "reason_category": r.reason_category,
            "initiated_at": r.initiated_at.isoformat(),
        }
        for r in returns
    ]


@router.get("/agent-status")
async def get_agent_status(db: AsyncSession = Depends(get_db)):
    """Get health/activity summary of all 5 agents."""
    agents = ["prophet", "whisperer", "loop_matcher", "recoverer", "learner"]
    statuses = []

    for agent_name in agents:
        result = await db.execute(
            select(func.count(AgentTrace.id)).where(AgentTrace.agent_name == agent_name)
        )
        total_actions = result.scalar() or 0

        last_result = await db.execute(
            select(AgentTrace.created_at)
            .where(AgentTrace.agent_name == agent_name)
            .order_by(AgentTrace.created_at.desc())
            .limit(1)
        )
        last_active = last_result.scalar_one_or_none()

        statuses.append({
            "name": agent_name,
            "status": "active" if total_actions > 0 else "idle",
            "total_actions": total_actions,
            "last_active": last_active.isoformat() if last_active else None,
        })

    return statuses


@router.get("/traces/{return_id}")
async def get_traces(return_id: str, db: AsyncSession = Depends(get_db)):
    """Get all agent traces for a specific return."""
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
