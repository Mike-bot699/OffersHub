from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class Integration(TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("store_id", "provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(32))  # "shopify"
    auth_method: Mapped[str | None] = mapped_column(String(32))  # admin_token | client_credentials
    status: Mapped[str] = mapped_column(String(24), default="not_configured")
    shop_domain: Mapped[str | None] = mapped_column(String(255))
    # Numeric shop id on a platform that serves several stores (Eli Z).
    external_shop_id: Mapped[str | None] = mapped_column(String(32))
    api_version: Mapped[str | None] = mapped_column(String(16))
    # Encrypted JSON blob (Fernet). Never serialised to API responses.
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    # Short-lived access token cache for client_credentials (also encrypted).
    token_cache_encrypted: Mapped[str | None] = mapped_column(Text)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    granted_scopes: Mapped[list[Any]] = mapped_column(default=list)
    shop_info: Mapped[dict[str, Any]] = mapped_column(default=dict)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    sync_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sync_interval_minutes: Mapped[int] = mapped_column(Integer, default=360)
    # How far back the first order sync reaches. read_all_orders is needed beyond 60 days.
    initial_history_days: Mapped[int] = mapped_column(Integer, default=730)


class SyncState(Base):
    """High-water mark per store and resource, used for incremental sync."""

    __tablename__ = "sync_states"
    __table_args__ = (UniqueConstraint("store_id", "resource"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    resource: Mapped[str] = mapped_column(String(32))
    watermark: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # max updatedAt seen
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    record_count: Mapped[int] = mapped_column(Integer, default=0)


class SyncJob(Base):
    __tablename__ = "sync_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    integration_id: Mapped[int | None] = mapped_column(ForeignKey("integrations.id", ondelete="SET NULL"))
    trigger: Mapped[str] = mapped_column(String(16))  # manual | scheduled
    resources: Mapped[list[Any]] = mapped_column(default=list)
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict)  # per-resource fetched/upserted counts
    error: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[Any]] = mapped_column(default=list)


class ImportBatch(Base):
    """A file import. The SHA-256 prevents the same file being imported twice by accident."""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    store_id: Mapped[int | None] = mapped_column(ForeignKey("stores.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # offers_workbook | costs_csv | traffic_csv
    filename: Mapped[str] = mapped_column(String(255))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16))  # completed | failed
    rows_total: Mapped[int] = mapped_column(Integer, default=0)
    rows_imported: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[Any]] = mapped_column(default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
