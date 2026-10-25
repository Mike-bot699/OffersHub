"""Low-level Shopify Admin GraphQL client.

Handles: authentication (static Admin API token for legacy custom apps, or the
client-credentials grant for Dev Dashboard apps owned by the same organisation),
cost-based throttling, retries with backoff, and error classification.
"""

import logging
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.errors import CapabilityNotSupported, IntegrationError
from app.core.security import redact

log = logging.getLogger(__name__)

DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]*\.myshopify\.com$")


def normalize_shop_domain(value: str) -> str:
    v = value.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
    if "." not in v:
        v = f"{v}.myshopify.com"
    if not DOMAIN_RE.match(v):
        raise IntegrationError(
            f"'{value}' is not a valid *.myshopify.com domain. Use the permanent myshopify domain, "
            "not a custom storefront domain."
        )
    return v


@dataclass
class TokenGrant:
    access_token: str
    scopes: list[str]
    expires_at: datetime | None


class ShopifyAuth:
    """Resolves an access token. `on_token` lets the caller persist a refreshed token (encrypted)."""

    def __init__(
        self,
        method: str,
        *,
        access_token: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        cached: TokenGrant | None = None,
        on_token: Callable[[TokenGrant], None] | None = None,
    ):
        if method not in ("admin_token", "client_credentials"):
            raise IntegrationError(f"Unsupported Shopify auth method: {method}")
        if method == "admin_token" and not access_token:
            raise IntegrationError("An Admin API access token is required.")
        if method == "client_credentials" and not (client_id and client_secret):
            raise IntegrationError("Client ID and client secret are required.")
        self.method = method
        self._static = access_token
        self._client_id = client_id
        self._client_secret = client_secret
        self._grant = cached
        self._on_token = on_token

    def token(self, http: httpx.Client, shop: str, *, force_refresh: bool = False) -> str:
        if self.method == "admin_token":
            assert self._static
            return self._static
        fresh_enough = (
            self._grant
            and self._grant.expires_at
            and self._grant.expires_at - datetime.now(UTC) > timedelta(minutes=10)
        )
        if fresh_enough and not force_refresh:
            return self._grant.access_token  # type: ignore[union-attr]
        self._grant = self._exchange(http, shop)
        if self._on_token:
            self._on_token(self._grant)
        return self._grant.access_token

    def _exchange(self, http: httpx.Client, shop: str) -> TokenGrant:
        try:
            resp = http.post(
                f"https://{shop}/admin/oauth/access_token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise IntegrationError(f"Could not reach {shop}: {exc}", retryable=True) from exc
        if resp.status_code != 200:
            body = redact(resp.text[:300])
            hint = ""
            if "shop_not_permitted" in body:
                hint = " The app and the store must belong to the same Shopify organisation."
            raise IntegrationError(f"Token exchange failed ({resp.status_code}): {body}.{hint}")
        data = resp.json()
        expires_in = data.get("expires_in")
        return TokenGrant(
            access_token=data["access_token"],
            scopes=[s for s in (data.get("scope") or "").split(",") if s],
            expires_at=datetime.now(UTC) + timedelta(seconds=int(expires_in)) if expires_in else None,
        )


class ShopifyGraphQL:
    MAX_ATTEMPTS = 6

    def __init__(
        self,
        shop: str,
        auth: ShopifyAuth,
        api_version: str,
        *,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.shop = normalize_shop_domain(shop)
        self.auth = auth
        self.api_version = api_version
        self.http = http or httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        self._sleep = sleep or (lambda s: time.sleep(s))
        self.last_throttle: dict[str, Any] | None = None

    @property
    def endpoint(self) -> str:
        return f"https://{self.shop}/admin/api/{self.api_version}/graphql.json"

    def close(self) -> None:
        self.http.close()

    def execute(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        attempt = 0
        refreshed = False
        while True:
            attempt += 1
            token = self.auth.token(self.http, self.shop)
            try:
                resp = self.http.post(
                    self.endpoint,
                    json={"query": query, "variables": variables or {}},
                    headers={"X-Shopify-Access-Token": token, "Content-Type": "application/json"},
                )
            except httpx.HTTPError as exc:
                if attempt >= self.MAX_ATTEMPTS:
                    raise IntegrationError(f"Network error talking to {self.shop}: {exc}", retryable=True) from exc
                self._backoff(attempt)
                continue

            if resp.status_code == 401:
                if self.auth.method == "client_credentials" and not refreshed:
                    self.auth.token(self.http, self.shop, force_refresh=True)
                    refreshed = True
                    continue
                raise IntegrationError("Shopify rejected the access token (401). Reconnect the integration.")
            if resp.status_code == 403:
                raise CapabilityNotSupported(f"Shopify denied access (403): {redact(resp.text[:300])}")
            if resp.status_code == 404:
                raise IntegrationError(
                    f"Endpoint not found for {self.shop} (API version {self.api_version}). "
                    "Check the shop domain and that the API version is still supported."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self.MAX_ATTEMPTS:
                    raise IntegrationError(f"Shopify unavailable ({resp.status_code}) after retries.", retryable=True)
                retry_after = resp.headers.get("Retry-After")
                self._sleep(float(retry_after) if retry_after else self._backoff_seconds(attempt))
                continue
            if resp.status_code != 200:
                raise IntegrationError(f"Unexpected Shopify response {resp.status_code}: {redact(resp.text[:300])}")

            payload = resp.json()
            cost = (payload.get("extensions") or {}).get("cost")
            if cost:
                self.last_throttle = cost.get("throttleStatus")
            errors = payload.get("errors")
            if errors:
                codes = {((e.get("extensions") or {}).get("code")) for e in errors if isinstance(e, dict)}
                if "THROTTLED" in codes:
                    if attempt >= self.MAX_ATTEMPTS:
                        raise IntegrationError("Shopify throttling did not clear after retries.", retryable=True)
                    self._sleep(self._throttle_wait(cost))
                    continue
                messages = "; ".join(str(e.get("message", e)) for e in errors)[:600]
                if "ACCESS_DENIED" in codes or "access denied" in messages.lower():
                    raise CapabilityNotSupported(
                        f"Missing permission for this query: {messages}", details={"codes": sorted(c for c in codes if c)}
                    )
                raise IntegrationError(f"Shopify GraphQL error: {messages}")
            self._pace(cost)
            return payload["data"]

    def _throttle_wait(self, cost: dict | None) -> float:
        if not cost:
            return 2.0
        status = cost.get("throttleStatus") or {}
        need = float(cost.get("requestedQueryCost") or 100)
        available = float(status.get("currentlyAvailable") or 0)
        rate = float(status.get("restoreRate") or 50)
        return max(1.0, (need - available) / rate + 0.5)

    def _pace(self, cost: dict | None) -> None:
        """Pause pre-emptively when the bucket is nearly empty, instead of waiting to be throttled."""
        if not cost:
            return
        status = cost.get("throttleStatus") or {}
        available = status.get("currentlyAvailable")
        maximum = status.get("maximumAvailable")
        rate = status.get("restoreRate") or 50
        if available is not None and maximum and available < 0.1 * maximum:
            self._sleep(min(10.0, (0.25 * maximum - available) / rate))

    def _backoff_seconds(self, attempt: int) -> float:
        return min(30.0, (2 ** (attempt - 1)) + random.uniform(0, 0.5))

    def _backoff(self, attempt: int) -> None:
        self._sleep(self._backoff_seconds(attempt))

    def paginate(self, query: str, path: list[str], variables: dict[str, Any] | None = None):
        """Yield nodes from a connection. `path` locates the connection inside `data`."""
        cursor = None
        while True:
            data = self.execute(query, {**(variables or {}), "cursor": cursor})
            conn = data
            for key in path:
                conn = conn[key]
            yield from conn["nodes"]
            page = conn["pageInfo"]
            if not page["hasNextPage"]:
                return
            cursor = page["endCursor"]

    def download(self, url: str) -> httpx.Response:
        resp = self.http.get(url, timeout=httpx.Timeout(300.0, connect=15.0))
        if resp.status_code != 200:
            raise IntegrationError(f"Bulk result download failed ({resp.status_code}).", retryable=True)
        return resp


def gid_tail(gid: str | None) -> str | None:
    return gid.rsplit("/", 1)[-1] if gid else None


def log_throttle(client: ShopifyGraphQL) -> None:
    if client.last_throttle:
        log.debug("Shopify throttle status: %s", client.last_throttle)
