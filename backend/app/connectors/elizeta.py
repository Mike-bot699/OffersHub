"""Eli Z platform client (platform.elizetaplus.com).

Eli Z serves several stores; each is identified by a numeric shop id and read with a
Bearer API key. The two endpoints below are the ones confirmed in use by another internal
tool (Email Maker, lib/elizeta-catalog.ts):

- GET /api/v1/products?shop=<id>&status=active&limit=250   (cursor pagination: nextPageInfo)
- GET /api/v1/sales-reports?from=&to=&currency=USD&shopid=<id>

Added by the platform on 2026-10-30 (described by its developer; schema on /api-docs):

- GET /api/v1/discounts/sales?from=&to=&shop=<id>   one row per (store, day, discount)
- GET /api/v1/discounts/codes?shop=<id>[&status=]   codes with campaign, status and dates;
  automatic discounts are not listed (they are in discounts/sales)
- GET /api/v1/collections?shop=<id> and /api/v1/collections/products?shop=<id>

Days are in the store's time zone and used as given. Field names are read in camelCase or
snake_case, and the field names seen are recorded so a sync can show what actually came back.
Orders have no list endpoint, so this client does not pretend to provide them.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx

from app.connectors.base import ProductDTO, VariantDTO
from app.core.errors import IntegrationError, ValidationError

DEFAULT_BASE_URL = "https://platform.elizetaplus.com"
CAPABILITIES = ("products", "sales_reports", "discount_sales", "discount_codes", "collections")
NOT_AVAILABLE = {
    "orders": "Eli Z has no orders list: AOV without a discount and the share of all orders using a code need "
              "the store's total orders.",
}
PAGE = 1000


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


def _snake(k: str) -> str:
    return re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", str(k)).lower()


def _items(d: Any, *keys: str) -> list[dict]:
    """The list of records in a response: the body itself, or the first list under a known key."""
    if isinstance(d, list):
        return [x for x in d if isinstance(x, dict)]
    if isinstance(d, dict):
        for k in (*keys, "data", "rows", "items", "results"):
            if isinstance(d.get(k), list):
                return [x for x in d[k] if isinstance(x, dict)]
    return []


@dataclass
class Fetched:
    rows: list[dict]
    fields: list[str]   # field names seen (never values)
    complete: bool      # False when the page limit stopped the read


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

    def _paged(self, path: str, params: dict[str, Any], *keys: str, max_pages: int | None = None) -> Fetched:
        """limit/offset pagination as on the platform's other list endpoints. Stops on a short page,
        on a page identical to the previous one (endpoint without paging) or at the page limit."""
        rows: list[dict] = []
        offset, prev, limit_hit = 0, None, False
        for page in range(max_pages or self.max_pages):
            d = self._get(path, {**params, "limit": PAGE, "offset": offset})
            items = _items(d, *keys)
            if not items or items == prev:
                break
            rows.extend(items)
            if len(items) < PAGE:
                break
            prev, offset = items, offset + len(items)
            if page == (max_pages or self.max_pages) - 1:
                limit_hit = True
        fields = sorted({k for r in rows[:200] for k in r})
        return Fetched(rows=[{_snake(k): v for k, v in r.items()} for r in rows], fields=fields, complete=not limit_hit)

    def fetch_discount_sales(self, start: date, end: date) -> Fetched:
        """Sales by discount, one row per day and discount, in the store's currency.

        A month per request keeps responses small."""
        out = Fetched(rows=[], fields=[], complete=True)
        cur = start
        while cur <= end:
            nxt = min(date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1) - timedelta(days=1), end)
            f = self._paged("/api/v1/discounts/sales", {"from": cur.isoformat(), "to": nxt.isoformat(), "shop": self.shop_id},
                            "sales", "discounts")
            out.rows.extend(f.rows)
            out.fields = sorted({*out.fields, *f.fields})
            out.complete = out.complete and f.complete
            cur = nxt + timedelta(days=1)
        return out

    def fetch_discount_codes(self) -> Fetched:
        """Every code (all statuses: a code missing from a complete list does not exist)."""
        return self._paged("/api/v1/discounts/codes", {"shop": self.shop_id}, "codes", "discounts", max_pages=200)

    def fetch_collections(self) -> Fetched:
        return self._paged("/api/v1/collections", {"shop": self.shop_id}, "collections")

    def fetch_collection_products(self) -> Fetched:
        return self._paged("/api/v1/collections/products", {"shop": self.shop_id}, "products", "memberships", max_pages=200)

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
