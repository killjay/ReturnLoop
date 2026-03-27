import uuid
from datetime import datetime
from sqlalchemy import String, Float, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db.database import Base


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    customer_id: Mapped[str] = mapped_column(String, ForeignKey("customers.id"))
    product_id: Mapped[str] = mapped_column(String, ForeignKey("products.id"))
    status: Mapped[str] = mapped_column(String(50), default="pending")  # pending, shipped, delivered, return_requested
    size: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    total_price: Mapped[float] = mapped_column(Float)
    shipping_address: Mapped[str] = mapped_column(String(500))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    shipped_from: Mapped[str] = mapped_column(String(50), default="warehouse")
    warehouse_id: Mapped[str] = mapped_column(String(50), default="WH-CENTRAL")
    tracking_number: Mapped[str] = mapped_column(String(100), default="")
    ordered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)

    customer = relationship("Customer", back_populates="orders")
    product = relationship("Product", back_populates="orders")
    return_requests = relationship("ReturnRequest", back_populates="order")
