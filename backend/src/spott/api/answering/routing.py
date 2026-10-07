"""The first step, alongside the search: whether the message needs the documents at all (a small model decides)."""

import logging

from ..languages import LANGUAGE_NAMES
from ..llm import FAST, LLM, LLMUnavailable
from ..schemas import AskRequest
from .prompts import ROUTE_PROMPT, ROUTE_SCHEMA, conversation, today_line

log = logging.getLogger("backend.answering")

ROUTE_STATUS = {"chat": "answered", "clarify": "answered", "off_topic": "refused"}
MAX_ROUTE_OPTIONS = 4


def route_question(llm: LLM, req: AskRequest, lang: str) -> dict | None:
    """{"route", "reply", "options"} from the small model; None if it can't be reached or says nothing usable."""
    user = today_line() + conversation(req) + f"Latest message: {req.question}"
    try:
        r = llm.complete_json(ROUTE_PROMPT.format(language=LANGUAGE_NAMES[lang]), user, "route", ROUTE_SCHEMA,
                              model=FAST, effort="none", max_tokens=400)
    except LLMUnavailable as e:
        log.warning("routing failed: %s", e)
        return None
    route, reply = r.data.get("route"), (r.data.get("reply") or "").strip()
    if route not in ("search", *ROUTE_STATUS) or (route != "search" and not reply):
        return None
    options = [o.strip() for o in r.data.get("options") or [] if isinstance(o, str) and o.strip()]
    return {"route": route, "reply": reply, "options": options[:MAX_ROUTE_OPTIONS], "model": r.model}
