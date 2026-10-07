"""LLM access for answer generation: strict JSON-schema output, streamed or whole, from one of several providers.

Providers: OpenAI, Anthropic (Claude), Google (Gemini, through its OpenAI-compatible endpoint) and any
OpenAI-compatible server of one's own (vLLM, Ollama, LM Studio, llama.cpp, OpenRouter...). The admin picks the
provider and model for each role (`answer`, `fast` for the short calls, `deep` for mode=deep) and stores the keys
in the database (admin → Models); what isn't set there comes from the environment (OPENAI_API_KEY, OPENAI_MODEL...).
"""

import contextlib
import json
import os
import re
import time
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from openai import BadRequestError, OpenAI

DEFAULT_MODEL = "gpt-4o"
TIMEOUT_S = 40.0
# Role tokens: callers ask for a role, the configuration says which provider and model plays it.
FAST = "@fast"  # the short calls (routing, query rewrite, translations, gap groups): the cheapest fast model
DEEP = "@deep"  # mode=deep: a stronger model if one is set, else the answer model
# For reasoning models; models without reasoning ignore it (the option is dropped on their first call).
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT") or "low"
ROLES = ("answer", "fast", "deep")


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema_name: str, schema: dict, *, model: str | None = None,
                      effort: str | None = None, max_tokens: int | None = None) -> LLMResult: ...

    def stream_json(self, system: str, user: str, schema_name: str, schema: dict, *,
                    model: str | None = None) -> Generator[str, None, LLMResult]:
        """Yields the JSON text as the model writes it; returns the parsed result at the end."""
        ...


class LLMUnavailable(RuntimeError):
    """No API key, network failure or a malformed response — the caller answers 503/502."""


def describe(e: Exception) -> str:
    """A provider error as one short line for the admin: the status and the provider's own message."""
    status = getattr(e, "status_code", None) or getattr(getattr(e, "response", None), "status_code", None)
    body = getattr(e, "body", None)
    if body is None and isinstance(getattr(e, "response", None), httpx.Response):
        try:
            body = e.response.json()
        except ValueError:
            body = None

    def message(x) -> str | None:
        if isinstance(x, dict):
            if isinstance(x.get("message"), str):
                return x["message"]
            return next((m for v in x.values() if (m := message(v))), None)
        if isinstance(x, list):
            return next((m for v in x if (m := message(v))), None)
        return None

    text = message(body) or str(e) or type(e).__name__
    return f"{status}: {text}" if status else f"{type(e).__name__}: {text}"


def schema_hint(schema: dict) -> str:
    return ("\n\nReply with one JSON object only, no other text, that matches this JSON schema:\n"
            + json.dumps(schema, ensure_ascii=False))


def parse_json(text: str) -> dict:
    """The model's JSON; a server without schema support may wrap it in ```json fences or add a sentence."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise
        return json.loads(text[start:end + 1])


# ─────────────── OpenAI and OpenAI-compatible servers (Gemini, your own) ───────────────

class OpenAILLM:
    """OpenAI's chat completions API, or any server that speaks it (`base_url`)."""

    def __init__(self, model: str | None = None, api_key: str | None = None, base_url: str | None = None,
                 timeout: float = TIMEOUT_S, http_client: httpx.Client | None = None):
        api_key = api_key or (None if base_url else os.getenv("OPENAI_API_KEY"))
        if not api_key and not base_url:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        # a server of one's own may need no key; the SDK wants a non-empty one
        self.client = OpenAI(api_key=api_key or "none", base_url=base_url or None, timeout=timeout, max_retries=2,
                             http_client=http_client)
        # Options each model accepted: deterministic answers where the model allows it (temperature 0), reasoning
        # effort where it has reasoning, usage in the stream. A model that rejects one loses it on its first call.
        self.accepted: dict[str, set[str]] = {}
        # How each model is asked for JSON: "schema" (strict JSON schema), "object" (JSON mode, the schema in the
        # prompt), "prompt" (only the prompt). A server that rejects one moves to the next on the first call.
        self.format: dict[str, str] = {}

    def _create(self, model: str, effort: str | None, system: str, user: str, schema_name: str, schema: dict,
                **request):
        accepted = self.accepted.setdefault(model, {"temperature", "reasoning_effort", "stream_options",
                                                    "max_completion_tokens"})
        while True:
            mode = self.format.get(model, "schema")
            options = {k: v for k, v in (("temperature", 0), ("reasoning_effort", effort or REASONING_EFFORT))
                       if k in accepted}
            req = dict(request)
            if "stream_options" in req and "stream_options" not in accepted:
                del req["stream_options"]
            if "max_completion_tokens" in req and "max_completion_tokens" not in accepted:
                req["max_tokens"] = req.pop("max_completion_tokens")  # an older server's name for the limit
            sys_text = system if mode == "schema" else system + schema_hint(schema)
            if mode == "schema":
                req["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": schema_name, "strict": True, "schema": schema}}
            elif mode == "object":
                req["response_format"] = {"type": "json_object"}
            messages = [{"role": "system", "content": sys_text}, {"role": "user", "content": user}]
            try:
                return self.client.chat.completions.create(model=model, messages=messages, **req, **options)
            except BadRequestError as e:
                message = str(e)
                rejected = {k for k in (*options, *(k for k in ("stream_options", "max_completion_tokens") if k in req))
                            if k in message}
                if rejected:
                    accepted -= rejected
                elif mode != "prompt" and ("response_format" in message or "json_schema" in message
                                           or "json_object" in message):
                    self.format[model] = "object" if mode == "schema" else "prompt"
                elif "reasoning_effort" in options and "reasoning" in message.lower():
                    accepted.discard("reasoning_effort")  # e.g. "thinking is not supported" worded another way
                else:
                    raise

    def complete_json(self, system: str, user: str, schema_name: str, schema: dict, *, model: str | None = None,
                      effort: str | None = None, max_tokens: int | None = None) -> LLMResult:
        limit = {"max_completion_tokens": max_tokens} if max_tokens else {}
        try:
            resp = self._create(model or self.model, effort, system, user, schema_name, schema, **limit)
            data = parse_json(resp.choices[0].message.content or "")
        except Exception as e:  # SDK, network and JSON errors all mean "no answer from the model"
            raise LLMUnavailable(describe(e)) from e
        usage = resp.usage
        return LLMResult(data=data, model=resp.model or model or self.model,
                         prompt_tokens=usage.prompt_tokens if usage else 0,
                         completion_tokens=usage.completion_tokens if usage else 0)

    def stream_json(self, system: str, user: str, schema_name: str, schema: dict, *,
                    model: str | None = None) -> Generator[str, None, LLMResult]:
        model = model or self.model
        text: list[str] = []
        usage = None
        try:
            stream = self._create(model, None, system, user, schema_name, schema, stream=True,
                                  stream_options={"include_usage": True})
            for chunk in stream:
                model = chunk.model or model
                usage = chunk.usage or usage
                if chunk.choices and (piece := chunk.choices[0].delta.content):
                    text.append(piece)
                    yield piece
            data = parse_json("".join(text))
        except Exception as e:
            raise LLMUnavailable(describe(e)) from e
        return LLMResult(data=data, model=model, prompt_tokens=usage.prompt_tokens if usage else 0,
                         completion_tokens=usage.completion_tokens if usage else 0)

    def models(self) -> list[str]:
        try:
            return sorted(m.id.removeprefix("models/") for m in self.client.models.list())
        except Exception as e:  # noqa: BLE001
            raise LLMUnavailable(describe(e)) from e


# ─────────────── Anthropic (Claude): the Messages API with structured outputs ───────────────

ANTHROPIC_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_MAX_TOKENS = 8192  # the API requires a limit; an answer is far shorter


class AnthropicLLM:
    """Claude through the Messages API; the JSON schema goes in `output_config.format` (structured outputs)."""

    def __init__(self, model: str, api_key: str, base_url: str | None = None, timeout: float = TIMEOUT_S,
                 transport: httpx.BaseTransport | None = None):
        if not api_key:
            raise LLMUnavailable("no Anthropic API key")
        self.model = model
        self.client = httpx.Client(base_url=(base_url or ANTHROPIC_URL).rstrip("/"), timeout=timeout,
                                   transport=transport,
                                   headers={"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION})
        self.accepted: dict[str, set[str]] = {}

    def _body(self, model: str, system: str, user: str, schema: dict, max_tokens: int | None, accepted: set[str]):
        body: dict[str, Any] = {
            "model": model, "max_tokens": max_tokens or ANTHROPIC_MAX_TOKENS, "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if "temperature" in accepted:
            body["temperature"] = 0
        return body

    def _post(self, model: str, build, stream: bool = False) -> httpx.Response:
        accepted = self.accepted.setdefault(model, {"temperature"})
        for attempt in range(3):
            request = self.client.build_request("POST", "/v1/messages", json=build(accepted))
            resp = self.client.send(request, stream=stream)
            if resp.status_code == 400 and "temperature" in accepted:
                resp.read()
                if "temperature" in resp.text:
                    accepted.discard("temperature")
                    continue
            if resp.status_code in (429, 500, 502, 503, 529) and attempt < 2:
                resp.close()
                time.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                resp.read()
                try:
                    detail = (resp.json().get("error") or {}).get("message") or resp.text[:300]
                except ValueError:
                    detail = resp.text[:300]
                raise LLMUnavailable(f"{resp.status_code}: {detail}")
            return resp
        raise LLMUnavailable("Anthropic: no answer")

    def complete_json(self, system: str, user: str, schema_name: str, schema: dict, *, model: str | None = None,
                      effort: str | None = None, max_tokens: int | None = None) -> LLMResult:
        model = model or self.model
        try:
            resp = self._post(model, lambda acc: self._body(model, system, user, schema, max_tokens, acc))
            body = resp.json()
            text = "".join(b.get("text", "") for b in body.get("content") or [] if b.get("type") == "text")
            data = parse_json(text)
        except LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMUnavailable(describe(e)) from e
        usage = body.get("usage") or {}
        return LLMResult(data=data, model=body.get("model") or model, prompt_tokens=usage.get("input_tokens", 0),
                         completion_tokens=usage.get("output_tokens", 0))

    def stream_json(self, system: str, user: str, schema_name: str, schema: dict, *,
                    model: str | None = None) -> Generator[str, None, LLMResult]:
        model = model or self.model
        text: list[str] = []
        prompt_tokens = completion_tokens = 0
        try:
            resp = self._post(model, lambda acc: {**self._body(model, system, user, schema, None, acc),
                                                  "stream": True}, stream=True)
            try:
                for event in sse_events(resp.iter_lines()):
                    kind = event.get("type")
                    if kind == "message_start":
                        message = event.get("message") or {}
                        model = message.get("model") or model
                        prompt_tokens = (message.get("usage") or {}).get("input_tokens", 0)
                    elif kind == "content_block_delta" and (event.get("delta") or {}).get("type") == "text_delta":
                        piece = event["delta"].get("text") or ""
                        if piece:
                            text.append(piece)
                            yield piece
                    elif kind == "message_delta":
                        completion_tokens = (event.get("usage") or {}).get("output_tokens", completion_tokens)
                    elif kind == "error":
                        raise LLMUnavailable(f"Anthropic stream error: {event.get('error')}")
            finally:
                resp.close()
            data = parse_json("".join(text))
        except LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            raise LLMUnavailable(describe(e)) from e
        return LLMResult(data=data, model=model, prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)

    def models(self) -> list[str]:
        try:
            resp = self.client.get("/v1/models", params={"limit": 100})
            resp.raise_for_status()
            return [m["id"] for m in resp.json().get("data") or []]
        except Exception as e:  # noqa: BLE001
            raise LLMUnavailable(describe(e)) from e


def sse_events(lines: Iterator[str]) -> Iterator[dict]:
    """The JSON `data:` payloads of a server-sent event stream."""
    for line in lines:
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload and payload != "[DONE]":
                yield json.loads(payload)


# ─────────────── Providers, configuration, the routed client ───────────────

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {"label": "OpenAI", "api": "openai", "base_url": None, "needs_key": True},
    "anthropic": {"label": "Anthropic (Claude)", "api": "anthropic", "base_url": None, "needs_key": True},
    "gemini": {"label": "Google (Gemini)", "api": "openai", "base_url": GEMINI_URL, "needs_key": True},
    # vLLM, Ollama (http://host:11434/v1), LM Studio, llama.cpp, OpenRouter...: the admin gives the URL
    "custom": {"label": "Own server (OpenAI-compatible)", "api": "openai", "base_url": None, "needs_key": False},
}


@dataclass
class LLMConfig:
    """Keys and URLs per provider, a provider and model per role."""

    providers: dict[str, dict[str, str]] = field(default_factory=dict)  # id → {"api_key", "base_url"}
    roles: dict[str, dict[str, str]] = field(default_factory=dict)      # role → {"provider", "model"}

    @classmethod
    def from_env(cls) -> "LLMConfig":
        providers = {}
        if key := os.getenv("OPENAI_API_KEY"):
            providers["openai"] = {"api_key": key}
        if key := os.getenv("ANTHROPIC_API_KEY"):
            providers["anthropic"] = {"api_key": key}
        if key := os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
            providers["gemini"] = {"api_key": key}
        if url := os.getenv("LLM_CUSTOM_BASE_URL"):
            providers["custom"] = {"base_url": url, "api_key": os.getenv("LLM_CUSTOM_API_KEY") or ""}
        answer = os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        roles = {"answer": {"provider": "openai", "model": answer},
                 "fast": {"provider": "openai", "model": os.getenv("OPENAI_REWRITE_MODEL") or "gpt-6-luna"}}
        if deep := os.getenv("OPENAI_MODEL_DEEP"):
            roles["deep"] = {"provider": "openai", "model": deep}
        return cls(providers=providers, roles=roles)

    def merged(self, saved: dict | None) -> "LLMConfig":
        """The admin's saved settings over this one: a key or URL saved in the admin wins; an empty one keeps this."""
        if not saved:
            return self
        providers = {p: dict(v) for p, v in self.providers.items()}
        for p, v in (saved.get("providers") or {}).items():
            if p in PROVIDERS:
                merged = providers.setdefault(p, {})
                merged.update({k: s for k, s in v.items() if k in ("api_key", "base_url") and s})
        roles = {r: dict(v) for r, v in self.roles.items()}
        for r, v in (saved.get("roles") or {}).items():
            if r not in ROLES:
                continue
            if v and v.get("provider") in PROVIDERS and v.get("model"):
                roles[r] = {"provider": v["provider"], "model": v["model"]}
            elif r == "deep":
                roles.pop(r, None)  # cleared: mode=deep uses the answer model
        return LLMConfig(providers=providers, roles=roles)


def make_client(provider: str, model: str, cfg: dict[str, str], timeout: float = TIMEOUT_S):
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise LLMUnavailable(f"unknown provider {provider}")
    key, base_url = cfg.get("api_key") or "", cfg.get("base_url") or spec["base_url"]
    if spec["needs_key"] and not key:
        raise LLMUnavailable(f"no API key for {spec['label']}")
    if provider == "custom" and not base_url:
        raise LLMUnavailable("no URL for the own server")
    if spec["api"] == "anthropic":
        return AnthropicLLM(model, key, base_url=cfg.get("base_url") or None, timeout=timeout)
    return OpenAILLM(model, api_key=key or None, base_url=base_url, timeout=timeout)


class RoutedLLM:
    """The LLM the answering code talks to: `model=None` is the answer role, FAST and DEEP the other roles; any
    other model name runs on the answer role's provider."""

    def __init__(self, config: LLMConfig, on_usage: Callable[..., None] | None = None):
        self.config = config
        # called after each call with (provider, model, role, kind, LLMResult, ms): admin → Spending counts tokens
        self.on_usage = on_usage
        if "answer" not in config.roles:
            raise LLMUnavailable("no answer model configured")
        self.clients: dict[str, Any] = {}
        self.role("answer")  # fails now (503 on the question) when the answer provider has no key

    def client(self, provider: str):
        if provider not in self.clients:
            model = next((r["model"] for r in self.config.roles.values() if r["provider"] == provider), "")
            self.clients[provider] = make_client(provider, model, self.config.providers.get(provider, {}))
        return self.clients[provider]

    def role(self, name: str) -> tuple[Any, str]:
        client, model, _, _ = self.route(name)
        return client, model

    def route(self, name: str) -> tuple[Any, str, str, str]:
        """(client, model, provider, the role that actually plays it)."""
        r = self.config.roles.get(name) or self.config.roles["answer"]
        played = name if self.config.roles.get(name) else "answer"
        try:
            return self.client(r["provider"]), r["model"], r["provider"], played
        except LLMUnavailable:
            if name == "answer":
                raise
            return self.route("answer")  # e.g. the fast model's provider has no key: the answer model does it

    def resolve(self, model: str | None) -> tuple[Any, str]:
        client, name, _, _ = self.resolve_route(model)
        return client, name

    def resolve_route(self, model: str | None) -> tuple[Any, str, str, str]:
        if model in (None, "", "@answer"):
            return self.route("answer")
        if model == FAST:
            return self.route("fast")
        if model == DEEP:
            return self.route("deep")
        client, _, provider, _ = self.route("answer")
        return client, model, provider, "answer"

    def _used(self, provider: str, role: str, kind: str, result: "LLMResult", started: float) -> None:
        if self.on_usage is None:
            return
        with contextlib.suppress(Exception):  # counting tokens must never break an answer
            self.on_usage(provider, result.model, role, kind, result, int((time.monotonic() - started) * 1000))

    @property
    def model(self) -> str:
        return self.config.roles["answer"]["model"]

    def complete_json(self, system: str, user: str, schema_name: str, schema: dict, *, model: str | None = None,
                      effort: str | None = None, max_tokens: int | None = None) -> LLMResult:
        client, name, provider, role = self.resolve_route(model)
        started = time.monotonic()
        result = client.complete_json(system, user, schema_name, schema, model=name, effort=effort,
                                      max_tokens=max_tokens)
        self._used(provider, role, schema_name, result, started)
        return result

    def stream_json(self, system: str, user: str, schema_name: str, schema: dict, *,
                    model: str | None = None) -> Generator[str, None, LLMResult]:
        client, name, provider, role = self.resolve_route(model)
        started = time.monotonic()
        result = yield from client.stream_json(system, user, schema_name, schema, model=name)
        self._used(provider, role, schema_name, result, started)
        return result


PROBE_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"],
                "additionalProperties": False}


def probe(provider: str, model: str, cfg: dict[str, str]) -> LLMResult:
    """One tiny structured call: the key, the model name and JSON-schema output all work."""
    client = make_client(provider, model, cfg, timeout=30.0)
    return client.complete_json("You answer health checks.", 'Reply with {"reply": "ok"}.', "probe", PROBE_SCHEMA,
                                model=model, effort="none", max_tokens=300)


# Not chat models: embeddings, speech, images, video, moderation... (OpenAI's and Gemini's lists mix them in)
NOT_CHAT = re.compile(r"embed|tts|transcribe|whisper|dall-e|image|imagen|veo|audio|realtime|moderation|search|"
                      r"davinci|babbage|sora|aqa|computer-use|live", re.I)


def list_models(provider: str, cfg: dict[str, str]) -> list[str]:
    models = make_client(provider, "", cfg, timeout=20.0).models()
    return models if provider == "custom" else [m for m in models if not NOT_CHAT.search(m)]
