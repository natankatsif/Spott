"""The stages and tools run as processes on Mac, Windows and Linux: a UTF-8 console, and the environment of a child
stage."""

import os
import sys


def utf8_console() -> None:
    """Windows consoles default to cp1252/cp866: force UTF-8 so ș ț ă ы print instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def child_env(**extra: str) -> dict[str, str]:
    """Environment for a child Python process: UTF-8 everywhere, unbuffered logs, plus `extra`."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", **extra}
