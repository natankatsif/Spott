"""Providers (OpenAI-compatible, Anthropic) against fake servers, the role routing, and admin → Models."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.api.fakes import SCHEMA, CompatServer, MemorySettings

from spott.api import llm, llm_settings, main
from spott.api.llm import AnthropicLLM, LLMConfig, LLMUnavailable, OpenAILLM, RoutedLLM


def compat(server, **kw) -> OpenAILLM:
    return OpenAILLM("m", api_key="k", base_url="http://own.test/v1",
                     http_client=httpx.Client(transport=httpx.MockTransport(server)), **kw)


def test_openai_compatible_strict_schema():
    server = CompatServer()
    r = compat(server).complete_json("sys", "hi", "probe", SCHEMA, max_tokens=50)
    assert r.data == {"reply": "ok"} and r.prompt_tokens == 3
    body = server.bodies[0]
    assert body["response_format"]["type"] == "json_schema" and body["response_format"]["json_schema"]["strict"]
    assert body["temperature"] == 0 and body["max_completion_tokens"] == 50


def test_server_without_schema_support_falls_back_to_json_mode_then_prompt():
    server = CompatServer(rejects={"json_schema", "json_object"}, reply='```json\n{"reply": "ok"}\n```')
    client = compat(server)
    assert client.complete_json("sys", "hi", "probe", SCHEMA).data == {"reply": "ok"}
    assert [(b.get("response_format") or {}).get("type") for b in server.bodies] == ["json_schema", "json_object",
                                                                                      None]
    assert "JSON schema" in server.bodies[-1]["messages"][0]["content"]
    assert client.format["m"] == "prompt"  # remembered: the next call goes straight to the prompt


def test_rejected_options_are_dropped_and_remembered():
    server = CompatServer(rejects={"reasoning_effort", "temperature", "max_completion_tokens"})
    client = compat(server)
    client.complete_json("sys", "hi", "probe", SCHEMA, max_tokens=40)
    last = server.bodies[-1]
    assert "reasoning_effort" not in last and "temperature" not in last and last["max_tokens"] == 40
    server.bodies.clear()
    client.complete_json("sys", "hi", "probe", SCHEMA, max_tokens=40)
    assert len(server.bodies) == 1


def test_openai_compatible_stream_without_stream_options():
    server = CompatServer(rejects={"stream_options"})
    gen = compat(server).stream_json("sys", "hi", "probe", SCHEMA)
    pieces = []
    try:
        while True:
            pieces.append(next(gen))
    except StopIteration as done:
        result = done.value
    assert "".join(pieces) == '{"reply": "ok"}' and result.data == {"reply": "ok"}
    assert "stream_options" not in server.bodies[-1]


class ClaudeServer:
    def __init__(self, reject_temperature=False, status=200):
        self.reject_temperature, self.status, self.bodies, self.headers = reject_temperature, status, [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-haiku-4-5"}]})
        body = json.loads(request.content)
        self.bodies.append(body)
        self.headers.append(request.headers)
        if self.status != 200:
            return httpx.Response(self.status, json={"type": "error", "error": {"message": "overloaded"}})
        if self.reject_temperature and "temperature" in body:
            return httpx.Response(400, json={"type": "error", "error": {
                "type": "invalid_request_error", "message": "temperature is not supported for this model"}})
        if body.get("stream"):
            events = [
                {"type": "message_start", "message": {"model": "claude-sonnet-5", "usage": {"input_tokens": 7}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '{"reply": '}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '"ok"}'}},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
                {"type": "message_stop"},
            ]
            text = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"model": "claude-sonnet-5", "content": [{"type": "text",
                                                                                 "text": '{"reply": "ok"}'}],
                                         "usage": {"input_tokens": 7, "output_tokens": 4}})


def claude(server) -> AnthropicLLM:
    return AnthropicLLM("claude-sonnet-5", "sk-ant", transport=httpx.MockTransport(server))


def test_claude_structured_output_request():
    server = ClaudeServer()
    r = claude(server).complete_json("sys", "hi", "probe", SCHEMA, max_tokens=100)
    assert r.data == {"reply": "ok"} and r.model == "claude-sonnet-5" and r.prompt_tokens == 7
    body, headers = server.bodies[0], server.headers[0]
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": SCHEMA}}
    assert body["system"] == "sys" and body["max_tokens"] == 100 and body["temperature"] == 0
    assert headers["x-api-key"] == "sk-ant" and headers["anthropic-version"] == "2023-06-01"


def test_claude_model_without_temperature():
    server = ClaudeServer(reject_temperature=True)
    client = claude(server)
    assert client.complete_json("sys", "hi", "probe", SCHEMA).data == {"reply": "ok"}
    assert "temperature" not in server.bodies[-1]
    client.complete_json("sys", "hi", "probe", SCHEMA)
    assert len(server.bodies) == 3  # the second call goes without it at once


def test_claude_stream():
    gen = claude(ClaudeServer()).stream_json("sys", "hi", "probe", SCHEMA)
    pieces = []
    try:
        while True:
            pieces.append(next(gen))
    except StopIteration as done:
        result = done.value
    assert pieces == ['{"reply": ', '"ok"}'] and result.data == {"reply": "ok"}
    assert (result.prompt_tokens, result.completion_tokens) == (7, 4)


def test_claude_errors_are_unavailable(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda s: None)
    with pytest.raises(LLMUnavailable, match="529: overloaded"):
        claude(ClaudeServer(status=529)).complete_json("sys", "hi", "probe", SCHEMA)


def test_claude_models():
    assert claude(ClaudeServer()).models() == ["claude-sonnet-5", "claude-haiku-4-5"]


# ─────────────── configuration and roles ───────────────

@pytest.fixture
def clean_env(monkeypatch):
    for name in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_MODEL_DEEP", "OPENAI_REWRITE_MODEL", "ANTHROPIC_API_KEY",
                 "GEMINI_API_KEY", "GOOGLE_API_KEY", "LLM_CUSTOM_BASE_URL", "LLM_CUSTOM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_env_config_then_admin_settings_win(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-env")
    clean_env.setenv("OPENAI_MODEL", "gpt-4o")
    env = LLMConfig.from_env()
    assert env.roles["answer"] == {"provider": "openai", "model": "gpt-4o"} and "deep" not in env.roles
    merged = env.merged({"providers": {"anthropic": {"api_key": "sk-ant"}, "openai": {"api_key": ""}},
                         "roles": {"answer": {"provider": "anthropic", "model": "claude-sonnet-5"}, "deep": None}})
    assert merged.providers["openai"]["api_key"] == "sk-env"  # an empty saved key keeps the env's
    assert merged.roles["answer"]["provider"] == "anthropic" and merged.roles["fast"]["provider"] == "openai"


def test_roles_route_to_their_providers(clean_env):
    config = LLMConfig(providers={"openai": {"api_key": "sk"}, "anthropic": {"api_key": "sk-ant"}},
                       roles={"answer": {"provider": "anthropic", "model": "claude-sonnet-5"},
                              "fast": {"provider": "openai", "model": "gpt-6-luna"}})
    routed = RoutedLLM(config)
    client, model = routed.resolve(None)
    assert isinstance(client, AnthropicLLM) and model == "claude-sonnet-5"
    client, model = routed.resolve(llm.FAST)
    assert isinstance(client, OpenAILLM) and model == "gpt-6-luna"
    assert routed.resolve(llm.DEEP)[1] == "claude-sonnet-5"  # no deep model: the answer model


def test_fast_role_without_key_uses_the_answer_model(clean_env):
    config = LLMConfig(providers={"anthropic": {"api_key": "sk-ant"}},
                       roles={"answer": {"provider": "anthropic", "model": "claude-sonnet-5"},
                              "fast": {"provider": "openai", "model": "gpt-6-luna"}})
    assert RoutedLLM(config).resolve(llm.FAST)[1] == "claude-sonnet-5"


def test_no_answer_key_is_unavailable(clean_env):
    with pytest.raises(LLMUnavailable):
        RoutedLLM(LLMConfig.from_env())


def test_gemini_and_own_server_use_their_urls(clean_env):
    gemini = llm.make_client("gemini", "gemini-3-flash", {"api_key": "g"})
    assert str(gemini.client.base_url).startswith("https://generativelanguage.googleapis.com/v1beta/openai")
    own = llm.make_client("custom", "llama", {"base_url": "http://gpu.local:8000/v1"})  # no key needed
    assert str(own.client.base_url).startswith("http://gpu.local:8000/v1")
    with pytest.raises(LLMUnavailable):
        llm.make_client("custom", "llama", {})


# ─────────────── admin → Models ───────────────

AUTH = {}


@pytest.fixture
def api(clean_env):
    clean_env.setenv("ADMIN_LOGIN", "admin")
    clean_env.setenv("ADMIN_PASSWORD", "secret-pass")
    clean_env.delenv("ADMIN_SECRET", raising=False)
    clean_env.setenv("OPENAI_API_KEY", "sk-env-123456789")
    store = MemorySettings()
    main.app.state.llm_holder = llm_settings.LLMHolder(store)
    c = TestClient(main.app)
    AUTH["Authorization"] = "Bearer " + c.post("/api/admin/login", json={"login": "admin",
                                                                         "password": "secret-pass"}).json()["token"]
    return c, store


def test_settings_hide_keys_and_show_where_they_come_from(api):
    c, _ = api
    assert c.get("/api/admin/llm").status_code == 401
    view = c.get("/api/admin/llm", headers=AUTH).json()
    openai = next(p for p in view["providers"] if p["id"] == "openai")
    assert openai == {**openai, "has_key": True, "key_hint": "…6789", "key_source": "env"}
    assert "sk-env" not in json.dumps(view)
    assert view["roles"]["answer"] == {"provider": "openai", "model": "gpt-4o", "source": "env"}
    assert view["roles"]["deep"] is None


def test_save_keys_and_roles(api):
    c, store = api
    r = c.put("/api/admin/llm", headers=AUTH, json={
        "providers": {"anthropic": {"api_key": "sk-ant-abcdefgh1234"}},
        "roles": {"answer": {"provider": "anthropic", "model": "claude-sonnet-5"}}})
    assert r.status_code == 200, r.text
    view = r.json()
    assert "sk-ant-abcdefgh1234" not in json.dumps(view)
    assert view["roles"]["answer"] == {"provider": "anthropic", "model": "claude-sonnet-5", "source": "admin"}
    assert store.data["llm"]["providers"]["anthropic"]["api_key"] == "sk-ant-abcdefgh1234"
    routed = main.app.state.llm_holder.get()
    assert isinstance(routed.resolve(None)[0], AnthropicLLM)
    # back to the environment's model
    view = c.put("/api/admin/llm", headers=AUTH, json={"roles": {"answer": None}}).json()
    assert view["roles"]["answer"]["source"] == "env" and view["roles"]["answer"]["provider"] == "openai"


def test_a_role_without_a_key_is_refused(api):
    c, store = api
    r = c.put("/api/admin/llm", headers=AUTH,
              json={"roles": {"answer": {"provider": "gemini", "model": "gemini-3-flash"}}})
    assert r.status_code == 422 and "Gemini" in r.json()["message"]
    assert store.data == {}
    r = c.put("/api/admin/llm", headers=AUTH, json={"providers": {"custom": {"base_url": "ftp://x"}}})
    assert r.status_code == 422


def test_own_server_needs_only_a_url(api):
    c, _ = api
    r = c.put("/api/admin/llm", headers=AUTH, json={
        "providers": {"custom": {"base_url": "http://localhost:11434/v1"}},
        "roles": {"fast": {"provider": "custom", "model": "qwen3:8b"}}})
    assert r.status_code == 200, r.text
    own = next(p for p in r.json()["providers"] if p["id"] == "custom")
    assert own["base_url"] == "http://localhost:11434/v1" and not own["has_key"]


def test_model_test_and_list(api, monkeypatch):
    c, _ = api
    seen = {}

    def fake_probe(provider, model, cfg):
        seen.update(provider=provider, model=model, cfg=cfg)
        return llm.LLMResult(data={"reply": "ok"}, model=model, prompt_tokens=1, completion_tokens=1)

    monkeypatch.setattr(llm_settings, "probe", fake_probe)
    r = c.post("/api/admin/llm/test", headers=AUTH, json={"provider": "openai", "model": "gpt-4o"}).json()
    assert r["ok"] and r["model"] == "gpt-4o" and seen["cfg"]["api_key"] == "sk-env-123456789"
    c.post("/api/admin/llm/test", headers=AUTH, json={"provider": "gemini", "model": "g", "api_key": "typed"})
    assert seen["cfg"]["api_key"] == "typed"  # a key typed but not saved yet

    def failing_probe(provider, model, cfg):
        raise LLMUnavailable("no API key for Google (Gemini)")

    monkeypatch.setattr(llm_settings, "probe", failing_probe)
    r = c.post("/api/admin/llm/test", headers=AUTH, json={"provider": "gemini", "model": "g"}).json()
    assert not r["ok"] and "Gemini" in r["error"]

    monkeypatch.setattr(llm_settings, "list_models", lambda provider, cfg: ["a", "b"])
    assert c.post("/api/admin/llm/models", headers=AUTH, json={"provider": "openai"}).json() == {"models": ["a", "b"]}
