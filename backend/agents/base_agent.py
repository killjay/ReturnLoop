"""Base agent class with shared reasoning, tracing, and WebSocket emission."""
import time
import uuid
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.agent_trace import AgentTrace
from backend.services.claude_client import claude_client
from backend.api.ws import ws_manager


class BaseAgent:
    """Base class for all Return Loop agents.

    Every agent logs its reasoning steps to AgentTrace,
    which powers the live dashboard Agent Trace panel.
    """

    name: str = "base"
    description: str = "Base agent"

    def __init__(self, db: AsyncSession):
        self.db = db
        self.current_step = 0

    async def think(
        self,
        return_request_id: str,
        action: str,
        reasoning: str,
        decision: str = "",
        data_used: dict = None,
        confidence: float = 0.0,
    ) -> AgentTrace:
        """Log a reasoning step and broadcast to dashboard.

        This is what makes the Agent Trace panel come alive.
        Each call = one visible step in the dashboard.
        """
        self.current_step += 1
        start_time = time.time()

        trace = AgentTrace(
            id=str(uuid.uuid4()),
            return_request_id=return_request_id,
            agent_name=self.name,
            step_number=self.current_step,
            action=action,
            reasoning=reasoning,
            decision=decision,
            data_used=data_used or {},
            confidence=confidence,
            duration_ms=int((time.time() - start_time) * 1000),
            created_at=datetime.utcnow(),
        )

        self.db.add(trace)
        await self.db.commit()

        # Broadcast to dashboard via WebSocket
        await ws_manager.broadcast_agent_trace({
            "id": trace.id,
            "return_request_id": return_request_id,
            "agent_name": self.name,
            "step_number": trace.step_number,
            "action": action,
            "reasoning": reasoning,
            "decision": decision,
            "data_used": data_used or {},
            "confidence": confidence,
            "duration_ms": trace.duration_ms,
            "created_at": trace.created_at.isoformat(),
        })

        return trace

    async def reason(self, system_prompt: str, user_prompt: str) -> str:
        """Call LLM for complex reasoning via Claude API."""
        return await claude_client.reason(system_prompt, user_prompt)

    def reset_steps(self):
        """Reset step counter for a new return processing."""
        self.current_step = 0
