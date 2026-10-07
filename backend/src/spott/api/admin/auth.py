"""Admin login. POST /api/admin/login with ADMIN_LOGIN / ADMIN_PASSWORD from the server's env gives a session token
(12 h, signed, nothing stored); every other admin endpoint needs `Authorization: Bearer <token>` (require_admin).
Without ADMIN_LOGIN and ADMIN_PASSWORD on the server the admin is off: everything answers 401."""

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request

from ..errors import ApiException, RateLimiter, client_address
from ..schemas import AdminLogin, AdminMe, AdminSession

SESSION_HOURS = float(os.getenv("ADMIN_SESSION_HOURS", "12"))
LOGIN_ATTEMPTS_PER_MINUTE = 5
login_limiter = RateLimiter(limit=LOGIN_ATTEMPTS_PER_MINUTE)


def credentials() -> tuple[str, str] | None:
    login, password = os.getenv("ADMIN_LOGIN", ""), os.getenv("ADMIN_PASSWORD", "")
    return (login, password) if login and password else None


def signing_key(login: str, password: str) -> bytes:
    """ADMIN_SECRET if set; else derived from the credentials, so changing the password ends every session."""
    secret = os.getenv("ADMIN_SECRET") or f"{login}\0{password}"
    return hashlib.sha256(secret.encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def make_token(login: str, expires: datetime) -> str:
    creds = credentials()
    assert creds is not None
    payload = _b64(json.dumps({"sub": login, "exp": int(expires.timestamp())}).encode())
    signature = _b64(hmac.new(signing_key(*creds), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def check_token(token: str) -> str | None:
    """The login of a valid, unexpired token signed with the current credentials; None otherwise."""
    creds = credentials()
    if creds is None or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    expected = _b64(hmac.new(signing_key(*creds), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        return None
    if claims.get("sub") != creds[0] or claims.get("exp", 0) < datetime.now(UTC).timestamp():
        return None
    return claims["sub"]


def require_admin(request: Request) -> str:
    header = request.headers.get("authorization", "")
    login = check_token(header.removeprefix("Bearer ").strip()) if header.startswith("Bearer ") else None
    if login is None:
        raise ApiException(401, "unauthorized", "Admin session required: POST /api/admin/login, then "
                                                 "Authorization: Bearer <token>")
    return login


router = APIRouter(prefix="/api/admin")


@router.post("/login", response_model=AdminSession)
def login(req: AdminLogin, request: Request) -> AdminSession:
    login_limiter.check(client_address(request))
    creds = credentials()
    if creds is None:
        raise ApiException(401, "unauthorized", "Admin is off: ADMIN_LOGIN / ADMIN_PASSWORD not set on the server")
    ok_login = hmac.compare_digest(req.login.encode(), creds[0].encode())
    ok_password = hmac.compare_digest(req.password.encode(), creds[1].encode())
    if not (ok_login and ok_password):
        raise ApiException(401, "unauthorized", "Wrong login or password")
    expires = datetime.now(UTC) + timedelta(hours=SESSION_HOURS)
    return AdminSession(token=make_token(creds[0], expires), login=creds[0],
                        expires_at=expires.isoformat(timespec="seconds"))


@router.get("/me", response_model=AdminMe)
def me(login: str = Depends(require_admin)) -> AdminMe:
    """Whether the stored token is still valid (the UI checks it on load)."""
    return AdminMe(login=login)
