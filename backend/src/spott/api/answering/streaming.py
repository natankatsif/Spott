"""The SSE events of the answer's text: live, sentence by sentence while the model writes its JSON (LiveSentences), or
from a finished response (stream_sentences)."""

from collections import defaultdict
from collections.abc import Iterator

from ..jsonstream import JsonEvents
from .response import Built, ResponseBuilder


class LiveSentences:
    """The model's JSON as it streams → citation / delta / sentence events, each sentence checked when the model
    closes it. Only for an answer that will be shown (verdict answered or partial, which comes first in the JSON);
    anything else waits for the whole JSON."""

    def __init__(self, builder: ResponseBuilder):
        self.builder = builder
        self.reader = JsonEvents()
        self.live = False
        self.refs: dict[int, list[str]] = defaultdict(list)
        self.texts: dict[int, list[str]] = defaultdict(list)
        self.open: int | None = None  # model index of the sentence being streamed
        self.sent: set[str] = set()  # citation ids already sent
        self.emitted = 0  # sentence events sent

    def feed(self, piece: str) -> list[dict]:
        out: list[dict] = []
        for kind, path, *value in self.reader.feed(piece):
            if path == ("verdict",) and kind == "value":
                self.live = value[0] in ("answered", "partial")
            elif not self.live or len(path) < 2 or path[0] != "sentences" or not isinstance(path[1], int):
                continue
            elif kind == "value" and len(path) == 4 and path[2] == "refs":
                self.refs[path[1]].append(value[0])
            elif kind == "text" and path[2:] == ("text",):
                out += self.text(path[1], value[0])
            elif kind == "end" and len(path) == 2:
                out += self.close(path[1])
        return out

    def citations(self, cids: list[str]) -> list[dict]:
        by_id = {c.id: c for c in self.builder.citations}
        new = [cid for cid in cids if cid not in self.sent]
        self.sent.update(new)
        return [{"type": "citation", "citation": by_id[cid].model_dump()} for cid in new]

    def text(self, i: int, piece: str) -> list[dict]:
        self.texts[i].append(piece)
        if self.open == i:
            return [{"type": "delta", "index": len(self.builder.sentences), "text": piece}]
        so_far = "".join(self.texts[i])
        refs = self.builder.valid(self.refs[i])
        if not so_far.strip() or not refs:  # not backed (yet): shown at close if it turns out backed
            return []
        self.open = i
        return self.citations(self.builder.cite_all(refs)) + [
            {"type": "delta", "index": len(self.builder.sentences), "text": so_far.lstrip()}]

    def close(self, i: int) -> list[dict]:
        streamed = self.open == i
        self.open = None
        added = self.builder.add_sentence({"refs": self.refs[i], "text": "".join(self.texts[i])})
        if added is None:
            return []
        index, sentence, verified = added
        out = [] if streamed else self.citations(sentence.cites) + [
            {"type": "delta", "index": index, "text": sentence.text}]
        self.emitted += 1
        return out + [{"type": "sentence", "index": index, "sentence": sentence.model_dump(), "verified": verified}]


def stream_sentences(built: Built, start: int = 0, sent: set[str] | None = None) -> Iterator[dict]:
    """citation* → delta* → sentence for the sentences from `start` on (the ones not streamed live);
    citations first sent right before their first use."""
    r = built.response
    by_id = {c.id: c for c in r.citations}
    sent = set(sent or ())
    for index, sentence in enumerate(r.sentences):
        if index < start:
            continue
        for cid in sentence.cites:
            if cid not in sent:
                sent.add(cid)
                yield {"type": "citation", "citation": by_id[cid].model_dump()}
        for word in sentence.text.split(" "):
            yield {"type": "delta", "index": index, "text": word + " "}
        verified = built.sentence_verified[index] if index < len(built.sentence_verified) else r.meta.verified
        yield {"type": "sentence", "index": index, "sentence": sentence.model_dump(), "verified": verified}
    for c in r.citations:  # cited only by checklist steps or the conflict
        if c.id not in sent:
            yield {"type": "citation", "citation": c.model_dump()}
