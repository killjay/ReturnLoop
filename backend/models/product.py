import uuid
from datetime import datetime
from sqlalchemy import String, Float, Boolean, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.db.database import Base


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    sku: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(100))
    brand: Mapped[str] = mapped_column(String(200))
    price: Mapped[float] = mapped_column(Float)
    cost: Mapped[float] = mapped_column(Float)
    sizes_available: Mapped[dict] = mapped_column(JSON, default=list)
    size_chart_accuracy: Mapped[float] = mapped_column(Float, default=0.9)
    avg_review_rating: Mapped[float] = mapped_column(Float, default=4.0)
    return_rate: Mapped[float] = mapped_column(Float, default=0.12)
    common_return_reasons: Mapped[dict] = mapped_column(JSON, default=list)
    trending: Mapped[bool] = mapped_column(Boolean, default=False)
    image_url: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    orders = relationship("Order", back_populates="product")
