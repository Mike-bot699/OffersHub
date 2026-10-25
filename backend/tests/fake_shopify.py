"""A small in-memory emulation of the Shopify Admin API endpoints the connector uses.

It is NOT a substitute for testing against a real store: it checks our client logic
(auth, throttling, bulk-operation flow, JSONL parsing, pagination, error handling), not
Shopify's schema. Schema compatibility is checked by the live probe on connect.
"""

import json
from dataclasses import dataclass, field

import httpx

SHOP = "test-shop.myshopify.com"


@dataclass
class FakeShopify:
    scopes: list[str] = field(default_factory=lambda: [
        "read_products", "read_orders", "read_all_orders", "read_discounts", "read_inventory", "read_customers"])
    products_jsonl: list[dict] = field(default_factory=list)
    orders_jsonl: list[dict] = field(default_factory=list)
    refunds: dict[str, list[dict]] = field(default_factory=dict)
    code_discounts: list[dict] = field(default_factory=list)
    throttle_first: bool = False
    fail_orders_bulk: bool = False
    reject_token: bool = False
    bulk_queries: list[str] = field(default_factory=list)
    token_exchanges: int = 0
    calls: list[str] = field(default_factory=list)
    _ops: dict[str, dict] = field(default_factory=dict)
    _throttled: bool = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/admin/oauth/access_token"):
            self.token_exchanges += 1
            return httpx.Response(200, json={"access_token": "shpat_fake_cc_token", "scope": ",".join(self.scopes),
                                             "expires_in": 86399})
        if url.startswith("https://storage.example/"):
            key = url.rsplit("/", 1)[-1]
            rows = self.products_jsonl if key == "products.jsonl" else self.orders_jsonl
            return httpx.Response(200, text="\n".join(json.dumps(r) for r in rows) + "\n")
        if "/graphql.json" not in url:
            return httpx.Response(404)
        if self.reject_token:
            return httpx.Response(401, json={"errors": "[API] Invalid API key or access token"})
        body = json.loads(request.content)
        q = body["query"]
        v = body.get("variables") or {}
        self.calls.append(q.strip().split("\n")[0][:40])
        cost = {"requestedQueryCost": 10, "actualQueryCost": 10,
                "throttleStatus": {"maximumAvailable": 2000, "currentlyAvailable": 1990, "restoreRate": 100}}
        if self.throttle_first and not self._throttled:
            self._throttled = True
            return httpx.Response(200, json={"errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                                             "extensions": {"cost": {**cost, "throttleStatus": {
                                                 "maximumAvailable": 2000, "currentlyAvailable": 0,
                                                 "restoreRate": 100}}}})
        data = self.resolve(q, v)
        if isinstance(data, httpx.Response):
            return data
        return httpx.Response(200, json={"data": data, "extensions": {"cost": cost}})

    def resolve(self, q: str, v: dict):
        if "query Shop" in q:
            return {"shop": {"name": "Test Shop", "myshopifyDomain": SHOP, "currencyCode": "USD",
                             "ianaTimezone": "America/New_York"},
                    "currentAppInstallation": {"accessScopes": [{"handle": s} for s in self.scopes]}}
        if "query Probe" in q:
            return {"products": {"nodes": []}} if "products(" in q else {"orders": {"nodes": []}}
        if "RunBulk" in q:
            inner = v["query"]
            self.bulk_queries.append(inner)
            kind = "products" if "products(" in inner else "orders"
            op_id = f"gid://shopify/BulkOperation/{len(self._ops) + 1}"
            self._ops[op_id] = {"kind": kind, "polls": 0}
            return {"bulkOperationRunQuery": {"bulkOperation": {"id": op_id, "status": "CREATED"}, "userErrors": []}}
        if "BulkStatus" in q:
            op = self._ops[v["id"]]
            op["polls"] += 1
            if op["kind"] == "orders" and self.fail_orders_bulk:
                return {"node": {"id": v["id"], "status": "FAILED", "errorCode": "INTERNAL_SERVER_ERROR",
                                 "objectCount": "0", "url": None, "partialDataUrl": None}}
            if op["polls"] < 2:
                return {"node": {"id": v["id"], "status": "RUNNING", "errorCode": None, "objectCount": "0",
                                 "url": None, "partialDataUrl": None}}
            rows = self.products_jsonl if op["kind"] == "products" else self.orders_jsonl
            url = f"https://storage.example/{op['kind']}.jsonl" if rows else None
            return {"node": {"id": v["id"], "status": "COMPLETED", "errorCode": None,
                             "objectCount": str(len(rows)), "url": url, "partialDataUrl": None}}
        if "RefundLines" in q:
            return {"nodes": [{"id": i, "refunds": [{"refundLineItems": {"nodes": self.refunds.get(i, [])}}]}
                              for i in v["ids"]]}
        if "CodeDiscounts" in q:
            return {"codeDiscountNodes": {"pageInfo": {"hasNextPage": False, "endCursor": None},
                                          "nodes": self.code_discounts}}
        if "AutomaticDiscounts" in q:
            return {"automaticDiscountNodes": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}}
        return httpx.Response(400, json={"errors": [{"message": f"unhandled query {q[:60]}"}]})


def money(x) -> dict:
    return {"shopMoney": {"amount": f"{x:.2f}"}}


def sample_catalog() -> list[dict]:
    return [
        {"__typename": "Product", "id": "gid://shopify/Product/1", "title": "Rain Jacket", "handle": "rain-jacket",
         "productType": "Jacket", "vendor": "WB", "status": "ACTIVE", "tags": ["outerwear"],
         "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2026-10-01T00:00:00Z"},
        {"__typename": "ProductVariant", "__parentId": "gid://shopify/Product/1", "id": "gid://shopify/ProductVariant/11",
         "title": "M / Pink", "sku": "RJ-M-P", "price": "89.00", "compareAtPrice": "129.00", "inventoryQuantity": 40,
         "availableForSale": True, "updatedAt": "2026-10-01T00:00:00Z",
         "inventoryItem": {"id": "gid://shopify/InventoryItem/111", "tracked": True,
                           "unitCost": {"amount": "22.50", "currencyCode": "USD"}}},
        {"__typename": "Product", "id": "gid://shopify/Product/2", "title": "Scarf", "handle": "scarf",
         "productType": "Accessory", "vendor": "WB", "status": "ACTIVE", "tags": [],
         "createdAt": "2025-01-01T00:00:00Z", "updatedAt": "2026-10-02T00:00:00Z"},
        {"__typename": "ProductVariant", "__parentId": "gid://shopify/Product/2", "id": "gid://shopify/ProductVariant/21",
         "title": "Default Title", "sku": "SC-1", "price": "25.00", "compareAtPrice": None, "inventoryQuantity": 5,
         "availableForSale": True, "updatedAt": "2026-10-02T00:00:00Z",
         "inventoryItem": {"id": "gid://shopify/InventoryItem/211", "tracked": True, "unitCost": None}},
    ]


def order_rows(oid: int, *, processed: str, lines: list[tuple], codes=(), refunded=0.0, customer=None,
               shipping=0.0, updated=None) -> list[dict]:
    gid = f"gid://shopify/Order/{oid}"
    gross = sum(q * p for _, q, p, _ in lines)
    disc = sum(d for *_, d in lines)
    rows = [{
        "__typename": "Order", "id": gid, "name": f"#{oid}", "processedAt": processed, "createdAt": processed,
        "updatedAt": updated or processed, "cancelledAt": None, "test": False, "currencyCode": "USD",
        "displayFinancialStatus": "PAID", "displayFulfillmentStatus": "FULFILLED", "sourceName": "web",
        "discountCodes": list(codes), "subtotalPriceSet": money(gross - disc), "totalDiscountsSet": money(disc),
        "totalShippingPriceSet": money(shipping), "totalTaxSet": money(0), "totalPriceSet": money(gross - disc + shipping),
        "totalRefundedSet": money(refunded), "customer": {"id": customer} if customer else None,
    }]
    for code in codes:
        rows.append({"__typename": "DiscountCodeApplication", "__parentId": gid, "allocationMethod": "ACROSS",
                     "targetSelection": "ALL", "targetType": "LINE_ITEM", "code": code,
                     "value": {"__typename": "PricingPercentageValue", "percentage": 10.0}})
    for i, (variant, qty, price, d) in enumerate(lines):
        product = {"gid://shopify/ProductVariant/11": "gid://shopify/Product/1",
                   "gid://shopify/ProductVariant/21": "gid://shopify/Product/2"}.get(variant)
        rows.append({"__typename": "LineItem", "__parentId": gid, "id": f"gid://shopify/LineItem/{oid}{i}",
                     "title": "item", "variantTitle": None, "sku": None, "quantity": qty, "isGiftCard": False,
                     "originalUnitPriceSet": money(price),
                     "discountAllocations": [{"allocatedAmountSet": money(d)}] if d else [],
                     "product": {"id": product} if product else None, "variant": {"id": variant}})
    return rows
