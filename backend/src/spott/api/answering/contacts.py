"""No answer, or a partial one: one or two real contacts from the corpus that can help (docs/history/tasks/09 §4)."""

import logging

from spott.core.links import make_deep_link

from ..schemas import AnswerSentence, AskResponse, ContactCard
from .corpus import Store
from .texts import CONTACT_REASON, GENERAL_REASON, NO_ANSWER_CONTACTS, PARTIAL_CONTACTS

log = logging.getLogger("backend.answering")

CONTACT_MIN_SIMILARITY = 0.46  # question ↔ "name. area. page" (bge-m3); unrelated questions score 0.31-0.36
CONTACT_SITE_BOOST = 0.05  # the site of chunks the search found but the answer didn't use
MAX_CONTACTS = 2


def contact_card(c: dict, reason: str) -> ContactCard:
    shown = c["phone"] + c["email"]
    line = next((t for t in c.get("line_texts", []) if any(x in t for x in shown)), "")
    return ContactCard(name=c["name"], area=c.get("area"), phone=c["phone"], email=c["email"], address=c.get("address"),
                       hours=c.get("hours"), url=c["url"], site=c["site"], reason=reason, line_ids=c["line_ids"],
                       deep_link=make_deep_link(c["url"], line, None) or c["url"])


def pick_contacts(store: Store, question: str, lang: str, unused_sites: set[str]) -> list[ContactCard]:
    """The nearest contact cards above the threshold (a site the search found but the answer didn't use counts a
    little more), else the City Hall's general card; [] when the corpus has neither."""
    try:
        near, general = store.contacts_near(question)
    except Exception as e:  # a missing contacts table or model must not break the answer
        log.warning("contacts not searched: %s", e)
        return []
    scored = sorted(((c["similarity"] + (CONTACT_SITE_BOOST if c["site"] in unused_sites else 0.0), c) for c in near),
                    key=lambda sc: -sc[0])
    chosen = [c for score, c in scored if score >= CONTACT_MIN_SIMILARITY][:MAX_CONTACTS]
    if chosen:
        return [contact_card(c, CONTACT_REASON[lang].format(site=c["site"])) for c in chosen]
    return [contact_card(general, GENERAL_REASON[lang])] if general else []


def with_contacts(response: AskResponse, contacts: list[ContactCard]) -> AskResponse:
    """The not_found text becomes "we can't answer, but this contact can"; a partial answer ends with it."""
    names = ", ".join(c.name for c in contacts)
    if response.status == "not_found":
        sentences = [AnswerSentence(text=NO_ANSWER_CONTACTS[response.lang].format(names=names), cites=[])]
    else:
        sentences = response.sentences + [
            AnswerSentence(text=PARTIAL_CONTACTS[response.lang].format(names=names), cites=[])]
    return response.model_copy(update={"contacts": contacts, "sentences": sentences,
                                       "answer": " ".join(s.text for s in sentences)})
