import uuid
from datetime import datetime
from sqlalchemy import String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db.database import Base


class ReturnRequest(Base):
    __tablename__ = "return_requests"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    order_id: Mapped[str] = mapped_column(String, ForeignKey("orders.id"))
    customer_id: Mapped[str] = mapped_column(String, ForeignKey("customers.id"))
    product_id: Mapped[str] = mapped_column(String, ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(String(50), default="initiated")
    # statuses: initiated, negotiating, accepted, rerouted, warehouse, prevented, resolved
    reason_category: Mapped[str] = mapped_column(String(50), default="")
    # categories: sizing, quality, preference, damage, wrong_item
    reason_detail: Mapped[str] = mapped_column(Text, default="")
    item_condition: Mapped[str] = mapped_column(String(20), default="like_new")
    # conditions: new, like_new, good, fair, poor
    negotiation_outcome: Mapped[str] = mapped_column(String(50), default="")
    # outcomes: exchange, keep_with_refund, discount_keep, store_credit, full_return
    resolution: Mapped[str] = mapped_column(Text, default="")
    cost_saved: Mapped[float] = mapped_column(Float, default=0.0)
    miles_saved: Mapped[float] = mapped_column(Float, default=0.0)
    co2_saved_kg: Mapped[float] = mapped_column(Float, default=0.0)
    initiated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    order = relationship("Order", back_populates="return_requests")
    customer = relationship("Customer", back_populates="return_requests")
    product = relationship("Product")
    routing_decision = relationship("RoutingDecision", back_populates="return_request", uselist=False)
    traces = relationship("AgentTrace", back_populates="return_request", order_by="AgentTrace.created_at")
