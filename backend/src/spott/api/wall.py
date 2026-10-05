"""Live "break the bot" wall: recent questions in memory (reset on restart), personal data masked."""

import re
import threading
from collections import Counter, deque
from datetime import UTC, datetime

from .schemas import AskRequest, AskResponse, WallItem, WallResponse

MAX_ITEMS = 500
MAX_QUESTION_CHARS = 200
MASK = "•••"
PERSONAL = re.compile(
    r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"  # e-mail
    r"|\+?\d[\d\s().-]{5,}\d"  # phones, IDNP, other long digit runs
)


def mask(text: str) -> str:
    masked = PERSONAL.sub(MASK, text)
    return masked if len(masked) <= MAX_QUESTION_CHARS else masked[: MAX_QUESTION_CHARS - 1] + "…"


class Wall:
    def __init__(self):
        self.items: deque[WallItem] = deque(maxlen=MAX_ITEMS)  # oldest first
        self.by_status: Counter[str] = Counter()
        self.total = 0
        self.lock = threading.Lock()

    def add(self, req: AskRequest, r: AskResponse) -> None:
        item = WallItem(
            id=r.id,
            ts=datetime.now(UTC).isoformat(timespec="seconds"),
            question=mask(req.question),
            lang=r.lang,
            status=r.status,
            verified=r.meta.verified,
            latency_ms=r.meta.latency_ms,
            top_source=r.citations[0].document_title if r.citations else None,
        )
        with self.lock:
            self.items.append(item)
            self.by_status[r.status] += 1
            self.total += 1

    def since(self, after: str | None, limit: int) -> WallResponse:
        with self.lock:
            items = list(self.items)
            ids = [i.id for i in items]
            if after in ids:
                items = items[ids.index(after) + 1:]
            return WallResponse(items=list(reversed(items))[:limit], total_questions=self.total,
                                by_status=dict(self.by_status))
