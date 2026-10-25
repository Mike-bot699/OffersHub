"""Integration lifecycle: configure credentials, verify against the live API, disconnect.

A connection is only marked `connected` after a real API call succeeds. Nothing in this
module fabricates connection state.
"""

import logging
from collections.abc import Callable
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import Capability
from app.connectors.shopify.client import ShopifyAuth, ShopifyGraphQL, TokenGrant, normalize_shop_domain
from app.connectors.shopify.connector import REQUIRED_SCOPES, ShopifyConnector, scopes_to_capabilities
from app.core.config import get_settings
from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.security import decrypt_secrets, encrypt_secrets, redact
from app.db.base import utcnow
from app.models import Integration, Store
from app.models.enums import IntegrationStatus
from app.services.audit import audit

log = logging.getLogger(__name__)

# Tests inject a transport here to emulate Shopify without network access.
http_factory: Callable[[], httpx.Client] = lambda: httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))


def get_or_create_integration(db: Session, store: Store, provider: str = "shopify") -> Integration:
    integ = db.scalar(select(Integration).where(Integration.store_id == store.id, Integration.provider == provider))
    if integ is None:
        integ = Integration(store_id=store.id, provider=provider, status=IntegrationStatus.NOT_CONFIGURED,
                            api_version=get_settings().shopify_api_version)
        db.add(integ)
        db.flush()
    return integ


def configure_shopify(
    db: Session,
    store: Store,
    *,
    shop_domain: str,
    auth_method: str,
    access_token: str | None = None,
    client_id: str | None = None,
    client_secret: str | None = None,
) -> Integration:
    if store.is_demo:
        raise ValidationError("Demo stores cannot be connected to a live platform.")
    domain = normalize_shop_domain(shop_domain)
    if auth_method == "admin_token":
        if not access_token:
            raise ValidationError("Admin API access token is required.")
        secrets = {"access_token": access_token.strip()}
    elif auth_method == "client_credentials":
        if not (client_id and client_secret):
            raise ValidationError("Client ID and client secret are required.")
        secrets = {"client_id": client_id.strip(), "client_secret": client_secret.strip()}
    else:
        raise ValidationError(f"Unknown auth method '{auth_method}'.")

    integ = get_or_create_integration(db, store)
    integ.shop_domain = domain
    integ.auth_method = auth_method
    integ.credentials_encrypted = encrypt_secrets(secrets)
    integ.token_cache_encrypted = None
    integ.token_expires_at = None
    integ.status = IntegrationStatus.CONFIGURED
    integ.last_error = None
    integ.api_version = get_settings().shopify_api_version
    store.shop_domain = domain
    audit(db, "integration.configured", "integration", integ.id, store_id=store.id,
          details={"provider": "shopify", "auth_method": auth_method, "shop_domain": domain})
    db.flush()
    return integ


def build_connector(db: Session, integ: Integration) -> ShopifyConnector:
    if integ.provider != "shopify":
        raise ValidationError(f"No connector for provider '{integ.provider}'.")
    if not integ.credentials_encrypted or not integ.shop_domain:
        raise ValidationError("Integration has no stored credentials. Configure it first.")
    secrets = decrypt_secrets(integ.credentials_encrypted)
    cached = None
    if integ.token_cache_encrypted and integ.token_expires_at:
        tok = decrypt_secrets(integ.token_cache_encrypted)
        expires = integ.token_expires_at
        cached = TokenGrant(tok["access_token"], tok.get("scopes", []), expires)

    def persist(grant: TokenGrant) -> None:
        integ.token_cache_encrypted = encrypt_secrets({"access_token": grant.access_token, "scopes": grant.scopes})
        integ.token_expires_at = grant.expires_at

    auth = ShopifyAuth(
        integ.auth_method or "admin_token",
        access_token=secrets.get("access_token"),
        client_id=secrets.get("client_id"),
        client_secret=secrets.get("client_secret"),
        cached=cached,
        on_token=persist,
    )
    client = ShopifyGraphQL(integ.shop_domain, auth, integ.api_version or get_settings().shopify_api_version,
                            http=http_factory())
    return ShopifyConnector(client)


def verify(db: Session, integ: Integration) -> dict:
    """Call the live API. Updates status, scopes, shop info and probe results."""
    store = db.get(Store, integ.store_id)
    try:
        conn = build_connector(db, integ)
    except AppError as exc:
        integ.status = IntegrationStatus.ERROR
        integ.last_error = exc.message
        db.flush()
        raise
    try:
        info = conn.verify()
        probe = conn.probe()
    except AppError as exc:
        integ.status = IntegrationStatus.ERROR
        integ.last_error = redact(exc.message)
        audit(db, "integration.verify_failed", "integration", integ.id, store_id=integ.store_id,
              details={"error": integ.last_error})
        db.flush()
        raise
    finally:
        conn.close()

    integ.status = IntegrationStatus.CONNECTED
    integ.granted_scopes = info.granted_scopes
    integ.shop_info = {"name": info.name, "domain": info.domain, "currency": info.currency,
                       "timezone": info.timezone, "probe": probe}
    integ.last_verified_at = utcnow()
    integ.last_error = None
    if store:
        # The platform is the authority for currency and timezone.
        if store.currency != info.currency:
            log.info("Store %s currency updated from Shopify: %s -> %s", store.code, store.currency, info.currency)
        store.currency = info.currency
        store.timezone = info.timezone
    audit(db, "integration.verified", "integration", integ.id, store_id=integ.store_id,
          details={"scopes": info.granted_scopes, "probe": probe})
    db.flush()
    return integ.shop_info


def disconnect(db: Session, integ: Integration, *, purge_data: bool = False) -> None:
    """Delete stored credentials locally. Synced data is kept unless purge_data is set.

    Note: this does not uninstall the app or revoke the token on Shopify's side; the
    UI instructs the user to uninstall the app / rotate the secret in Shopify as well.
    """
    integ.credentials_encrypted = None
    integ.token_cache_encrypted = None
    integ.token_expires_at = None
    integ.status = IntegrationStatus.DISCONNECTED
    integ.sync_enabled = False
    audit(db, "integration.disconnected", "integration", integ.id, store_id=integ.store_id,
          details={"purge_data": purge_data})
    if purge_data:
        from app.services.store_data import purge_platform_data

        purge_platform_data(db, integ.store_id, source=integ.provider)
    db.flush()


def describe(integ: Integration | None) -> dict:
    """Safe, credential-free description for the API."""
    if integ is None:
        return {"status": IntegrationStatus.NOT_CONFIGURED, "provider": "shopify"}
    caps = sorted(scopes_to_capabilities(integ.granted_scopes or [])) if integ.granted_scopes else []
    granted = set(integ.granted_scopes or [])
    return {
        "id": integ.id,
        "provider": integ.provider,
        "status": integ.status,
        "auth_method": integ.auth_method,
        "shop_domain": integ.shop_domain,
        "api_version": integ.api_version,
        "has_credentials": bool(integ.credentials_encrypted),
        "granted_scopes": integ.granted_scopes,
        "capabilities": caps,
        "missing_capabilities": [c for c in (Capability.READ_ALL_ORDERS, Capability.READ_DISCOUNTS,
                                             Capability.READ_INVENTORY_COSTS, Capability.READ_CUSTOMERS)
                                 if integ.granted_scopes and c not in caps],
        "scope_plan": [{"scope": s, "required": r, "why": why, "granted": s in granted or
                        s.replace("read_", "write_") in granted} for s, r, why in REQUIRED_SCOPES],
        "shop_info": integ.shop_info,
        "last_verified_at": _iso(integ.last_verified_at),
        "last_error": integ.last_error,
        "token_expires_at": _iso(integ.token_expires_at),
        "sync_enabled": integ.sync_enabled,
        "sync_interval_minutes": integ.sync_interval_minutes,
        "initial_history_days": integ.initial_history_days,
    }


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def require_integration(db: Session, store_id: int) -> Integration:
    integ = db.scalar(select(Integration).where(Integration.store_id == store_id, Integration.provider == "shopify"))
    if integ is None:
        raise NotFoundError("This store has no Shopify integration configured.")
    return integ
