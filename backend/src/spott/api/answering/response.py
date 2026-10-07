"""The model's JSON → AskResponse. Sentences are kept only with valid line ids and their quotes come from the index,
numbers are checked against those quotes; citations, conflict, checklist, contacts and links are built by code."""

from dataclasses import dataclass, field

from spott.core.links import make_deep_link

from ..pdf_source import is_pdf_url
from ..preview import file_url, preview_kind, preview_url, to_top_left
from ..schemas import (
    AnswerMeta,
    AnswerSentence,
    AskResponse,
    Checklist,
    ChecklistStep,
    Citation,
    ConflictInfo,
    ContactCard,
    NavLink,
    TraceStep,
)
from .chunks import document_title, quote_lang
from .claims import copied_from, numbers_backed
from .sources import Source
from .texts import NOT_FOUND, REFUSED, SOURCE_PAGE

MAX_NAV_LINKS = 3
MAX_FOLLOWUPS = 3
MAX_CONTACTS_IN_ANSWER = 3  # the ones the answer model copies from the lines when asked where to go


@dataclass
class Built:
    response: AskResponse
    sentence_verified: list[bool] = field(default_factory=list)
    dropped: int = 0


class ResponseBuilder:
    """The model's JSON → AskResponse. Sentences can be added one by one while the answer streams
    (add_sentence); build() then takes the rest of the JSON."""

    def __init__(self, sources: list[Source], docs: dict[str, dict], lang: str, answer_id: str):
        self.sources = sources
        self.lines = {f"{s.ref}.L{n}": (s, line) for s in sources for n, line in s.lines}
        self.docs = docs
        self.lang = lang
        self.answer_id = answer_id
        self.translations: dict[str, str] = {}
        self.citations: list[Citation] = []
        self.cited: dict[str, str] = {}  # line ref → citation id
        self.first_ref: dict[str, str] = {}  # citation id → the line ref it was made from
        self.sentences: list[AnswerSentence] = []
        self.verified: list[bool] = []
        self.consumed = 0  # model sentences taken so far, backed or not
        self.dropped = 0

    def cite_all(self, refs: list[str]) -> list[str]:
        return list(dict.fromkeys(self.cite(r) for r in refs))

    def valid(self, refs: list[str] | None) -> list[str]:
        return [r for r in dict.fromkeys(refs or []) if r in self.lines]

    def cite(self, ref: str) -> str:
        if ref not in self.cited:
            source, line = self.lines[ref]
            # The same line repeated on many pages of a site (footer contacts) is one citation.
            same = next((c.id for c in self.citations if c.quote == line["text"] and c.site == source.chunk.get("site")),
                        None)
            if same is None:
                self.citations.append(self.make_citation(f"c{len(self.citations) + 1}", source, line))
                same = self.citations[-1].id
                self.first_ref[same] = ref
            self.cited[ref] = same
        return self.cited[ref]

    def add_sentence(self, item: dict) -> tuple[int, AnswerSentence, bool] | None:
        """A model sentence, checked: (index in the answer, sentence, numbers backed); None if dropped."""
        self.consumed += 1
        if not (b := self.backed(item)):
            return None
        text, refs = b
        self.sentences.append(AnswerSentence(text=text, cites=self.cite_all(refs)))
        self.verified.append(numbers_backed(text, self.evidence(refs)))
        return len(self.sentences) - 1, self.sentences[-1], self.verified[-1]

    def make_citation(self, cid: str, source: Source, line: dict) -> Citation:
        c = source.chunk
        doc = self.docs.get(c["doc_id"], {})
        url = c.get("url") or ""
        page = line.get("page") or (c.get("pages") or [None])[0]
        kind = "file" if c.get("kind") == "file" else "page"
        lang = quote_lang(c)
        boxes = line.get("bboxes") or [b for b in c.get("bboxes") or [] if b.get("page") == page]
        return Citation(
            id=cid,
            doc_id=c["doc_id"],
            chunk_id=c["chunk_id"],
            line_ids=[line["line_id"]] if line.get("line_id") else [],
            kind=kind,
            document_title=document_title(c),
            doc_type=c.get("doc_type"),
            act_number=c.get("number"),
            published=c.get("date"),
            location=" › ".join(c.get("legal_path") or []) or None,
            page=page if kind == "file" else None,
            quote=line["text"],
            quote_lang=lang,
            translation=None,  # set by build(): the model writes translations after the sentences
            url=url,
            deep_link=make_deep_link(url, line["text"], page if kind == "file" else None) or url,
            found_on=c.get("found_on"),
            site=c.get("site"),
            file_url=file_url(c["doc_id"]) if kind == "file" and (doc.get("has_file") or is_pdf_url(url)) else None,
            bboxes=to_top_left(boxes, doc.get("page_sizes") or []) if kind == "file" else [],
            preview_url=preview_url(c["doc_id"], [line["line_id"]] if line.get("line_id") else [], self.lang),
            preview_kind=preview_kind(c.get("kind") or "page", url, bool(doc.get("has_file"))),
        )

    def evidence(self, refs: list[str]) -> list[str]:
        out = []
        for r in refs:
            source, line = self.lines[r]
            c = source.chunk
            out += [line["text"], document_title(c), c.get("citation_label") or "", c.get("date") or ""]
        return out

    def backed(self, item: dict | None) -> tuple[str, list[str]] | None:
        """(text, refs) of a model item whose refs exist; None if it can't be backed."""
        if not item or not (text := (item.get("text") or "").strip()):
            return None
        refs = self.valid(item.get("refs"))
        if not refs:
            self.dropped += 1
            return None
        return text, refs

    def build(self, data: dict, retrieved: list[dict], meta: AnswerMeta, trace: list[TraceStep]) -> Built:
        self.translations = {t["ref"]: t["text"] for t in data.get("translations") or []}
        verdict = data.get("verdict")
        if verdict == "refused":
            return Built(self.fixed("refused", REFUSED[self.lang], [], meta.model_copy(update={"verified": False}),
                                    trace))

        for item in (data.get("sentences") or [])[self.consumed:]:
            self.add_sentence(item)
        sentences, verified = list(self.sentences), list(self.verified)

        checklist_verified: list[bool] = []
        checklist = self.checklist(data.get("checklist"), checklist_verified)
        if verdict == "not_found" or not (sentences or checklist):
            return Built(self.not_found(retrieved, meta, trace), dropped=self.dropped)
        if not sentences:  # a checklist needs at least its title as text
            sentences.append(AnswerSentence(text=checklist.title, cites=[]))
            verified.append(True)
        conflict = None
        c = data.get("conflict")
        if c and len(refs := self.valid(c.get("refs"))) >= 2:
            preferred = c.get("preferred_ref")
            conflict = ConflictInfo(kind=c["kind"], explanation=c["explanation"],
                                    citation_ids=self.cite_all(refs),
                                    preferred_citation_id=self.cite(preferred) if preferred in self.lines else None)

        partial = verdict == "partial"
        if partial:
            for text in data.get("missing") or []:
                if text.strip():
                    sentences.append(AnswerSentence(text=text.strip(), cites=[]))
                    verified.append(True)

        contacts = self.contacts(data.get("contacts"))
        self.citations = [c.model_copy(update={"translation": self.translations.get(self.first_ref[c.id])})
                          if c.quote_lang != self.lang else c for c in self.citations]
        cited_chunks = [self.lines[r][0].chunk for r in self.cited]
        followups = [f.strip() for f in data.get("followups") or [] if f.strip()][:MAX_FOLLOWUPS]
        response = AskResponse(
            id=self.answer_id,
            status="conflict" if conflict else "partial" if partial else "answered",
            lang=self.lang,
            answer=" ".join(s.text for s in sentences),
            sentences=sentences,
            citations=self.citations,
            conflict=conflict,
            checklist=checklist,
            nav_links=self.nav_links(cited_chunks),
            followups=followups,
            trace=trace,
            meta=meta.model_copy(update={"verified": all(verified + checklist_verified)}),
            focus_citation_id=self.focus(sentences) if data.get("locate") else None,
            contacts=contacts,
        )
        return Built(response, verified, self.dropped)

    def focus(self, sentences: list[AnswerSentence]) -> str | None:
        """First citation of the answer, preferring one the viewer can open (a PDF)."""
        ordered = [cid for s in sentences for cid in s.cites]
        by_id = {c.id: c for c in self.citations}
        return next((cid for cid in ordered if by_id[cid].file_url), ordered[0] if ordered else None)

    def checklist(self, data: dict | None, verified: list[bool]) -> Checklist | None:
        if not data:
            return None
        steps = []
        for item in data.get("steps") or []:
            if b := self.backed(item):
                steps.append(ChecklistStep(text=b[0], cites=self.cite_all(b[1])))
                verified.append(numbers_backed(b[0], self.evidence(b[1])))
        if not steps:
            return None

        def backed_text(item: dict | None) -> str | None:
            if b := self.backed(item):
                for r in b[1]:
                    self.cite(r)
                verified.append(numbers_backed(b[0], self.evidence(b[1])))
                return b[0]
            return None

        return Checklist(
            title=(data.get("title") or "").strip() or steps[0].text,
            steps=steps,
            documents_needed=[t for item in data.get("documents_needed") or [] if (t := backed_text(item))],
            fee=backed_text(data.get("fee")),
            deadline=backed_text(data.get("deadline")),
        )

    def contacts(self, items: list[dict] | None) -> list[ContactCard]:
        """Where to call or go, as the model copied it from the lines: a phone or e-mail is kept only if it is in
        the cited lines, an address or hours only if their numbers are; a card with nothing left is dropped."""
        cards = []
        for item in (items or [])[:MAX_CONTACTS_IN_ANSWER]:
            refs = self.valid(item.get("refs"))
            name = (item.get("name") or "").strip()
            if not refs or not name:
                continue
            texts = [self.lines[r][1]["text"] for r in refs]
            digits = ["".join(ch for ch in t if ch.isdigit()) for t in texts]
            joined = " ".join(texts).casefold()
            phone = [p.strip() for p in item.get("phone") or []
                     if len(d := "".join(ch for ch in p if ch.isdigit())) >= 5 and any(d in x for x in digits)]
            email = [e.strip() for e in item.get("email") or [] if e.strip() and e.strip().casefold() in joined]
            address, hours = ((v or "").strip() or None for v in (item.get("address"), item.get("hours")))
            address = address if address and copied_from(address, texts) else None
            hours = hours if hours and copied_from(hours, texts) else None
            if not (phone or email or address):
                continue
            for r in refs:
                self.cite(r)
            source, first = self.lines[refs[0]]
            c = source.chunk
            url = c.get("url") or ""
            cards.append(ContactCard(
                name=name, area=None, phone=phone, email=email, address=address, hours=hours, url=url,
                site=c.get("site") or "", reason="", line_ids=[lid for r in refs if (lid := self.lines[r][1].get("line_id"))],
                deep_link=make_deep_link(url, first["text"], first.get("page") if c.get("kind") == "file" else None)
                or url))
        return cards

    def nav_links(self, chunks: list[dict]) -> list[NavLink]:
        """Where to go on the city hall sites: the cited pages, or the pages that publish the cited files."""
        links: dict[str, NavLink] = {}
        for c in chunks:
            if c.get("kind") == "file":
                url, title = c.get("found_on") or c.get("url"), SOURCE_PAGE[self.lang].format(site=c.get("site"))
            else:
                url, title = c.get("url"), c.get("title") or c.get("site") or c.get("url")
            if url and url not in links:
                links[url] = NavLink(title=title or url, url=url,
                                     kind="contact" if c.get("has_contacts") else "page")
        return list(links.values())[:MAX_NAV_LINKS]

    def fixed(self, status: str, text: str, nav: list[NavLink], meta: AnswerMeta,
              trace: list[TraceStep]) -> AskResponse:
        return AskResponse(id=self.answer_id, status=status, lang=self.lang, answer=text,
                           sentences=[AnswerSentence(text=text, cites=[])], citations=[], conflict=None,
                           checklist=None, nav_links=nav, followups=[], trace=trace, meta=meta)

    def not_found(self, retrieved: list[dict], meta: AnswerMeta, trace: list[TraceStep]) -> AskResponse:
        # Nearest pages with contacts, so the person isn't left at a dead end.
        contacts = [c for c in retrieved if c.get("has_contacts")]
        self.citations, self.cited = [], {}
        return self.fixed("not_found", NOT_FOUND[self.lang], self.nav_links(contacts),
                          meta.model_copy(update={"verified": True}), trace)
