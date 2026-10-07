"""Admin → Spending: tokens counted per routed call, priced by the admin, summed per day / model / kind."""

from datetime import datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.api.fakes import SCHEMA, CompatServer

from spott.api import llm_settings, main, usage
from spott.api.llm import LLMConfig, OpenAILLM, RoutedLLM

AUTH: dict[str, str] = {}


def routed(on_usage) -> RoutedLLM:
    config = LLMConfig(providers={"openai": {"api_key": "sk"}},
                       roles={"answer": {"provider": "openai", "model": "gpt-4o"},
                              "fast": {"provider": "openai", "model": "gpt-6-luna"}})
    r = RoutedLLM(config, on_usage=on_usage)
    r.clients["openai"] = OpenAILLM("gpt-4o", api_key="sk", base_url="http://own.test/v1",
                                    http_client=httpx.Client(transport=httpx.MockTransport(CompatServer())))
    return r


def test_every_routed_call_is_counted_with_its_role_and_kind():
    seen = []
    r = routed(lambda *a: seen.append(a))
    r.complete_json("sys", "hi", "rewrite", SCHEMA, model="@fast")
    stream = r.stream_json("sys", "hi", "answer", SCHEMA)
    for _ in stream:
        pass
    (p1, _, role1, kind1, res1, ms1), (p2, _, role2, kind2, _, _) = seen
    assert (p1, role1, kind1) == ("openai", "fast", "rewrite") and res1.prompt_tokens == 3 and ms1 >= 0
    assert (p2, role2, kind2) == ("openai", "answer", "answer")


def test_a_failing_counter_never_breaks_the_answer():
    def boom(*_):
        raise RuntimeError("db down")

    assert routed(boom).complete_json("sys", "hi", "route", SCHEMA).data == {"reply": "ok"}


def test_prices_turn_tokens_into_money_and_unpriced_models_are_flagged():
    log = usage.MemoryUsage()
    now = datetime(2026, 9, 27, 12, tzinfo=usage.TZ)
    for days_ago, model, kind, i, o in [(0, "gpt-4o-2024-08-06", "answer", 1_000_000, 100_000),
                                        (0, "gpt-6-luna", "rewrite", 200_000, 10_000),
                                        (3, "gpt-4o", "answer", 500_000, 50_000),
                                        (40, "gpt-4o", "answer", 1_000_000, 0)]:
        log.record(usage.Call("openai", model, "answer", kind, i, o, 10, at=now - timedelta(days=days_ago)))
    log.answered = [now, now - timedelta(days=1)]
    pricing = usage.Pricing(prices={"gpt-4o": usage.Price(input=2.5, output=10)})
    rep = usage.build_report(log, pricing, 30, ["gpt-4o", "claude-sonnet-5"], now=now)

    assert rep.today.calls == 2 and rep.today.unpriced_calls == 1
    assert rep.today.cost_usd == pytest.approx(2.5 + 1.0)            # the dated gpt-4o name uses gpt-4o's price
    assert rep.range.cost_usd == pytest.approx(3.5 + 1.25 + 0.5)
    assert rep.all_time.cost_usd == pytest.approx(3.5 + 1.75 + 2.5)
    assert len(rep.daily) == 30 and rep.daily[-1].day == now.date() and rep.daily[-1].cost_usd == pytest.approx(3.5)
    assert rep.questions == 2 and rep.cost_per_question_usd == pytest.approx(5.25 / 2)
    luna = next(m for m in rep.models if m.model == "gpt-6-luna")
    assert luna.cost_usd is None and luna.price is None and rep.models[0].model.startswith("gpt-4o")
    assert [k.kind for k in rep.kinds] == ["answer", "rewrite"]
    assert "claude-sonnet-5" in rep.known_models and "gpt-6-luna" in rep.known_models
    assert rep.month_forecast_usd == pytest.approx(rep.month.cost_usd / 27 * 30)


@pytest.fixture
def api(monkeypatch):
    for name in ("OPENAI_MODEL", "OPENAI_MODEL_DEEP", "OPENAI_REWRITE_MODEL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ADMIN_LOGIN", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "secret-pass")
    monkeypatch.delenv("ADMIN_SECRET", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-123456789")
    store = llm_settings.MemorySettings()
    log = usage.MemoryUsage()
    main.app.state.llm_holder = llm_settings.LLMHolder(store, on_usage=main.record_usage)
    main.app.state.usage = log
    c = TestClient(main.app)
    AUTH["Authorization"] = "Bearer " + c.post("/api/admin/login", json={"login": "admin",
                                                                         "password": "secret-pass"}).json()["token"]
    return c, store, log


def test_pricing_is_saved_and_the_report_uses_it(api):
    c, store, log = api
    assert c.get("/api/admin/usage").status_code == 401
    assert c.get("/api/admin/usage/pricing", headers=AUTH).json()["currency"] == "USD"
    saved = c.put("/api/admin/usage/pricing", headers=AUTH, json={
        "currency": "MDL", "rates": {"EUR": 0.9, "MDL": 17.5}, "budget_usd": 20,
        "prices": {" gpt-4o ": {"input": 2.5, "output": 10}, "": {"input": 1, "output": 1}}}).json()
    assert saved["currency"] == "MDL" and saved["rates"]["USD"] == 1.0 and list(saved["prices"]) == ["gpt-4o"]
    assert store.get("pricing")["budget_usd"] == 20

    main.record_usage("openai", "gpt-4o", "answer", "answer",
                      type("R", (), {"prompt_tokens": 400_000, "completion_tokens": 20_000})(), 900)
    rep = c.get("/api/admin/usage?days=7", headers=AUTH).json()
    assert rep["today"]["cost_usd"] == pytest.approx(1.2) and rep["pricing"]["currency"] == "MDL"
    assert len(rep["daily"]) == 7 and rep["models"][0]["price"] == {"input": 2.5, "output": 10}
    assert "gpt-4o" in rep["known_models"]


def test_bad_rates_are_refused(api):
    c, _, _ = api
    r = c.put("/api/admin/usage/pricing", headers=AUTH, json={"currency": "EUR", "rates": {"EUR": 0}, "prices": {}})
    assert r.status_code == 422
