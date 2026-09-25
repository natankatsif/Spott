"""API contract shared with the frontend (frontend/src/lib/api.ts mirrors it)."""

from typing import Literal

from pydantic import BaseModel, Field

Lang = Literal["ro", "ru"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    lang: Lang | None = None  # None → detect from the question


class Citation(BaseModel):
    document_title: str
    url: str
    passage: str  # verbatim quote from the document
    location: str | None = None  # e.g. "Anexa 1, pct. 3.2"
    page: int | None = None
    published: str | None = None


class NavLink(BaseModel):
    title: str
    url: str


class AskResponse(BaseModel):
    # answered  — answer grounded in citations
    # not_found — corpus has no information on the question
    # conflict  — sources contradict each other; all of them are cited
    status: Literal["answered", "not_found", "conflict"]
    lang: Lang
    answer: str
    citations: list[Citation] = []
    nav_links: list[NavLink] = []
