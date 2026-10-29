"""Catalog, orders, customers, costs and traffic — the factual data layer.

Every row carries `store_id` (no cross-store joins without it) and `source`.
Records pulled from a platform keep the platform identifier in `source_id`.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Money, TimestampMixin


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("store_id", "source", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(500))
    handle: Mapped[str | None] = mapped_column(String(255))
    product_type: Mapped[str | None] = mapped_column(String(255))
    vendor: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str | None] = mapped_column(String(24))  # ACTIVE | DRAFT | ARCHIVED
    tags: Mapped[list[Any]] = mapped_column(default=list)
    # Tags set by people in this application (e.g. "Featured"); never overwritten by a sync.
    manual_tags: Mapped[list[Any]] = mapped_column(default=list)
    # Performance tag from the last 30 days of sales: best_seller | top_seller | popular | free | None.
    perf_tag: Mapped[str | None] = mapped_column(String(16))
    perf_rank: Mapped[int | None] = mapped_column(Integer)
    perf_units_30d: Mapped[int | None] = mapped_column(Integer)
    perf_revenue_30d: Mapped[float | None] = mapped_column(Money)
    perf_computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    variants: Mapped[list["Variant"]] = relationship(back_populates="product", cascade="all, delete-orphan")


class Variant(TimestampMixin, Base):
    __tablename__ = "variants"
    __table_args__ = (UniqueConstraint("store_id", "source", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(128))
    inventory_item_source_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(500))
    sku: Mapped[str | None] = mapped_column(String(255), index=True)
    price: Mapped[float | None] = mapped_column(Money)
    compare_at_price: Mapped[float | None] = mapped_column(Money)
    inventory_quantity: Mapped[int | None] = mapped_column(Integer)
    inventory_tracked: Mapped[bool | None] = mapped_column(Boolean)
    available_for_sale: Mapped[bool | None] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    product: Mapped[Product] = relationship(back_populates="variants")


class PriceHistory(Base):
    """Price observations. A row is written whenever a sync sees a price differ from the last one."""

    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), index=True)
    price: Mapped[float | None] = mapped_column(Money)
    compare_at_price: Mapped[float | None] = mapped_column(Money)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(24))


class CostRecord(Base):
    """Unit cost per variant (or SKU) with provenance and an effective date."""

    __tablename__ = "cost_records"
    __table_args__ = (Index("ix_cost_lookup", "store_id", "variant_id", "effective_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"))
    sku: Mapped[str | None] = mapped_column(String(255))
    unit_cost: Mapped[float] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    effective_from: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(24))  # shopify | csv_import | manual
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Customer(Base):
    """Pseudonymous customer record. No names, emails, addresses or phone numbers are stored."""

    __tablename__ = "customers"
    __table_args__ = (UniqueConstraint("store_id", "source", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(128))
    first_order_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("store_id", "source", "source_id"),
        Index("ix_orders_store_processed", "store_id", "processed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(24))
    source_id: Mapped[str] = mapped_column(String(128))
    name: Mapped[str | None] = mapped_column(String(64))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(3))
    # Amounts in shop currency, as originally placed (not "current" after edits).
    gross_sales: Mapped[float] = mapped_column(Money, default=0.0)        # Σ original unit price × qty
    total_discounts: Mapped[float] = mapped_column(Money, default=0.0)    # line + order-level discounts
    subtotal: Mapped[float] = mapped_column(Money, default=0.0)           # after discounts, excl. shipping & tax
    total_shipping: Mapped[float] = mapped_column(Money, default=0.0)     # shipping charged to the customer
    total_tax: Mapped[float] = mapped_column(Money, default=0.0)
    total_price: Mapped[float] = mapped_column(Money, default=0.0)        # what the customer paid
    total_refunded: Mapped[float] = mapped_column(Money, default=0.0)
    refunded_merchandise: Mapped[float] = mapped_column(Money, default=0.0)  # Σ refund line subtotals
    units: Mapped[int] = mapped_column(Integer, default=0)
    discount_codes: Mapped[list[Any]] = mapped_column(default=list)       # upper-cased codes
    discount_applications: Mapped[list[Any]] = mapped_column(default=list)
    financial_status: Mapped[str | None] = mapped_column(String(32))
    fulfillment_status: Mapped[str | None] = mapped_column(String(32))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    channel: Mapped[str | None] = mapped_column(String(64))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id", ondelete="SET NULL"), index=True)
    customer_order_index: Mapped[int | None] = mapped_column(Integer)  # 1 = first order (when known)

    lines: Mapped[list["OrderLine"]] = relationship(back_populates="order", cascade="all, delete-orphan")


class OrderLine(Base):
    __tablename__ = "order_lines"
    __table_args__ = (UniqueConstraint("order_id", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    source_id: Mapped[str] = mapped_column(String(128))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("variants.id", ondelete="SET NULL"), index=True)
    product_source_id: Mapped[str | None] = mapped_column(String(128))
    variant_source_id: Mapped[str | None] = mapped_column(String(128))
    sku: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    variant_title: Mapped[str | None] = mapped_column(String(500))
    quantity: Mapped[int] = mapped_column(Integer)
    original_unit_price: Mapped[float] = mapped_column(Money)
    total_discount: Mapped[float] = mapped_column(Money, default=0.0)  # allocated line + order discounts
    refunded_quantity: Mapped[int] = mapped_column(Integer, default=0)
    refunded_amount: Mapped[float] = mapped_column(Money, default=0.0)
    is_gift_card: Mapped[bool] = mapped_column(Boolean, default=False)

    order: Mapped[Order] = relationship(back_populates="lines")


class TrafficDaily(Base):
    """Sessions / funnel data. Shopify orders alone cannot provide this; it must be imported
    or come from an analytics integration. product_id NULL = store-level row."""

    __tablename__ = "traffic_daily"
    __table_args__ = (UniqueConstraint("store_id", "day", "product_id", "source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    sessions: Mapped[int] = mapped_column(Integer)
    added_to_cart: Mapped[int | None] = mapped_column(Integer)
    reached_checkout: Mapped[int | None] = mapped_column(Integer)
    converted: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(24))
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))


class MetricSnapshot(Base):
    """Point-in-time copy of headline metrics, written each time analysis runs, so the
    application can show how the business looked when a recommendation was made."""

    __tablename__ = "metric_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict)
    data_quality: Mapped[dict[str, Any]] = mapped_column(default=dict)


class DataNote(Base):
    """Free-form provenance note attached to any entity (e.g. "costs supplied by finance 2025-03")."""

    __tablename__ = "data_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


__all__ = [
    "CostRecord",
    "Customer",
    "DataNote",
    "MetricSnapshot",
    "Order",
    "OrderLine",
    "PriceHistory",
    "Product",
    "TrafficDaily",
    "Variant",
]


class SalesReportRow(Base):
    """One row of a platform sales report (units sold per product over a period).

    Kept as received, with how it was matched to a catalog product, so the performance
    tags can always be traced back to the report they came from."""

    __tablename__ = "sales_report_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(24))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(500))
    external_id: Mapped[str | None] = mapped_column(String(128))
    sku: Mapped[str | None] = mapped_column(String(255))
    units: Mapped[int] = mapped_column(Integer, default=0)
    revenue: Mapped[float | None] = mapped_column(Money)  # as written in the report, if present
    avg_price: Mapped[float | None] = mapped_column(Money)
    currency: Mapped[str | None] = mapped_column(String(3))
    product_id: Mapped[int | None] = mapped_column(ForeignKey("products.id", ondelete="SET NULL"), index=True)
    match_method: Mapped[str | None] = mapped_column(String(16))  # title | external_id | sku | None
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
