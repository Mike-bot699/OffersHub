from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

# Commercial settings understood by the analytics layer. Every value is optional;
# analyses that need a missing value report it instead of guessing.
DEFAULT_COMMERCIAL_SETTINGS: dict[str, Any] = {
    "free_shipping_threshold": None,     # currency amount, e.g. 99
    "avg_shipping_cost_per_order": None,  # what fulfilment shipping costs the store per order
    "payment_fee_pct": None,             # e.g. 0.029
    "payment_fee_fixed": None,           # e.g. 0.30
    "default_cogs_pct": None,            # assumption used ONLY when no unit cost exists; flagged as assumption
    "fulfillment_cost_per_order": None,  # pick/pack cost
    "min_margin_pct": None,              # margin floor used by objectives
    "new_customer_discount_pct": None,   # standing welcome offer
    "returning_customer_discount_pct": None,
}


class Store(TimestampMixin, Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    platform: Mapped[str] = mapped_column(String(32), default="shopify")
    shop_domain: Mapped[str | None] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | inactive
    # Demo stores hold synthetic data. They are excluded from the real portfolio by default.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    commercial_settings: Mapped[dict[str, Any]] = mapped_column(default=lambda: dict(DEFAULT_COMMERCIAL_SETTINGS))
    constraints: Mapped[dict[str, Any]] = mapped_column(default=dict)
    notes: Mapped[str | None] = mapped_column(Text)

    objectives: Mapped[list["StoreObjective"]] = relationship(
        back_populates="store", cascade="all, delete-orphan", order_by="StoreObjective.priority"
    )


class StoreObjective(TimestampMixin, Base):
    __tablename__ = "store_objectives"
    __table_args__ = (UniqueConstraint("store_id", "objective"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    objective: Mapped[str] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, default=1)  # 1 = primary
    params: Mapped[dict[str, Any]] = mapped_column(default=dict)  # e.g. {"min_margin_pct": 0.45, "product_ids": [..]}
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(Text)

    store: Mapped[Store] = relationship(back_populates="objectives")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(default=dict)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
