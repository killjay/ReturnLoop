import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db.database import Base


class AgentTrace(Base):
    __tablename__ = "agent_traces"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    return_request_id: Mapped[str] = mapped_column(String, ForeignKey("return_requests.id"))
    agent_name: Mapped[str] = mapped_column(String(50))
    # agents: prophet, whisperer, loop_matcher, recoverer, learner
    step_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(200))
    reasoning: Mapped[str] = mapped_column(Text, default="")
    decision: Mapped[str] = mapped_column(String(500), default="")
    data_used: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    return_request = relationship("ReturnRequest", back_populates="traces")
