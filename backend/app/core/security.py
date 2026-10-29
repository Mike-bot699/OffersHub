"""Credential encryption and secret redaction.

Integration secrets are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using a
key supplied through the environment (`EGI_SECRET_KEY`). The key never lives in the
database, so a copied database file alone does not expose store credentials.
"""

import base64
import hashlib
import json
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings
from app.core.errors import ConfigurationError


def generate_key() -> str:
    return Fernet.generate_key().decode()


def _fernet() -> Fernet:
    key = get_settings().secret_key
    if key is None or not key.get_secret_value():
        raise ConfigurationError(
            "EGI_SECRET_KEY is not set. Generate one with `uv run python -m app.cli generate-key` "
            "and add it to backend/.env before storing integration credentials."
        )
    raw = key.get_secret_value()
    try:
        return Fernet(raw.encode())
    except (ValueError, TypeError):
        # Hosting platforms generate random secrets in their own format: derive a Fernet key
        # from any secret of at least 32 characters instead of rejecting it.
        if len(raw) < 32:
            raise ConfigurationError("EGI_SECRET_KEY must be a Fernet key or a random secret of 32+ characters.") from None
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encryption_available() -> bool:
    try:
        _fernet()
        return True
    except ConfigurationError:
        return False


def encrypt_secrets(payload: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(payload).encode()).decode()


def decrypt_secrets(token: str) -> dict[str, Any]:
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise ConfigurationError(
            "Stored credentials cannot be decrypted with the current EGI_SECRET_KEY. "
            "Reconnect the integration or restore the original key."
        ) from exc


_SECRET_PATTERNS = [
    re.compile(r"shp(at|ca|ss|pa)_[A-Za-z0-9]+"),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)(x-shopify-access-token[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
    re.compile(r"(?i)(client_secret[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]+"),
]


def redact(text: str) -> str:
    """Remove anything that looks like a credential from text bound for logs or the UI."""
    for pattern in _SECRET_PATTERNS:
        if pattern.groups:
            text = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]", text)
        else:
            text = pattern.sub("[REDACTED]", text)
    return text
