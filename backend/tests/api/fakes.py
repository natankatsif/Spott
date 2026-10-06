"""Fakes the API tests share, no database and no network: two acts with their lines, a contacts page, a contact card,
a store and an answer model over them, an OpenAI-compatible server and the admin login."""

import json

import httpx

from spott.api import answering
from spott.api.llm import LLMResult

LOGIN = {"login": "admin", "password": "correct horse battery"}

DOC = "file:dgaurf.md/storage/d.pdf"
DECISION = {
    "chunk_id": "c1", "doc_id": DOC, "kind": "file", "lang": "ro", "site": "dgaurf.md",
    "text": "5. Taxa este de 200 lei.\n6. Termenul este de 10 zile.",
    "title": "Cu privire la taxe", "doc_type": "decizie", "number": "12/14", "date": "2020-07-28",
    "citation_label": "Decizie nr. 12/14 din 2020-07-28 › pct. 5", "legal_path": ["pct. 5"],
    "url": "https://dgaurf.md/storage/d.pdf", "found_on": "https://dgaurf.md/ro/acte",
    "pages": [2], "has_contacts": False, "block_ids": [4],
}
NEWER = DECISION | {
    "chunk_id": "c2", "doc_id": "file:dgaurf.md/storage/n.pdf", "number": "3/1", "date": "2024-01-10",
    "text": "Taxa este de 350 lei.", "citation_label": "Decizie nr. 3/1 din 2024-01-10", "legal_path": [],
    "url": "https://dgaurf.md/storage/n.pdf",
}
CONTACTS = {
    "chunk_id": "c3", "doc_id": "page:dgaurf.md/contacte", "kind": "page", "lang": "ro", "site": "dgaurf.md",
    "text": "Tel: 022 000 000", "title": "Contacte DGAURF", "url": "https://dgaurf.md/contacte",
    "found_on": "https://dgaurf.md/contacte", "has_contacts": True,
}
# A contact card as FakeStore.contacts_near returns it.
DGMU = {"contact_id": "k1", "name": "Direcția Generală Mobilitate Urbană", "area": "infrastructura urbană",
        "phone": ["022-20-46-90"], "email": ["dirtrans@pmc.md"], "address": None, "hours": None,
        "url": "https://mobilitatechisinau.md/", "site": "mobilitatechisinau.md", "line_ids": ["m1"],
        "is_general": False, "similarity": 0.53,
        "line_texts": ["ANTICAMERA TEL: 022-20-46-90 FAX: 022 -20-46-58 EMAIL: dirtrans@pmc.md"]}
BOX = {"page": 2, "l": 70.0, "t": 700.0, "r": 500.0, "b": 680.0, "origin": "BOTTOMLEFT"}
# FakeStore's lines by chunk. A test adds its own with monkeypatch.setitem: the dict is shared by every test module.
LINES = {
    "c1": [{"line_id": "l1", "idx": 0, "text": "5. Taxa este de 200 lei.", "page": 2, "bboxes": [BOX]},
           {"line_id": "l2", "idx": 1, "text": "6. Termenul este de 10 zile.", "page": 2, "bboxes": []}],
    "c2": [{"line_id": "l3", "idx": 0, "text": "Taxa este de 350 lei.", "page": 1, "bboxes": []}],
}


class FakeStore:
    def __init__(self, meta=None, following=(), has_file=True, links=(), later=(), contacts=(), general=None):
        self.meta, self.following, self.has_file = meta or {}, list(following), has_file
        self.links, self.later = list(links), list(later)
        self.contacts, self.general = list(contacts), general
        self.anchors, self.grep_patterns = [], []

    def contacts_near(self, question, limit=8):
        return self.contacts, self.general

    def chunk_meta(self, ids):
        return {i: self.meta[i] for i in ids if i in self.meta}

    def next_chunks(self, anchors):
        self.anchors += anchors
        return self.following

    def lines(self, ids):
        return {i: LINES[i] for i in ids if i in LINES}

    def documents(self, doc_ids):
        return {d: {"page_sizes": [{"n": 2, "width": 595.0, "height": 842.0}], "has_file": self.has_file}
                for d in doc_ids}

    def later_acts(self, patterns, exclude_doc_ids, limit=20):
        self.grep_patterns += patterns
        return [{"chunk_id": c["chunk_id"], "line_id": f"{c['chunk_id']}-l1"} for c in self.later]

    def grep_lines(self, keywords, limit=200):
        return [{"chunk_id": cid, "line_id": line["line_id"], "text": line["text"]}
                for cid, lines in LINES.items() for line in lines
                if any(k.casefold() in line["text"].casefold() for k in keywords)]

    def relation_lines(self, doc_ids):
        return [link for link in self.links if link["to_doc_id"] in doc_ids or link["from_doc_id"] in doc_ids]

    def dated_lines(self, doc_ids):
        return {d: [line["text"] for lines in LINES.values() for line in lines if d in line.get("doc", "")]
                for d in doc_ids}


def s(text, *refs):
    """A sentence of the answer model, backed by the given line ids."""
    return {"refs": list(refs), "text": text}


def model(verdict="answered", sentences=(), missing=(), conflict=None, checklist=None, translations=(),
          followups=(), locate=False, search_ro="", contacts=()):
    return {"verdict": verdict, "sentences": list(sentences), "missing": list(missing), "conflict": conflict,
            "checklist": checklist, "translations": list(translations), "followups": list(followups),
            "locate": locate, "search_ro": search_ro, "contacts": list(contacts)}


class FakeLLM:
    """Answers with the given outputs in turn (the last one repeats); remembers every prompt. The answer
    streams as JSON in small pieces; `finished` tells whether the stream has ended."""

    def __init__(self, *outputs, rewrite=None, piece=7):
        self.outputs, self.prompts, self.rewrite, self.piece = list(outputs), [], rewrite, piece
        self.finished = False

    @property
    def user(self):
        return self.prompts[-1] if self.prompts else None

    def next_output(self, user):
        self.prompts.append(user)
        return self.outputs[min(len(self.prompts), len(self.outputs)) - 1]

    route = None  # the routing call: unreachable unless a test sets it, so questions go to the search

    def complete_json(self, system, user, schema_name, schema, **kw):
        if schema_name == "quotes":
            if getattr(self, "quotes", None) is None:
                raise answering.LLMUnavailable("no quote translation in this test")
            return LLMResult(data={"translations": self.quotes}, model="fake-mini", prompt_tokens=20, completion_tokens=5)
        if schema_name == "route":
            if self.route is None:
                raise answering.LLMUnavailable("no routing in this test")
            return LLMResult(data=self.route, model="fake-mini", prompt_tokens=20, completion_tokens=5)
        if schema_name == "rewrite":
            if self.rewrite is None:
                raise answering.LLMUnavailable("no rewrite in this test")
            return LLMResult(data=self.rewrite, model="fake-mini", prompt_tokens=50, completion_tokens=10)
        return LLMResult(data=self.next_output(user), model="fake", prompt_tokens=100, completion_tokens=20)

    def stream_json(self, system, user, schema_name, schema, **kw):
        self.finished = False
        data = self.next_output(user)
        text = json.dumps(data, ensure_ascii=False)
        for i in range(0, len(text), self.piece):
            yield text[i:i + self.piece]
        self.finished = True
        return LLMResult(data=data, model="fake", prompt_tokens=100, completion_tokens=20)


SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
          "additionalProperties": False}


def completion(content: str, model: str = "m") -> dict:
    return {"id": "x", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}


class CompatServer:
    """An OpenAI-compatible server that rejects the options listed in `rejects` (by name in the 400 message)."""

    def __init__(self, rejects=(), reply='{"reply": "ok"}'):
        self.rejects, self.reply, self.bodies = set(rejects), reply, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        fmt = (body.get("response_format") or {}).get("type")
        for name in self.rejects:
            if name in body or (name == fmt):
                return httpx.Response(400, json={"error": {"message": f"Unsupported parameter: {name}",
                                                           "type": "invalid_request_error"}})
        if body.get("stream"):
            chunks = [{"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                       "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
                      for piece in (self.reply[:5], self.reply[5:])]
            text = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json=completion(self.reply))
