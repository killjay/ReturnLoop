import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db.database import Base


class RoutingDecision(Base):
    __tablename__ = "routing_decisions"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    return_request_id: Mapped[str] = mapped_column(String, ForeignKey("return_requests.id"))
    decision_type: Mapped[str] = mapped_column(String(50))
    # types: reroute_to_customer, warehouse, refurbish, liquidate, donate
    target_order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"), nullable=True)
    target_customer_id: Mapped[str] = mapped_column(String, ForeignKey("customers.id"), nullable=True)
    source_latitude: Mapped[float] = mapped_column(Float)
    source_longitude: Mapped[float] = mapped_column(Float)
    target_latitude: Mapped[float] = mapped_column(Float)
    target_longitude: Mapped[float] = mapped_column(Float)
    warehouse_latitude: Mapped[float] = mapped_column(Float, default=39.8283)
    warehouse_longitude: Mapped[float] = mapped_column(Float, default=-98.5795)
    distance_saved_miles: Mapped[float] = mapped_column(Float, default=0.0)
    cost_saved_usd: Mapped[float] = mapped_column(Float, default=0.0)
    co2_saved_kg: Mapped[float] = mapped_column(Float, default=0.0)
    warehouse_route_cost: Mapped[float] = mapped_column(Float, default=0.0)
    actual_route_cost: Mapped[float] = mapped_column(Float, default=0.0)
    recipient_risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    reasoning: Mapped[str] = mapped_column(Text, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    return_request = relationship("ReturnRequest", back_populates="routing_decision")
    target_order = relationship("Order", foreign_keys=[target_order_id])
    target_customer = relationship("Customer", foreign_keys=[target_customer_id])
