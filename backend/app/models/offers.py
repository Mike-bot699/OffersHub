"""Offer library (reusable definitions), promotional events (dated executions) and coupons.

An Offer is *what* the mechanic is ("15% off over $99"). A PromoEvent is *when and where*
it ran, with its targets and results. Coupons mirror discount codes, either documented
here or synced from the store; `verification_status` on events records whether what is
documented here was actually found configured in the connected store.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Money, TimestampMixin


class Offer(TimestampMixin, Base):
    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    offer_type: Mapped[str] = mapped_column(String(32))
    # Structured mechanic: {"value": 15, "value_type": "percent", "threshold_amount": 99,
    #  "min_quantity": 2, "gift": "...", "tiers": [{"threshold": 99, "value": 10}, ...]}
    mechanic: Mapped[dict[str, Any]] = mapped_column(default=dict)
    description: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(String(64))
    target_audience: Mapped[str | None] = mapped_column(String(64))  # all | new | returning | vip | email_list
    eligible_scope: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"type": "all"} | {"type": "products", "ids": []}
    est_incentive_cost: Mapped[float | None] = mapped_column(Money)  # per redemption, if known
    status: Mapped[str] = mapped_column(String(16), default="active")  # draft | active | archived
    source: Mapped[str] = mapped_column(String(24), default="manual")
    natural_key: Mapped[str | None] = mapped_column(String(64), index=True)


class PromoEvent(TimestampMixin, Base):
    __tablename__ = "promo_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    offer_id: Mapped[int | None] = mapped_column(ForeignKey("offers.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(300))
    offer_text: Mapped[str | None] = mapped_column(Text)
    offer_type: Mapped[str | None] = mapped_column(String(32))
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    # Operational offer status (OfferStatus). NULL = not set; shown as "needs review".
    status: Mapped[str | None] = mapped_column(String(16))
    status_source: Mapped[str | None] = mapped_column(String(16))  # manual | import | store_sync | demo
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_history: Mapped[list[Any]] = mapped_column(default=list)  # [{at, from, to, source, actor, note}]
    # Technical status (TechnicalStatus): what the connected store reports, kept separate from the above.
    technical_status: Mapped[str] = mapped_column(String(16), default="unknown")
    technical_evidence: Mapped[str | None] = mapped_column(Text)
    technical_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shopify_discount_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # When true, the offer status follows the store (used for offers detected in Shopify).
    sync_status_from_store: Mapped[bool] = mapped_column(default=False)
    status_conflict: Mapped[str | None] = mapped_column(Text)
    status_conflict_ack: Mapped[str | None] = mapped_column(Text)  # the conflict text the user reviewed
    objective: Mapped[str | None] = mapped_column(String(64))
    target_audience: Mapped[str | None] = mapped_column(String(64))
    channels: Mapped[list[Any]] = mapped_column(default=list)  # site | email | sms | paid_social
    display: Mapped[str | None] = mapped_column(String(32))  # full_site | email_only | hidden
    activation: Mapped[str | None] = mapped_column(String(16))  # manual_code | automatic
    coupon_codes: Mapped[list[Any]] = mapped_column(default=list)
    # Automatic discounts (no code) linked by hand to this offer, by their name in the discount report.
    linked_discounts: Mapped[list[Any]] = mapped_column(default=list)
    threshold_amount: Mapped[float | None] = mapped_column(Money)
    threshold_text: Mapped[str | None] = mapped_column(String(100))
    bonus: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)  # what the event is about (campaign brief)
    # Where the offer applies: store (everything) | collection | product. `level_targets` names the
    # collections or products; empty means all of them.
    level: Mapped[str | None] = mapped_column(String(16))
    level_targets: Mapped[list[Any]] = mapped_column(default=list)
    landing_page: Mapped[str | None] = mapped_column(String(1000))
    articles: Mapped[str | None] = mapped_column(Text)
    banner_message: Mapped[str | None] = mapped_column(Text)
    # Banner design for full-site offers: an HTML design file or an image (data URL).
    banner_kind: Mapped[str | None] = mapped_column(String(8))  # html | image
    banner_content: Mapped[str | None] = mapped_column(Text)
    banner_filename: Mapped[str | None] = mapped_column(String(255))
    eligible_products: Mapped[list[Any]] = mapped_column(default=list)
    est_incentive_cost: Mapped[float | None] = mapped_column(Money)
    targets: Mapped[dict[str, Any]] = mapped_column(default=dict)  # {"offer_orders": 200, "revenue": 20000}
    # Results as recorded (spreadsheet/manual) — not causal, see experiments for that.
    offer_orders: Mapped[int | None] = mapped_column(Integer)
    total_orders: Mapped[int | None] = mapped_column(Integer)
    offer_sales: Mapped[float | None] = mapped_column(Money)
    emails_sent: Mapped[int | None] = mapped_column(Integer)
    results_source: Mapped[str | None] = mapped_column(String(24))  # spreadsheet_import | shopify | manual
    analysis: Mapped[str | None] = mapped_column(Text)
    conclusions: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(24), default="documented_only")
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_issues: Mapped[list[Any]] = mapped_column(default=list)  # importer warnings for this row
    source: Mapped[str] = mapped_column(String(24), default="manual")
    source_ref: Mapped[str | None] = mapped_column(String(255))  # e.g. "Events!R12"
    # Stable identity for imported rows (store + event name + start date + coupon), so a
    # re-import of an edited workbook updates rows instead of duplicating them.
    natural_key: Mapped[str | None] = mapped_column(String(64), index=True)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id", ondelete="SET NULL"))


class Coupon(TimestampMixin, Base):
    __tablename__ = "coupons"
    __table_args__ = (UniqueConstraint("store_id", "source", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(128))  # stored upper-case
    source: Mapped[str] = mapped_column(String(24))  # shopify | spreadsheet_import | manual
    source_id: Mapped[str | None] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(String(300))
    discount_class: Mapped[str | None] = mapped_column(String(32))  # order | product | shipping
    value_type: Mapped[str | None] = mapped_column(String(16))  # percent | fixed | free_shipping | bxgy
    value: Mapped[float | None] = mapped_column(Float)
    min_subtotal: Mapped[float | None] = mapped_column(Money)
    min_quantity: Mapped[int | None] = mapped_column(Integer)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str | None] = mapped_column(String(16))  # active | expired | scheduled | deleted
    usage_count: Mapped[int | None] = mapped_column(Integer)
    usage_limit: Mapped[int | None] = mapped_column(Integer)
    once_per_customer: Mapped[bool | None] = mapped_column()
    channel: Mapped[str | None] = mapped_column(String(64))
    combines_with: Mapped[dict[str, Any]] = mapped_column(default=dict)
    raw: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DiscountReport(Base):
    """One imported "Sales by discount code" report (Shopify Analytics export or platform API) for a
    store and period. Re-importing the same store and period replaces it."""

    __tablename__ = "discount_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(24))  # shopify_export | elizeta
    filename: Mapped[str | None] = mapped_column(String(255))
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    by_day: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DiscountReportRow(Base):
    __tablename__ = "discount_report_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("discount_reports.id", ondelete="CASCADE"), index=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    day: Mapped[date | None] = mapped_column(Date)  # when the report is split by day
    name: Mapped[str] = mapped_column(String(300))  # code, or automatic discount title
    method: Mapped[str | None] = mapped_column(String(24))  # discount_code | automatic
    discount_type: Mapped[str | None] = mapped_column(String(24))
    discount_class: Mapped[str | None] = mapped_column(String(24))  # order | product | shipping
    orders: Mapped[int] = mapped_column(Integer, default=0)
    discounts: Mapped[float] = mapped_column(Money, default=0.0)  # positive amount
    gross_sales: Mapped[float | None] = mapped_column(Money)
    reversals: Mapped[float | None] = mapped_column(Money)
    net_sales: Mapped[float | None] = mapped_column(Money)
