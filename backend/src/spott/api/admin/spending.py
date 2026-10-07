"""Admin → Spending: the admin's prices that turn the model calls' tokens (spott.api.usage) into money.

Prices are the admin's: per model, USD per 1M input tokens and per 1M output tokens (the unit providers publish), in
the settings table under "pricing" with the display currency (USD / EUR / MDL), its exchange rates and an optional
monthly budget. Money is computed when the report is read, so a price change re-prices the past too; a model
without a price counts its tokens but no money, and the report says so.

GET /api/admin/usage?days=30   totals (today, this month, the range, all time), a series per day, per model, per kind
GET /api/admin/usage/pricing   the prices, currency, rates and budget
PUT /api/admin/usage/pricing   saves them
"""

from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..deps import service, settings
from ..errors import ApiException
from ..usage import TZ, Group, UsageLog
from .auth import require_admin

PRICING_KEY = "pricing"
Currency = Literal["USD", "EUR", "MDL"]
# Starting values only, to be checked and edited in the admin: units of each currency per 1 USD.
DEFAULT_RATES = {"USD": 1.0, "EUR": 0.86, "MDL": 17.0}


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


router = APIRouter(prefix="/api/admin/usage", dependencies=[Depends(require_admin)])


@router.get("", response_model=UsageReport)
async def report(request: Request, days: int = Query(30, ge=1, le=366)) -> UsageReport:
    log_ = service(request, "usage")
    h = settings(request)

    def build() -> UsageReport:
        config = h.config()
        return build_report(log_, load_pricing(h.store), days, [r["model"] for r in config.roles.values() if r])

    return await run_in_threadpool(build)


@router.get("/pricing", response_model=Pricing)
async def get_pricing(request: Request) -> Pricing:
    h = settings(request)
    return await run_in_threadpool(load_pricing, h.store)


@router.put("/pricing", response_model=Pricing)
async def save_pricing(pricing: Pricing, request: Request) -> Pricing:
    h = settings(request)
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
