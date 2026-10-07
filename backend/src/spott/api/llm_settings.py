"""The model settings the app runs with: the environment's (OPENAI_API_KEY, OPENAI_MODEL, ...) under what the admin
saved in the database (admin → Models, spott.api.admin.model_settings), re-read so every API process follows a
change."""

import threading
import time
from collections.abc import Callable
from typing import Protocol

from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .llm import LLMConfig, RoutedLLM

KEY = "llm"
RELOAD_S = 15.0  # another API process sees a change within this time; this one at once


class SettingsStore(Protocol):
    def get(self, key: str) -> dict | None: ...
    def set(self, key: str, value: dict) -> None: ...


class PgSettings:
    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    def get(self, key: str) -> dict | None:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = %s", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: dict) -> None:
        with self.pool.connection() as conn:
            conn.execute("INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO UPDATE "
                         "SET value = EXCLUDED.value, updated_at = NOW()", (key, Jsonb(value)))


class LLMHolder:
    """The routed client built from env + saved settings; rebuilt after a save, re-read every RELOAD_S."""

    def __init__(self, store: SettingsStore | None, on_usage: Callable[..., None] | None = None):
        self.store = store
        self.on_usage = on_usage  # every call's tokens → admin → Spending (spott/api/usage.py)
        self.lock = threading.Lock()
        self.llm: RoutedLLM | None = None
        self.saved: dict | None = None
        self.read_at = 0.0

    def saved_settings(self) -> dict:
        if self.store is None:
            return {}
        now = time.monotonic()
        if now - self.read_at > RELOAD_S:
            try:
                saved = self.store.get(KEY) or {}
            except Exception:  # noqa: BLE001 - the database hiccuped: keep what we had
                saved = self.saved or {}
            if saved != self.saved:
                self.saved, self.llm = saved, None
            self.read_at = now
        return self.saved or {}

    def config(self) -> LLMConfig:
        return LLMConfig.from_env().merged(self.saved_settings())

    def get(self) -> RoutedLLM:
        with self.lock:
            config = self.config()
            if self.llm is None:
                self.llm = RoutedLLM(config, on_usage=self.on_usage)  # LLMUnavailable: the answer provider has no key
            return self.llm

    def changed(self) -> None:
        with self.lock:
            self.llm, self.read_at = None, 0.0
