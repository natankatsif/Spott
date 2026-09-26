"""LLM access for answer generation (OpenAI, strict JSON-schema output)."""

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol

from openai import BadRequestError, OpenAI

DEFAULT_MODEL = "gpt-4o-mini"
TIMEOUT_S = 40.0


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema_name: str, schema: dict) -> LLMResult: ...


class LLMUnavailable(RuntimeError):
    """No API key, network failure or a malformed response — the caller answers 503/502."""


class OpenAILLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise LLMUnavailable("OPENAI_API_KEY is not set")
        self.model = model or os.getenv("OPENAI_MODEL") or DEFAULT_MODEL
        self.client = OpenAI(api_key=api_key, timeout=TIMEOUT_S, max_retries=2)
        # Deterministic answers where the model allows it; reasoning models only accept the default.
        self.sampling: dict = {"temperature": 0}

    def complete_json(self, system: str, user: str, schema_name: str, schema: dict) -> LLMResult:
        request = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
        }
        try:
            try:
                resp = self.client.chat.completions.create(**request, **self.sampling)
            except BadRequestError as e:
                if "temperature" not in str(e) or not self.sampling:
                    raise
                self.sampling = {}
                resp = self.client.chat.completions.create(**request)
            data = json.loads(resp.choices[0].message.content or "")
        except Exception as e:  # SDK, network and JSON errors all mean "no answer from the model"
            raise LLMUnavailable(f"{type(e).__name__}: {e}") from e
        usage = resp.usage
        return LLMResult(
            data=data,
            model=resp.model,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
        )
