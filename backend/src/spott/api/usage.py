"""Admin → Spending: the tokens of every model call, and the admin's prices that turn them into money.

Every call through the routed LLM (the answer, the routing / rewrite / translation calls, the gap groups) leaves one
row in `llm_usage`: when, which provider and model, which role and which kind of call, input and output tokens and
how long it took. The row is written by a background thread, so a question never waits for it.

Prices are the admin's: per model, USD per 1M input tokens and per 1M output tokens (the unit providers publish), in
the settings table under "pricing" with the display currency (USD / EUR / MDL), its exchange rates and an optional
monthly budget. Money is computed when the report is read, so a price change re-prices the past too; a model
without a price counts its tokens but no money, and the report says so.

GET /api/admin/usage?days=30   totals (today, this month, the range, all time), a series per day, per model, per kind
GET /api/admin/usage/pricing   the prices, currency, rates and budget
PUT /api/admin/usage/pricing   saves them
"""

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Request
from psycopg_pool import ConnectionPool
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import admin
from .errors import ApiException

log = logging.getLogger(__name__)

PRICING_KEY = "pricing"
Currency = Literal["USD", "EUR", "MDL"]
# Starting values only, to be checked and edited in the admin: units of each currency per 1 USD.
DEFAULT_RATES = {"USD": 1.0, "EUR": 0.86, "MDL": 17.0}
TZ = ZoneInfo(os.getenv("USAGE_TZ") or "Europe/Chisinau")  # where "today" and "this month" start


@dataclass
class Call:
    provider: str
    model: str
    role: str       # answer | fast | deep (which role's model was used)
    kind: str       # the call's schema name: answer, route, rewrite, translate_quotes, translate, gap_groups...
    input_tokens: int
    output_tokens: int
    ms: int
    at: datetime | None = None


@dataclass
class Group:
    """Calls summed by day (in TZ), provider, model and kind."""
    day: date
    provider: str
    model: str
    kind: str
    calls: int
    input_tokens: int
    output_tokens: int


class UsageLog(Protocol):
    def record(self, call: Call) -> None: ...
    def groups(self, since: datetime | None) -> list[Group]: ...
    def questions(self, since: datetime | None) -> int: ...


# ─────────────── storage ───────────────

class PgUsage:
    """Writes in a background thread (a queue, a few rows per insert); reads group in SQL."""

    def __init__(self, pool: ConnectionPool, max_queue: int = 10_000):
        self.pool = pool
        self.queue: queue.Queue[Call] = queue.Queue(maxsize=max_queue)
        self.thread = threading.Thread(target=self._writer, name="llm-usage", daemon=True)
        self.thread.start()

    def record(self, call: Call) -> None:
        call.at = call.at or datetime.now(TZ)
        try:
            self.queue.put_nowait(call)
        except queue.Full:  # the database is down for a long time: lose the oldest accounting, not the answers
            log.warning("llm_usage queue full, a call's tokens not recorded")

    def _writer(self) -> None:
        while True:
            batch = [self.queue.get()]
            while len(batch) < 100:
                try:
                    batch.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            for attempt in range(3):
                try:
                    with self.pool.connection() as conn, conn.cursor() as cur:
                        cur.executemany(
                            "INSERT INTO llm_usage (created_at, provider, model, role, kind, input_tokens, "
                            "output_tokens, ms) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                            [(c.at, c.provider, c.model, c.role, c.kind, c.input_tokens, c.output_tokens, c.ms)
                             for c in batch])
                    break
                except Exception as e:  # noqa: BLE001 - accounting must never take the service down
                    log.warning("llm_usage write failed (%s), try %d", e, attempt + 1)
                    time.sleep(2 * (attempt + 1))

    def groups(self, since: datetime | None) -> list[Group]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT (created_at AT TIME ZONE %s)::date AS day, provider, model, kind, COUNT(*), "
                "COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0) FROM llm_usage "
                "WHERE %s::timestamptz IS NULL OR created_at >= %s GROUP BY 1, 2, 3, 4",
                (str(TZ), since, since)).fetchall()
        return [Group(*r) for r in rows]

    def questions(self, since: datetime | None) -> int:
        with self.pool.connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM answers WHERE %s::timestamptz IS NULL OR created_at >= %s",
                               (since, since)).fetchone()
        return row[0] if row else 0


class MemoryUsage:
    def __init__(self):
        self.calls: list[Call] = []
        self.answered: list[datetime] = []

    def record(self, call: Call) -> None:
        call.at = call.at or datetime.now(TZ)
        self.calls.append(call)

    def groups(self, since: datetime | None) -> list[Group]:
        out: dict[tuple, Group] = {}
        for c in self.calls:
            if since and c.at < since:
                continue
            key = (c.at.astimezone(TZ).date(), c.provider, c.model, c.kind)
            g = out.setdefault(key, Group(*key, calls=0, input_tokens=0, output_tokens=0))
            g.calls += 1
            g.input_tokens += c.input_tokens
            g.output_tokens += c.output_tokens
        return list(out.values())

    def questions(self, since: datetime | None) -> int:
        return sum(1 for t in self.answered if not since or t >= since)


# ─────────────── pricing ───────────────

class Price(BaseModel):
    input: float = Field(ge=0, le=100_000)   # USD per 1M input tokens
    output: float = Field(ge=0, le=100_000)  # USD per 1M output tokens


class Pricing(BaseModel):
    currency: Currency = "USD"
    rates: dict[Currency, float] = Field(default_factory=lambda: dict(DEFAULT_RATES))
    prices: dict[str, Price] = {}            # model name → price
    budget_usd: float | None = Field(None, ge=0)  # per calendar month, optional


def load_pricing(store) -> Pricing:
    raw = (store.get(PRICING_KEY) if store is not None else None) or {}
    try:
        p = Pricing(**raw)
    except Exception:  # noqa: BLE001 - a hand-edited or old row: start from the defaults rather than fail
        p = Pricing()
    p.rates = {**DEFAULT_RATES, **{k: v for k, v in p.rates.items() if v and v > 0}, "USD": 1.0}
    return p


def price_of(pricing: Pricing, model: str) -> Price | None:
    if model in pricing.prices:
        return pricing.prices[model]
    # a dated or suffixed name of a priced model ("gpt-4o-2024-08-06" → "gpt-4o"): the longest priced prefix
    best = max((m for m in pricing.prices if model.startswith(m + "-") or model.startswith(m + ":")), key=len,
               default=None)
    return pricing.prices[best] if best else None


def cost(price: Price | None, input_tokens: int, output_tokens: int) -> float | None:
    if price is None:
        return None
    return (input_tokens * price.input + output_tokens * price.output) / 1_000_000


# ─────────────── report ───────────────

class Totals(BaseModel):
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    unpriced_calls: int = 0   # calls of models without a price: their money isn't in cost_usd


class Day(BaseModel):
    day: date
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ModelRow(BaseModel):
    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None    # None: no price set
    price: Price | None


class KindRow(BaseModel):
    kind: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class UsageReport(BaseModel):
    days: int
    pricing: Pricing
    today: Totals
    month: Totals
    month_forecast_usd: float      # this month so far, extended to the whole month at the same daily pace
    range: Totals
    all_time: Totals
    questions: int                 # answers given in the range
    cost_per_question_usd: float | None
    daily: list[Day]
    models: list[ModelRow]         # the range's models, most expensive (then most tokens) first
    kinds: list[KindRow]
    known_models: list[str]        # models seen ever or set in admin → Models: the ones to price


def add(t: Totals, g: Group, c: float | None) -> None:
    t.calls += g.calls
    t.input_tokens += g.input_tokens
    t.output_tokens += g.output_tokens
    if c is None:
        t.unpriced_calls += g.calls
    else:
        t.cost_usd += c


def build_report(log_: UsageLog, pricing: Pricing, days: int, role_models: list[str], now: datetime | None = None
                 ) -> UsageReport:
    now = (now or datetime.now(TZ)).astimezone(TZ)
    today = now.date()
    first_day = today - timedelta(days=days - 1)
    month_start = today.replace(day=1)

    every = log_.groups(None)
    today_t, month_t, range_t, all_t = Totals(), Totals(), Totals(), Totals()
    per_day: dict[date, Day] = {first_day + timedelta(i): Day(day=first_day + timedelta(i), calls=0, input_tokens=0,
                                                             output_tokens=0, cost_usd=0.0) for i in range(days)}
    models: dict[tuple[str, str], ModelRow] = {}
    kinds: dict[str, KindRow] = {}
    seen: set[str] = set()
    for g in every:
        p = price_of(pricing, g.model)
        c = cost(p, g.input_tokens, g.output_tokens)
        seen.add(g.model)
        add(all_t, g, c)
        if g.day >= month_start:
            add(month_t, g, c)
        if g.day == today:
            add(today_t, g, c)
        if g.day < first_day:
            continue
        add(range_t, g, c)
        d = per_day.get(g.day)
        if d:
            d.calls += g.calls
            d.input_tokens += g.input_tokens
            d.output_tokens += g.output_tokens
            d.cost_usd += c or 0.0
        m = models.setdefault((g.provider, g.model), ModelRow(provider=g.provider, model=g.model, calls=0,
                                                              input_tokens=0, output_tokens=0,
                                                              cost_usd=None if p is None else 0.0, price=p))
        m.calls += g.calls
        m.input_tokens += g.input_tokens
        m.output_tokens += g.output_tokens
        if c is not None:
            m.cost_usd = (m.cost_usd or 0.0) + c
        k = kinds.setdefault(g.kind, KindRow(kind=g.kind, calls=0, input_tokens=0, output_tokens=0, cost_usd=0.0))
        k.calls += g.calls
        k.input_tokens += g.input_tokens
        k.output_tokens += g.output_tokens
        k.cost_usd += c or 0.0

    since = datetime.combine(first_day, datetime.min.time(), TZ)
    questions = log_.questions(since)
    days_in_month = ((month_start + timedelta(days=32)).replace(day=1) - month_start).days
    forecast = month_t.cost_usd / today.day * days_in_month if today.day else month_t.cost_usd
    known = sorted(seen | {m for m in role_models if m} | set(pricing.prices))
    return UsageReport(
        days=days, pricing=pricing, today=today_t, month=month_t, month_forecast_usd=forecast, range=range_t,
        all_time=all_t, questions=questions,
        cost_per_question_usd=range_t.cost_usd / questions if questions else None,
        daily=list(per_day.values()),
        models=sorted(models.values(), key=lambda m: (-(m.cost_usd or 0.0), -(m.input_tokens + m.output_tokens))),
        kinds=sorted(kinds.values(), key=lambda k: (-k.cost_usd, -(k.input_tokens + k.output_tokens))),
        known_models=known,
    )


# ─────────────── API ───────────────

router = APIRouter(prefix="/api/admin/usage", dependencies=[Depends(admin.require_admin)])


def usage_log(request: Request) -> UsageLog:
    u = getattr(request.app.state, "usage", None)
    if u is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return u


def settings_store(request: Request):
    h = getattr(request.app.state, "llm_holder", None)
    if h is None or h.store is None:
        raise ApiException(503, "unavailable", "Database not initialized")
    return h


@router.get("", response_model=UsageReport)
async def report(request: Request, days: int = Query(30, ge=1, le=366)) -> UsageReport:
    log_ = usage_log(request)
    h = settings_store(request)

    def build() -> UsageReport:
        config = h.config()
        return build_report(log_, load_pricing(h.store), days, [r["model"] for r in config.roles.values() if r])

    return await run_in_threadpool(build)


@router.get("/pricing", response_model=Pricing)
async def get_pricing(request: Request) -> Pricing:
    h = settings_store(request)
    return await run_in_threadpool(load_pricing, h.store)


@router.put("/pricing", response_model=Pricing)
async def save_pricing(pricing: Pricing, request: Request) -> Pricing:
    h = settings_store(request)
    prices = {m.strip(): p for m, p in pricing.prices.items() if m.strip()}
    if len(prices) > 500:
        raise ApiException(422, "validation_error", "too many models")
    for cur, rate in pricing.rates.items():
        if not rate or rate <= 0:
            raise ApiException(422, "validation_error", f"{cur}: the rate must be above 0")
    clean = Pricing(currency=pricing.currency, rates={**pricing.rates, "USD": 1.0}, prices=prices,
                    budget_usd=pricing.budget_usd)
    await run_in_threadpool(h.store.set, PRICING_KEY, clean.model_dump())
    return await run_in_threadpool(load_pricing, h.store)
