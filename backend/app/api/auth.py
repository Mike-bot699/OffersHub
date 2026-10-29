"""Optional single-owner password authentication for hosted deployments.

EGI_AUTH_MODE=none     — local use only (the server binds to 127.0.0.1 by default).
EGI_AUTH_MODE=password — every /api route except health and login requires a signed,
                         HttpOnly session cookie obtained with EGI_AUTH_PASSWORD.
"""

import base64
import hashlib
import hmac
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.errors import AuthError, ConfigurationError

COOKIE = "egi_session"
OPEN_PATHS = {"/api/health", "/api/auth/login", "/api/auth/status", "/api/cron/sync"}  # cron checks its own secret
router = APIRouter(prefix="/api/auth", tags=["auth"])


def _key() -> bytes:
    s = get_settings()
    if s.secret_key is None:
        raise ConfigurationError("EGI_SECRET_KEY is required when authentication is enabled.")
    return hashlib.sha256(b"session:" + s.secret_key.get_secret_value().encode()).digest()


def issue_token() -> str:
    exp = int(time.time()) + get_settings().session_ttl_hours * 3600
    msg = str(exp).encode()
    sig = hmac.new(_key(), msg, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(msg + b"." + sig).decode()


def valid_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        raw = base64.urlsafe_b64decode(token.encode())
        msg, sig = raw.split(b".", 1)
        good = hmac.compare_digest(sig, hmac.new(_key(), msg, hashlib.sha256).digest())
        return good and int(msg) > time.time()
    except (ValueError, TypeError):
        return False


async def auth_middleware(request: Request, call_next):
    s = get_settings()
    path = request.url.path
    if s.auth_mode == "password" and path.startswith("/api") and path not in OPEN_PATHS:
        if not valid_token(request.cookies.get(COOKIE)):
            return JSONResponse({"error": {"code": "unauthorized", "message": "Sign in required."}}, status_code=401)
    return await call_next(request)


class Login(BaseModel):
    password: str


@router.get("/status")
def status(request: Request):
    s = get_settings()
    return {"mode": s.auth_mode,
            "authenticated": s.auth_mode == "none" or valid_token(request.cookies.get(COOKIE))}


@router.post("/login")
def login(body: Login, response: Response):
    s = get_settings()
    if s.auth_mode != "password":
        return {"ok": True, "mode": s.auth_mode}
    expected = s.auth_password.get_secret_value() if s.auth_password else None
    if not expected:
        raise ConfigurationError("EGI_AUTH_PASSWORD is not set.")
    if not hmac.compare_digest(body.password.encode(), expected.encode()):
        time.sleep(1.0)  # slow down guessing
        raise AuthError("Incorrect password.")
    response.set_cookie(COOKIE, issue_token(), httponly=True, samesite="strict",
                        secure=s.env == "hosted", max_age=s.session_ttl_hours * 3600)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}
