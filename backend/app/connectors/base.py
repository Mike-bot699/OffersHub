"""Platform-neutral connector contract.

Connectors translate a platform's API into the DTOs below. The sync service only
knows these DTOs, so adding WooCommerce, an analytics tool or an email platform
means writing a new connector, not changing the data model or analytics.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol


class Capability(StrEnum):
    READ_SHOP = "read_shop"
    READ_PRODUCTS = "read_products"
    READ_INVENTORY_COSTS = "read_inventory_costs"
    READ_ORDERS = "read_orders"
    READ_ALL_ORDERS = "read_all_orders"
    READ_CUSTOMERS = "read_customers"
    READ_DISCOUNTS = "read_discounts"
    READ_ANALYTICS = "read_analytics"
    WRITE_PRICES = "write_prices"
    WRITE_DISCOUNTS = "write_discounts"


@dataclass
class ShopInfo:
    name: str
    domain: str
    currency: str
    timezone: str
    plan: str | None = None
    granted_scopes: list[str] = field(default_factory=list)


@dataclass
class VariantDTO:
    source_id: str
    title: str | None
    sku: str | None
    price: float | None
    compare_at_price: float | None
    inventory_quantity: int | None
    inventory_tracked: bool | None
    available_for_sale: bool | None
    updated_at: datetime | None
    inventory_item_id: str | None = None
    unit_cost: float | None = None
    unit_cost_currency: str | None = None


@dataclass
class ProductDTO:
    source_id: str
    title: str
    handle: str | None
    product_type: str | None
    vendor: str | None
    status: str | None
    tags: list[str]
    created_at: datetime | None
    updated_at: datetime | None
    variants: list[VariantDTO] = field(default_factory=list)


@dataclass
class LineItemDTO:
    source_id: str
    title: str
    variant_title: str | None
    sku: str | None
    quantity: int
    original_unit_price: float
    total_discount: float
    product_source_id: str | None
    variant_source_id: str | None
    is_gift_card: bool = False
    refunded_quantity: int = 0
    refunded_amount: float = 0.0


@dataclass
class OrderDTO:
    source_id: str
    name: str | None
    processed_at: datetime
    updated_at: datetime | None
    currency: str
    subtotal: float
    total_discounts: float
    total_shipping: float
    total_tax: float
    total_price: float
    total_refunded: float
    discount_codes: list[str]
    discount_applications: list[dict[str, Any]]
    financial_status: str | None
    fulfillment_status: str | None
    cancelled_at: datetime | None
    is_test: bool
    channel: str | None
    customer_source_id: str | None
    has_refunds: bool
    lines: list[LineItemDTO] = field(default_factory=list)


@dataclass
class CouponDTO:
    source_id: str
    code: str
    title: str | None
    discount_class: str | None
    value_type: str | None
    value: float | None
    min_subtotal: float | None
    min_quantity: int | None
    starts_at: datetime | None
    ends_at: datetime | None
    status: str | None
    usage_count: int | None
    usage_limit: int | None
    once_per_customer: bool | None
    is_automatic: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RefundLineDTO:
    order_source_id: str
    line_source_id: str
    quantity: int
    amount: float


class Connector(Protocol):
    provider: str

    def verify(self) -> ShopInfo: ...

    def capabilities(self) -> set[Capability]: ...

    def fetch_products(self, updated_since: datetime | None) -> Iterator[ProductDTO]: ...

    def fetch_orders(self, updated_since: datetime | None, created_since: datetime | None) -> Iterator[OrderDTO]: ...

    def fetch_refund_lines(self, order_source_ids: list[str]) -> Iterator[RefundLineDTO]: ...

    def fetch_discounts(self) -> Iterator[CouponDTO]: ...

    def close(self) -> None: ...
