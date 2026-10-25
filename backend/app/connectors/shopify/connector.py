"""Shopify implementation of the Connector contract (read-only)."""

import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

from app.connectors.base import (
    Capability,
    CouponDTO,
    LineItemDTO,
    OrderDTO,
    ProductDTO,
    RefundLineDTO,
    ShopInfo,
    VariantDTO,
)
from app.connectors.shopify import queries as Q
from app.connectors.shopify.client import ShopifyGraphQL
from app.core.errors import CapabilityNotSupported, IntegrationError

log = logging.getLogger(__name__)

# Scope -> capability. Shopify write scopes imply the matching read scope.
SCOPE_CAPABILITIES: dict[str, set[Capability]] = {
    "read_products": {Capability.READ_PRODUCTS},
    "write_products": {Capability.READ_PRODUCTS, Capability.WRITE_PRICES},
    "read_inventory": {Capability.READ_INVENTORY_COSTS},
    "write_inventory": {Capability.READ_INVENTORY_COSTS},
    "read_orders": {Capability.READ_ORDERS},
    "write_orders": {Capability.READ_ORDERS},
    "read_all_orders": {Capability.READ_ALL_ORDERS},
    "read_customers": {Capability.READ_CUSTOMERS},
    "write_customers": {Capability.READ_CUSTOMERS},
    "read_discounts": {Capability.READ_DISCOUNTS},
    "write_discounts": {Capability.READ_DISCOUNTS, Capability.WRITE_DISCOUNTS},
    "read_reports": {Capability.READ_ANALYTICS},
}

# What the application asks for, and why. Shown in the Integrations screen.
REQUIRED_SCOPES = [
    ("read_products", True, "Products, variants, prices and availability for product analysis."),
    ("read_orders", True, "Orders, line items, discounts and refunds — the basis of every metric."),
    ("read_all_orders", False, "Order history older than 60 days (needed for trends, seasonality, repeat rate)."),
    ("read_discounts", False, "Discount codes and automatic discounts, to verify documented offers and coupon usage."),
    ("read_inventory", False, "Inventory item unit cost (COGS) and tracking, for margin analysis."),
    ("read_customers", False, "Pseudonymous customer IDs on orders, for repeat-purchase analysis. "
     "Also requires protected customer data access (level 1) in the Dev Dashboard. No PII is stored."),
]
# Not requested yet: write_products / write_discounts (Phase 6, only after explicit enablement).


def scopes_to_capabilities(scopes: list[str]) -> set[Capability]:
    caps = {Capability.READ_SHOP}
    for s in scopes:
        caps |= SCOPE_CAPABILITIES.get(s, set())
    return caps


def _f(v: Any) -> float:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return 0.0


def _opt_f(v: Any) -> float | None:
    return None if v in (None, "") else _f(v)


def _money(obj: dict | None) -> float:
    return _f(((obj or {}).get("shopMoney") or {}).get("amount"))


def _dt(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


class ShopifyConnector:
    provider = "shopify"
    BULK_POLL_SECONDS = 3.0
    BULK_TIMEOUT_SECONDS = 60 * 60

    def __init__(self, client: ShopifyGraphQL, *, sleep: Callable[[float], None] | None = None):
        self.client = client
        self._sleep = sleep or (lambda s: time.sleep(s))
        self._scopes: list[str] | None = None

    # ---- connection -------------------------------------------------------------------------
    def verify(self) -> ShopInfo:
        data = self.client.execute(Q.SHOP)
        shop = data["shop"]
        scopes = [s["handle"] for s in (data.get("currentAppInstallation") or {}).get("accessScopes", [])]
        self._scopes = scopes
        return ShopInfo(
            name=shop["name"],
            domain=shop["myshopifyDomain"],
            currency=shop["currencyCode"],
            timezone=shop["ianaTimezone"],
            granted_scopes=scopes,
        )

    def capabilities(self) -> set[Capability]:
        if self._scopes is None:
            self.verify()
        return scopes_to_capabilities(self._scopes or [])

    def probe(self) -> dict[str, str]:
        """Run tiny versions of every sync query so schema/permission problems surface on connect."""
        caps = self.capabilities()
        results: dict[str, str] = {}
        checks: list[tuple[str, Capability, str]] = [
            ("products", Capability.READ_PRODUCTS, Q.probe_products_query(Capability.READ_INVENTORY_COSTS in caps)),
            ("orders", Capability.READ_ORDERS, Q.probe_orders_query(Capability.READ_CUSTOMERS in caps)),
            ("discounts", Capability.READ_DISCOUNTS, Q.CODE_DISCOUNTS),
        ]
        for name, cap, query in checks:
            if cap not in caps:
                results[name] = "skipped: permission not granted"
                continue
            try:
                self.client.execute(query, {"cursor": None} if name == "discounts" else None)
                results[name] = "ok"
            except (IntegrationError, CapabilityNotSupported) as exc:
                results[name] = f"error: {exc.message}"
        return results

    def close(self) -> None:
        self.client.close()

    # ---- bulk operations --------------------------------------------------------------------
    def _run_bulk(self, query: str) -> Iterator[dict[str, Any]]:
        for attempt in range(10):
            data = self.client.execute(Q.BULK_RUN, {"query": query})["bulkOperationRunQuery"]
            errs = data.get("userErrors") or []
            if not errs:
                break
            msg = "; ".join(e["message"] for e in errs)
            if "already in progress" in msg.lower() and attempt < 9:
                self._sleep(10)
                continue
            raise IntegrationError(f"Bulk operation rejected: {msg}")
        op_id = data["bulkOperation"]["id"]
        waited = 0.0
        while True:
            node = self.client.execute(Q.BULK_STATUS, {"id": op_id})["node"]
            status = node["status"]
            if status == "COMPLETED":
                break
            if status in ("FAILED", "CANCELED", "EXPIRED"):
                partial = " Partial data was available but is not used, to avoid an incomplete sync." if node.get(
                    "partialDataUrl") else ""
                raise IntegrationError(f"Bulk operation {status.lower()} ({node.get('errorCode')}).{partial}",
                                       retryable=status != "FAILED")
            if waited > self.BULK_TIMEOUT_SECONDS:
                raise IntegrationError("Bulk operation timed out.", retryable=True)
            self._sleep(self.BULK_POLL_SECONDS)
            waited += self.BULK_POLL_SECONDS
        url = node.get("url")
        if not url:  # completed with zero objects
            return
        resp = self.client.download(url)
        for line in resp.iter_lines():
            if line.strip():
                yield json.loads(line)

    # ---- products ---------------------------------------------------------------------------
    def fetch_products(self, updated_since: datetime | None) -> Iterator[ProductDTO]:
        caps = self.capabilities()
        if Capability.READ_PRODUCTS not in caps:
            raise CapabilityNotSupported("read_products scope is not granted.")
        search = f"updated_at:>'{updated_since.isoformat()}'" if updated_since else ""
        query = Q.bulk_products_query(search, Capability.READ_INVENTORY_COSTS in caps)
        current: ProductDTO | None = None
        for obj in self._run_bulk(query):
            if obj.get("__typename") == "Product":
                if current:
                    yield current
                current = ProductDTO(
                    source_id=obj["id"], title=obj["title"], handle=obj.get("handle"),
                    product_type=obj.get("productType") or None, vendor=obj.get("vendor") or None,
                    status=obj.get("status"), tags=obj.get("tags") or [],
                    created_at=_dt(obj.get("createdAt")), updated_at=_dt(obj.get("updatedAt")),
                )
            elif obj.get("__typename") == "ProductVariant":
                if current is None or obj.get("__parentId") != current.source_id:
                    log.warning("Variant %s arrived without its parent product; skipped", obj.get("id"))
                    continue
                inv = obj.get("inventoryItem") or {}
                cost = inv.get("unitCost") or {}
                current.variants.append(VariantDTO(
                    source_id=obj["id"], title=obj.get("title"), sku=obj.get("sku") or None,
                    price=_opt_f(obj.get("price")), compare_at_price=_opt_f(obj.get("compareAtPrice")),
                    inventory_quantity=obj.get("inventoryQuantity"), inventory_tracked=inv.get("tracked"),
                    available_for_sale=obj.get("availableForSale"), updated_at=_dt(obj.get("updatedAt")),
                    inventory_item_id=inv.get("id"), unit_cost=_opt_f(cost.get("amount")),
                    unit_cost_currency=cost.get("currencyCode"),
                ))
        if current:
            yield current

    # ---- orders -----------------------------------------------------------------------------
    def fetch_orders(self, updated_since: datetime | None, created_since: datetime | None) -> Iterator[OrderDTO]:
        caps = self.capabilities()
        if Capability.READ_ORDERS not in caps:
            raise CapabilityNotSupported("read_orders scope is not granted.")
        if updated_since:
            search = f"updated_at:>'{updated_since.isoformat()}'"
        elif created_since:
            search = f"created_at:>='{created_since.isoformat()}'"
        else:
            search = ""
        query = Q.bulk_orders_query(search, Capability.READ_CUSTOMERS in caps)
        current: OrderDTO | None = None
        for obj in self._run_bulk(query):
            t = obj.get("__typename")
            if t == "Order":
                if current:
                    yield current
                current = self._order(obj)
            elif t == "LineItem":
                if current and obj.get("__parentId") == current.source_id:
                    current.lines.append(self._line(obj))
            elif t and t.startswith(("Discount", "Automatic", "Manual", "Script")) and t.endswith("Application"):
                if current and obj.get("__parentId") == current.source_id:
                    current.discount_applications.append(self._discount_app(obj))
        if current:
            yield current

    @staticmethod
    def _order(o: dict[str, Any]) -> OrderDTO:
        refunded = _money(o.get("totalRefundedSet"))
        return OrderDTO(
            source_id=o["id"], name=o.get("name"),
            processed_at=_dt(o.get("processedAt") or o.get("createdAt")),  # type: ignore[arg-type]
            updated_at=_dt(o.get("updatedAt")), currency=o.get("currencyCode") or "",
            subtotal=_money(o.get("subtotalPriceSet")), total_discounts=_money(o.get("totalDiscountsSet")),
            total_shipping=_money(o.get("totalShippingPriceSet")), total_tax=_money(o.get("totalTaxSet")),
            total_price=_money(o.get("totalPriceSet")), total_refunded=refunded,
            discount_codes=[c.strip().upper() for c in (o.get("discountCodes") or []) if c],
            discount_applications=[], financial_status=o.get("displayFinancialStatus"),
            fulfillment_status=o.get("displayFulfillmentStatus"), cancelled_at=_dt(o.get("cancelledAt")),
            is_test=bool(o.get("test")), channel=o.get("sourceName"),
            customer_source_id=(o.get("customer") or {}).get("id"), has_refunds=refunded > 0,
        )

    @staticmethod
    def _line(li: dict[str, Any]) -> LineItemDTO:
        disc = sum(_money(a.get("allocatedAmountSet")) for a in li.get("discountAllocations") or [])
        return LineItemDTO(
            source_id=li["id"], title=li.get("title") or "", variant_title=li.get("variantTitle"),
            sku=li.get("sku") or None, quantity=int(li.get("quantity") or 0),
            original_unit_price=_money(li.get("originalUnitPriceSet")), total_discount=round(disc, 4),
            product_source_id=(li.get("product") or {}).get("id"),
            variant_source_id=(li.get("variant") or {}).get("id"), is_gift_card=bool(li.get("isGiftCard")),
        )

    @staticmethod
    def _discount_app(d: dict[str, Any]) -> dict[str, Any]:
        value = d.get("value") or {}
        return {
            "type": d.get("__typename"),
            "code": (d.get("code") or "").upper() or None,
            "title": d.get("title"),
            "allocation": d.get("allocationMethod"),
            "target_selection": d.get("targetSelection"),
            "target_type": d.get("targetType"),
            "value_type": "percent" if "percentage" in value else "fixed" if "amount" in value else None,
            "value": _opt_f(value.get("percentage", value.get("amount"))),
        }

    def fetch_refund_lines(self, order_source_ids: list[str]) -> Iterator[RefundLineDTO]:
        for i in range(0, len(order_source_ids), 10):
            batch = order_source_ids[i:i + 10]
            data = self.client.execute(Q.REFUND_LINES, {"ids": batch})
            for node in data.get("nodes") or []:
                if not node:
                    continue
                for refund in node.get("refunds") or []:
                    for rli in ((refund.get("refundLineItems") or {}).get("nodes") or []):
                        yield RefundLineDTO(
                            order_source_id=node["id"], line_source_id=(rli.get("lineItem") or {}).get("id"),
                            quantity=int(rli.get("quantity") or 0), amount=_money(rli.get("subtotalSet")),
                        )

    # ---- discounts --------------------------------------------------------------------------
    def fetch_discounts(self) -> Iterator[CouponDTO]:
        if Capability.READ_DISCOUNTS not in self.capabilities():
            raise CapabilityNotSupported("read_discounts scope is not granted.")
        for node in self.client.paginate(Q.CODE_DISCOUNTS, ["codeDiscountNodes"]):
            d = node.get("codeDiscount") or {}
            for code_node in ((d.get("codes") or {}).get("nodes") or []):
                yield self._coupon(node["id"], code_node["code"], d, automatic=False)
        for node in self.client.paginate(Q.AUTOMATIC_DISCOUNTS, ["automaticDiscountNodes"]):
            d = node.get("automaticDiscount") or {}
            yield self._coupon(node["id"], f"AUTO:{d.get('title') or node['id']}", d, automatic=True)

    @staticmethod
    def _coupon(source_id: str, code: str, d: dict[str, Any], *, automatic: bool) -> CouponDTO:
        typename = d.get("__typename") or ""
        value = ((d.get("customerGets") or {}).get("value")) or {}
        if "FreeShipping" in typename:
            vtype, val, klass = "free_shipping", None, "shipping"
        elif "Bxgy" in typename:
            vtype, val, klass = "bxgy", None, "product"
        elif "percentage" in value:
            # DiscountPercentage.percentage is a 0–1 fraction; store percent points (15 = 15%).
            pct = _opt_f(value["percentage"])
            vtype, val, klass = "percent", round(pct * 100, 4) if pct is not None else None, "order"
        elif "amount" in value:
            vtype, val, klass = "fixed", _opt_f((value.get("amount") or {}).get("amount")), "order"
        else:
            vtype, val, klass = None, None, None
        req = d.get("minimumRequirement") or {}
        min_sub = _opt_f((req.get("greaterThanOrEqualToSubtotal") or {}).get("amount"))
        min_qty = req.get("greaterThanOrEqualToQuantity")
        return CouponDTO(
            source_id=source_id, code=code.strip().upper(), title=d.get("title"), discount_class=klass,
            value_type=vtype, value=val, min_subtotal=min_sub, min_quantity=int(min_qty) if min_qty else None,
            starts_at=_dt(d.get("startsAt")), ends_at=_dt(d.get("endsAt")),
            status=(d.get("status") or "").lower() or None, usage_count=d.get("asyncUsageCount"),
            usage_limit=d.get("usageLimit"), once_per_customer=d.get("appliesOncePerCustomer"),
            is_automatic=automatic, raw={"typename": typename},
        )
