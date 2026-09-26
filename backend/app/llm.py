"""LLM access for answer generation (OpenAI, strict JSON-schema output, streamed or whole)."""

import json
import os
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Protocol

from openai import BadRequestError, OpenAI

DEFAULT_MODEL = "gpt-4o"
TIMEOUT_S = 40.0
# mode=deep may use a stronger model than the default one (fast/auto).
DEEP_MODEL = os.getenv("OPENAI_MODEL_DEEP") or None
# The query rewrite is a short call before the answer: the cheapest fast model.
REWRITE_MODEL = os.getenv("OPENAI_REWRITE_MODEL") or "gpt-6-luna"
# For reasoning models; models without reasoning ignore it (the option is dropped on their first call).
REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT") or "low"


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


class OpenAILLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        self.client = OpenAI(api_key=api_key, timeout=TIMEOUT_S, max_retries=2)
        # Sampling options each model accepted: deterministic answers where the model allows it (temperature 0),
        # reasoning effort where it has reasoning. A model that rejects one loses it on its first call.
        self.accepted: dict[str, set[str]] = {}

    def _create(self, model: str, effort: str | None, **request):
        accepted = self.accepted.setdefault(model, {"temperature", "reasoning_effort"})
        while True:
            options = {k: v for k, v in (("temperature", 0), ("reasoning_effort", effort or REASONING_EFFORT))
                       if k in accepted}
            try:
                return self.client.chat.completions.create(model=model, **request, **options)
            except BadRequestError as e:
                rejected = {k for k in options if k in str(e)}
                if not rejected:
                    raise
                accepted -= rejected

    @staticmethod
    def _request(system: str, user: str, schema_name: str, schema: dict) -> dict:
        return {
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        }

    def complete_json(self, system: str, user: str, schema_name: str, schema: dict, *, model: str | None = None,
                      effort: str | None = None, max_tokens: int | None = None) -> LLMResult:
        limit = {"max_completion_tokens": max_tokens} if max_tokens else {}
        try:
            resp = self._create(model or self.model, effort, **self._request(system, user, schema_name, schema),
                                **limit)
            data = json.loads(resp.choices[0].message.content or "")
        except Exception as e:  # SDK, network and JSON errors all mean "no answer from the model"
            raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
        usage = resp.usage
        return LLMResult(data=data, model=resp.model, prompt_tokens=usage.prompt_tokens if usage else 0,
                         completion_tokens=usage.completion_tokens if usage else 0)

    def stream_json(self, system: str, user: str, schema_name: str, schema: dict, *,
                    model: str | None = None) -> Generator[str, None, LLMResult]:
        model = model or self.model
        text: list[str] = []
        usage = None
        try:
            stream = self._create(model, None, stream=True, stream_options={"include_usage": True},
                                  **self._request(system, user, schema_name, schema))
            for chunk in stream:
                model = chunk.model or model
                usage = chunk.usage or usage
                if chunk.choices and (piece := chunk.choices[0].delta.content):
                    text.append(piece)
                    yield piece
            data = json.loads("".join(text))
        except Exception as e:
            raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
        return LLMResult(data=data, model=model, prompt_tokens=usage.prompt_tokens if usage else 0,
                         completion_tokens=usage.completion_tokens if usage else 0)
