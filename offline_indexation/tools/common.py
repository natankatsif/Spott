"""Shared bits for tools: paths, UTF-8 console, docker/db settings."""

import os
import sys
from pathlib import Path

OI_DIR = Path(__file__).resolve().parents[1]  # offline_indexation/
ROOT = OI_DIR.parent  # repo root
CONTAINER = os.getenv("POSTGRES_CONTAINER", "qwerty-pgvector")


def utf8_console() -> None:
    """Windows consoles default to cp1252/cp866: force UTF-8 so ș ț ă ы print instead of crashing."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def db_env() -> dict[str, str]:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    return {
        "user": os.getenv("POSTGRES_USER", "qwerty"),
        "db": os.getenv("POSTGRES_DB", "qwerty"),
    }


def child_env() -> dict[str, str]:
    """Environment for child Python processes: UTF-8 everywhere, unbuffered logs."""
    return {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
