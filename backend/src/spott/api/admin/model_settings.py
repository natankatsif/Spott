"""Admin → Models: API keys per provider and the provider/model of each role, stored in the database.

GET  /api/admin/llm          what is set (keys only as a hint of their last characters, never whole)
PUT  /api/admin/llm          save keys / URLs / roles; the next question uses them
POST /api/admin/llm/models   the provider's model list (checks the key)
POST /api/admin/llm/test     one tiny structured call to a model (checks the key, the name and JSON output)

What the admin hasn't set comes from the environment (OPENAI_API_KEY, OPENAI_MODEL, ...), as before.
"""

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..deps import settings
from ..errors import ApiException
from ..llm import PROVIDERS, ROLES, LLMConfig, LLMUnavailable, RoutedLLM, list_models, probe
from ..llm_settings import KEY, LLMHolder
from .auth import require_admin

Provider = Literal["openai", "anthropic", "gemini", "custom"]
Role = Literal["answer", "fast", "deep"]


router = APIRouter(prefix="/api/admin/llm", dependencies=[Depends(require_admin)])


class ProviderView(BaseModel):
    id: Provider
    label: str
    api: Literal["openai", "anthropic"]
    needs_key: bool
    needs_url: bool
    has_key: bool
    key_hint: str | None = None                          # "…abcd"
    key_source: Literal["admin", "env"] | None = None
    base_url: str | None = None
    default_url: str | None = None


class RoleModel(BaseModel):
    provider: Provider
    model: str = Field(min_length=1, max_length=200)


class RoleView(RoleModel):
    source: Literal["admin", "env"]


class LLMSettingsView(BaseModel):
    providers: list[ProviderView]
    roles: dict[Role, RoleView | None]


class ProviderUpdate(BaseModel):
    api_key: str | None = Field(None, max_length=500)    # absent/None: keep; "": remove the saved key
    base_url: str | None = Field(None, max_length=500)   # absent/None: keep; "": remove


class LLMSettingsUpdate(BaseModel):
    providers: dict[Provider, ProviderUpdate] = {}
    roles: dict[Role, RoleModel | None] = {}             # None: back to the environment's (deep: off)


class ProviderCheck(BaseModel):
    provider: Provider
    api_key: str | None = Field(None, max_length=500)    # not saved; empty: the saved or env key
    base_url: str | None = Field(None, max_length=500)


class ModelCheck(ProviderCheck):
    model: str = Field(min_length=1, max_length=200)


class ModelList(BaseModel):
    models: list[str]


class ModelTest(BaseModel):
    ok: bool
    model: str | None = None
    latency_ms: int
    error: str | None = None


def hint(key: str) -> str:
    return "…" + key[-4:] if len(key) > 8 else "…"


def view(h: LLMHolder) -> LLMSettingsView:
    saved = h.saved_settings()
    config = h.config()
    providers = []
    for pid, spec in PROVIDERS.items():
        saved_p = (saved.get("providers") or {}).get(pid) or {}
        cfg = config.providers.get(pid, {})
        key = cfg.get("api_key") or ""
        providers.append(ProviderView(
            id=pid, label=spec["label"], api=spec["api"], needs_key=spec["needs_key"], needs_url=pid == "custom",
            has_key=bool(key), key_hint=hint(key) if key else None,
            key_source=("admin" if saved_p.get("api_key") else "env") if key else None,
            base_url=cfg.get("base_url") or None, default_url=spec["base_url"]))
    roles: dict[str, RoleView | None] = {}
    for role in ROLES:
        r = config.roles.get(role)
        saved_r = (saved.get("roles") or {}).get(role)
        roles[role] = RoleView(**r, source="admin" if saved_r else "env") if r else None
    return LLMSettingsView(providers=providers, roles=roles)


@router.get("", response_model=LLMSettingsView)
async def get_settings(request: Request) -> LLMSettingsView:
    return await run_in_threadpool(view, settings(request))


def apply(saved: dict, update: LLMSettingsUpdate) -> dict:
    out: dict[str, Any] = {"providers": {p: dict(v) for p, v in (saved.get("providers") or {}).items()},
                           "roles": dict(saved.get("roles") or {})}
    for pid, u in update.providers.items():
        p = out["providers"].setdefault(pid, {})
        for field_name in ("api_key", "base_url"):
            value = getattr(u, field_name)
            if value is None:
                continue
            value = value.strip()
            if value:
                p[field_name] = value
            else:
                p.pop(field_name, None)
    for role, r in update.roles.items():
        if r is None:
            if role == "deep":
                out["roles"]["deep"] = None  # explicitly off (the env's OPENAI_MODEL_DEEP no longer applies)
            else:
                out["roles"].pop(role, None)
        else:
            out["roles"][role] = {"provider": r.provider, "model": r.model.strip()}
    return out


@router.put("", response_model=LLMSettingsView)
async def save_settings(update: LLMSettingsUpdate, request: Request) -> LLMSettingsView:
    h = settings(request)
    for pid, u in update.providers.items():
        if u.base_url and not u.base_url.strip().lower().startswith(("http://", "https://")):
            raise ApiException(422, "validation_error", f"{pid}: the URL must start with http:// or https://")
    saved = await run_in_threadpool(lambda: h.store.get(KEY) or {})
    new = apply(saved, update)
    config = LLMConfig.from_env().merged(new)
    try:  # the answer role and every role set here need their provider's key (or URL): a save mustn't break the chat
        for role, r in config.roles.items():
            if role != "answer" and not (new["roles"].get(role)):
                continue
            cfg = config.providers.get(r["provider"], {})
            spec = PROVIDERS[r["provider"]]
            if spec["needs_key"] and not cfg.get("api_key"):
                raise LLMUnavailable(f"{role}: no API key for {spec['label']}")
            if r["provider"] == "custom" and not cfg.get("base_url"):
                raise LLMUnavailable(f"{role}: no URL for the own server")
        RoutedLLM(config)
    except LLMUnavailable as e:
        raise ApiException(422, "validation_error", str(e)) from e
    await run_in_threadpool(h.store.set, KEY, new)
    h.changed()
    return await run_in_threadpool(view, h)


def provider_config(h: LLMHolder, check: ProviderCheck) -> dict[str, str]:
    cfg = dict(h.config().providers.get(check.provider, {}))
    if check.api_key and check.api_key.strip():
        cfg["api_key"] = check.api_key.strip()
    if check.base_url and check.base_url.strip():
        cfg["base_url"] = check.base_url.strip()
    return cfg


@router.post("/models", response_model=ModelList)
async def provider_models(check: ProviderCheck, request: Request) -> ModelList:
    cfg = provider_config(settings(request), check)
    try:
        models = await run_in_threadpool(list_models, check.provider, cfg)
    except LLMUnavailable as e:
        raise ApiException(502, "unavailable", str(e)[:300]) from e
    return ModelList(models=models)


@router.post("/test", response_model=ModelTest)
async def test_model(check: ModelCheck, request: Request) -> ModelTest:
    cfg = provider_config(settings(request), check)
    start = time.monotonic()
    try:
        r = await run_in_threadpool(probe, check.provider, check.model.strip(), cfg)
        ok = isinstance(r.data.get("reply"), str)
        return ModelTest(ok=ok, model=r.model, latency_ms=int((time.monotonic() - start) * 1000),
                         error=None if ok else "the reply didn't match the JSON schema")
    except LLMUnavailable as e:
        return ModelTest(ok=False, latency_ms=int((time.monotonic() - start) * 1000), error=str(e)[:300])
