"""Eli Z platform client (platform.elizetaplus.com).

Eli Z serves several stores; each is identified by a numeric shop id and read with a
Bearer API key. The two endpoints below are the ones confirmed in use by another internal
tool (Email Maker, lib/elizeta-catalog.ts):

- GET /api/v1/products?shop=<id>&status=active&limit=250   (cursor pagination: nextPageInfo)
- GET /api/v1/sales-reports?from=&to=&currency=USD&shopid=<id>

There is no public documentation. Orders and discount codes have no confirmed endpoint yet,
so this client does not pretend to provide them: `capabilities()` says what it can read.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from typing import Any

import httpx

from app.connectors.base import ProductDTO, VariantDTO
from app.core.errors import IntegrationError, ValidationError

DEFAULT_BASE_URL = "https://platform.elizetaplus.com"
CAPABILITIES = ("products", "sales_reports")
NOT_AVAILABLE = {
    "orders": "Eli Z has no confirmed orders endpoint yet: AOV with/without discount, orders with a code and "
              "discount rate need order data.",
    "discounts": "Eli Z has no confirmed discount-codes endpoint yet: the technical status of coupons cannot be "
                 "checked against the platform.",
}


class ElizetaError(IntegrationError):
    pass


@dataclass
class SalesRow:
    title: str
    units: int
    revenue: float | None
    avg_price: float | None
    external_id: str | None
    sku: str | None


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(re.sub(r"[^0-9.\-]", "", str(v)) or "x")
    except ValueError:
        return None


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return None


class ElizetaClient:
    def __init__(self, shop_id: str, api_key: str, *, base_url: str = DEFAULT_BASE_URL,
                 http: httpx.Client | None = None, max_pages: int = 50):
        if not shop_id or not str(shop_id).strip():
            raise ValidationError("Eli Z shop id is required.")
        if not api_key:
            raise ValidationError("Eli Z API key is required.")
        self.shop_id = str(shop_id).strip()
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        self.max_pages = max_pages

    def close(self) -> None:
        self.http.close()

    def capabilities(self) -> tuple[str, ...]:
        return CAPABILITIES

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            r = self.http.get(f"{self.base_url}{path}", params=params,
                              headers={"Authorization": f"Bearer {self._key}", "Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise ElizetaError(f"Could not reach Eli Z: {type(exc).__name__}.") from exc
        if r.status_code in (401, 403):
            raise ElizetaError(f"Eli Z refused the API key (HTTP {r.status_code}). Check the key in Settings → Stores.")
        if r.status_code == 404:
            raise ElizetaError(f"Eli Z returned HTTP 404 for shop id \"{self.shop_id}\". Check the shop id.")
        if r.status_code >= 400:
            raise ElizetaError(f"Eli Z returned HTTP {r.status_code}.")
        try:
            return r.json()
        except ValueError as exc:
            raise ElizetaError("Eli Z returned a response that is not JSON.") from exc

    def raw_products(self) -> Iterator[dict]:
        """Active catalog. The status filter only travels on the first page (later pages follow a
        cursor), so it is applied again on every result; products without a status are kept."""
        page_info: str | None = None
        pages = 0
        while True:
            params: dict[str, Any] = {"shop": self.shop_id, "limit": 250}
            if page_info:
                params["page_info"] = page_info
            else:
                params["status"] = "active"
            d = self._get("/api/v1/products", params)
            items = d.get("products", []) if isinstance(d, dict) else []
            for p in items:
                st = str(p.get("status") or "").lower()
                if not st or st == "active":
                    yield p
            pages += 1
            page_info = d.get("nextPageInfo") if isinstance(d, dict) else None
            if not page_info or pages >= self.max_pages:
                return

    def fetch_products(self) -> Iterator[ProductDTO]:
        for p in self.raw_products():
            yield to_product_dto(p)

    def fetch_sales(self, start: date, end: date, currency: str = "USD") -> list[SalesRow]:
        d = self._get("/api/v1/sales-reports", {"from": start.isoformat(), "to": end.isoformat(),
                                                "currency": currency, "shopid": self.shop_id})
        raw = d if isinstance(d, list) else next((d[k] for k in ("sold", "products", "rows", "data")
                                                  if isinstance(d.get(k), list)), [])
        # Field names only (never values), so a sync can say what the report actually contained.
        self.last_sales_fields = sorted({k for r in raw[:50] if isinstance(r, dict) for k in r})
        out = []
        for r in raw:
            title = str(_first(r, "title", "name", "product_title") or "").strip()
            if not title:
                continue
            # Same field order as Email Maker (lib/catalog-match.ts): the live report carries units in `sold`.
            units = _num(_first(r, "sold", "sales", "units", "quantity", "qty")) or 0
            out.append(SalesRow(
                title=title, units=int(units),
                revenue=_num(_first(r, "revenue", "total")),
                avg_price=_num(_first(r, "avgPrice", "price", "unit_price", "avg_price")),
                external_id=str(_first(r, "product_id", "productId", "id") or "") or None,
                sku=str(_first(r, "sku", "SKU") or "") or None,
            ))
        return out

    def verify(self) -> dict:
        """One page of the catalog: proves the shop id and key work."""
        d = self._get("/api/v1/products", {"shop": self.shop_id, "status": "active", "limit": 1})
        n = len(d.get("products", [])) if isinstance(d, dict) else 0
        return {"shop_id": self.shop_id, "sample_products": n}


def to_product_dto(p: dict) -> ProductDTO:
    title = str(p.get("title") or "").strip() or "(untitled)"
    pid = str(_first(p, "id", "product_id", "handle") or title)
    variants = []
    for i, v in enumerate(p.get("variants") or []):
        vid = str(_first(v, "id", "variant_id") or f"{pid}:{v.get('sku') or i}")
        variants.append(VariantDTO(
            source_id=vid, title=v.get("title"), sku=v.get("sku") or None, price=_num(v.get("price")),
            compare_at_price=_num(v.get("compare_at_price")), inventory_quantity=v.get("inventory_quantity"),
            inventory_tracked=None, available_for_sale=v.get("available"), updated_at=None,
        ))
    tags = p.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]
    return ProductDTO(source_id=pid, title=title, handle=p.get("handle"), product_type=p.get("product_type"),
                      vendor=p.get("vendor"), status=(str(p.get("status") or "active")).upper(), tags=tags,
                      created_at=None, updated_at=None, variants=variants)


# Tests inject a transport here.
http_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
