"""Every non-2xx response has the ApiError body (docs/API.md → Errors), plus the rate limit for questions."""

import ipaddress
import logging
import time
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .schemas import ApiError, ErrorCode

log = logging.getLogger("backend.errors")

CODE_BY_STATUS: dict[int, ErrorCode] = {
    401: "unauthorized", 404: "not_found", 409: "conflict", 422: "validation_error", 429: "rate_limited",
    501: "not_implemented", 503: "unavailable",
}


class ApiException(Exception):
    def __init__(self, status: int, code: ErrorCode, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.retry_after_s = status, code, message, retry_after_s


def error_response(status: int, code: ErrorCode, message: str, retry_after_s: float | None = None) -> JSONResponse:
    headers = {"Retry-After": str(int(retry_after_s))} if retry_after_s else None
    body = ApiError(error=code, message=message, retry_after_s=retry_after_s)
    return JSONResponse(status_code=status, content=body.model_dump(), headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiException)
    async def api_exception(_: Request, e: ApiException) -> JSONResponse:
        return error_response(e.status, e.code, e.message, e.retry_after_s)

    @app.exception_handler(RequestValidationError)
    async def validation(_: Request, e: RequestValidationError) -> JSONResponse:
        first = e.errors()[0] if e.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())[1:])
        return error_response(422, "validation_error", f"{where}: {first.get('msg', 'invalid request')}".strip(": "))

    @app.exception_handler(StarletteHTTPException)
    async def http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        return error_response(e.status_code, CODE_BY_STATUS.get(e.status_code, "internal"), str(e.detail))

    @app.exception_handler(Exception)
    async def unexpected(_: Request, e: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error_response(500, "internal", "Internal error")


def client_address(request: Request) -> str:
    """Who is asking, for the rate limits. X-Forwarded-For is written by the client and only appended to by
    proxies, so it is believed only when the connection itself comes from a proxy on a private network (Caddy or
    cloudflared in Docker), and then its last address, the one our proxy added. A client reaching the API directly
    is counted by its own address whatever header it sends."""
    peer = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(peer)
        via_proxy = ip.is_private or ip.is_loopback
    except ValueError:
        via_proxy = False
    if via_proxy:
        hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
        if hops:
            return hops[-1]
    return peer


class RateLimiter:
    """At most `limit` questions per `window_s` per client (a QR stand must not flood the model)."""

    MAX_CLIENTS = 10_000  # remembered clients before the idle ones are forgotten

    def __init__(self, limit: int = 10, window_s: float = 60.0):
        self.limit, self.window_s = limit, window_s
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, client: str) -> None:
        now = time.monotonic()
        if len(self.hits) > self.MAX_CLIENTS:  # forget clients with no hit inside the window
            for key in [k for k, v in self.hits.items() if not v or v[-1] <= now - self.window_s]:
                del self.hits[key]
        hits = self.hits[client]
        while hits and hits[0] <= now - self.window_s:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = round(hits[0] + self.window_s - now, 1)
            raise ApiException(429, "rate_limited", f"Too many questions, retry in {retry:.0f} s", retry)
        hits.append(now)
