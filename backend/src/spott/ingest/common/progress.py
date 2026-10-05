"""Progress of a pipeline stage for the admin worker (python -m spott.ingest.worker).

A stage run by the worker gets two environment variables:
    PROGRESS_FILE  the stage writes {"done", "total", "errors"} there (at most every 0.5 s, and at the end);
    CANCEL_FILE    appears when the admin cancels the job: the stage stops after the current item.
Run by hand, without them, Progress does nothing.

    progress = Progress(total=len(items))
    for item in items:
        if progress.cancelled():
            break
        ...
        progress.advance()
    progress.finish()
"""

import json
import os
import time
from pathlib import Path

WRITE_EVERY_S = 0.5


class Progress:
    def __init__(self, total: int = 0):
        self.file = Path(p) if (p := os.getenv("PROGRESS_FILE")) else None
        self.cancel_file = Path(p) if (p := os.getenv("CANCEL_FILE")) else None
        self.total, self.done, self.errors = total, 0, 0
        self.written = 0.0
        self._write(force=True)

    def set_total(self, total: int) -> None:
        self.total = total
        self._write(force=True)

    def update(self, done: int, total: int) -> None:
        """Both counters at once, for a stage whose total grows as it goes (the crawler's queue)."""
        self.done, self.total = done, total
        self._write()

    def advance(self, n: int = 1, error: bool = False) -> None:
        self.done += n
        self.errors += int(error)
        self._write()

    def cancelled(self) -> bool:
        return bool(self.cancel_file and self.cancel_file.exists())

    def finish(self) -> None:
        self._write(force=True)

    def _write(self, force: bool = False) -> None:
        if self.file is None or (not force and time.monotonic() - self.written < WRITE_EVERY_S):
            return
        self.written = time.monotonic()
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"done": self.done, "total": self.total, "errors": self.errors}), encoding="utf-8")
        os.replace(tmp, self.file)  # the worker never reads a half-written file


def read_progress(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
