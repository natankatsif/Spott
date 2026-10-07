"""Quotes in another language than the answer that the answer model left untranslated, translated by the small
model."""

import logging

from ..languages import LANGUAGE_NAMES
from ..llm import FAST, LLM
from ..schemas import AskResponse
from .prompts import TRANSLATE_QUOTES_PROMPT, TRANSLATE_QUOTES_SCHEMA

log = logging.getLogger("backend.answering")

def translate_missing(llm: LLM | None, response: AskResponse) -> AskResponse:
    """Quotes in another language than the answer that the answer model left untranslated get a translation from
    the small model (one call); the reader of an English or Russian answer can't read a Romanian quote. A failed
    call leaves them as they are."""
    todo = [c for c in response.citations if c.quote_lang != response.lang and not c.translation]
    if not todo or llm is None:
        return response
    try:
        r = llm.complete_json(TRANSLATE_QUOTES_PROMPT.format(language=LANGUAGE_NAMES[response.lang]),
                              "\n".join(f"{i + 1}. {c.quote}" for i, c in enumerate(todo)), "quotes",
                              TRANSLATE_QUOTES_SCHEMA, model=FAST, effort="none",
                              max_tokens=200 + 120 * len(todo))
        texts = [t.strip() for t in r.data.get("translations") or []]
    except Exception as e:  # noqa: BLE001 - untranslated is still a correct answer
        log.warning("quotes not translated: %s", e)
        return response
    done = {c.id: t for c, t in zip(todo, texts, strict=False) if t}
    if not done:
        return response
    return response.model_copy(update={"citations": [
        c.model_copy(update={"translation": done[c.id]}) if c.id in done else c for c in response.citations]})
