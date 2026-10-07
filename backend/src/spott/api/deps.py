"""What a route takes from the app: the services main.lifespan puts on app.state. A missing one is a 503 (no
database; or a test that set up only what it uses)."""

from fastapi import Request

from .errors import ApiException
from .llm import LLM, LLMUnavailable
from .llm_settings import LLMHolder


def service(request: Request, name: str, message: str = "Database not initialized"):
    """app.state.<name>, or 503 when it isn't there."""
    value = getattr(request.app.state, name, None)
    if value is None:
        raise ApiException(503, "unavailable", message)
    return value


def settings(request: Request) -> LLMHolder:
    """The model settings (admin → Models, prices), when there is a database to keep them in."""
    holder = getattr(request.app.state, "llm_holder", None)
    if holder is None or holder.store is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return holder


def llm(state) -> LLM:
    """The model client for app.state, as the environment and the admin's settings configure it; built on the first
    question, so the server starts without an API key. 503 when the answer role has no key."""
    holder: LLMHolder | None = getattr(state, "llm_holder", None)
    if holder is None:
        raise ApiException(503, "unavailable", "LLM not configured: no model settings")
    try:
        return holder.get()
    except LLMUnavailable as e:
        raise ApiException(503, "unavailable", f"LLM not configured: {e}") from e
