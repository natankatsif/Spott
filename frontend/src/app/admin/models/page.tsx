"use client";

import { CheckIcon, CircleAlertIcon, CpuIcon, KeyRoundIcon, RotateCcwIcon, ServerIcon, TrashIcon, ZapIcon } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/admin/empty-state";
import { PageHeader } from "@/components/admin/page-header";
import { Badge } from "@/components/spell/badge";
import { Spinner } from "@/components/spell/spinner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { admin, useAdminQuery } from "@/lib/admin";
import { errorText } from "@/lib/admin-errors";
import { ADMIN_UI } from "@/lib/admin-i18n";
import { ApiRequestError, type LLMModelTest, LLMProvider, LLMRole, LLMRoleModel, LLMSettings, LLMSettingsUpdate } from "@/lib/api";
import { useUILang } from "@/lib/lang";
import { useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const ROLES: LLMRole[] = ["answer", "fast", "deep"];
const ROLE_ICONS = { answer: CpuIcon, fast: ZapIcon, deep: CpuIcon } as const;

type Draft = Record<LLMRole, LLMRoleModel | null>;
type KeyCheck = { busy: boolean; ok?: boolean; text?: string };

const draftOf = (s: LLMSettings): Draft => ({
  answer: s.roles.answer && { provider: s.roles.answer.provider, model: s.roles.answer.model },
  fast: s.roles.fast && { provider: s.roles.fast.provider, model: s.roles.fast.model },
  deep: s.roles.deep && { provider: s.roles.deep.provider, model: s.roles.deep.model },
});

export default function ModelsPage() {
  const uiLang = useUILang();
  const t = ADMIN_UI[uiLang];
  const tm = t.models;
  const mode = useApiMode();
  const query = useAdminQuery(`llm-${mode}`, () => admin.llm());
  const settings = query.data;

  // edits over the loaded settings; `null` in `reset` = back to the server's .env (deep: off)
  const [roles, setRoles] = useState<Partial<Draft>>({});
  const [keys, setKeys] = useState<Partial<Record<LLMProvider, string>>>({});
  const [urls, setUrls] = useState<Partial<Record<LLMProvider, string>>>({});
  const [removed, setRemoved] = useState<Set<LLMProvider>>(new Set());
  const [models, setModels] = useState<Partial<Record<LLMProvider, string[]>>>({});
  const [checks, setChecks] = useState<Partial<Record<LLMProvider, KeyCheck>>>({});
  const [tests, setTests] = useState<Partial<Record<LLMRole, LLMModelTest | "busy">>>({});
  const [saving, setSaving] = useState(false);

  if (query.loading || (!settings && !query.error)) {
    return (
      <>
        <PageHeader subtitle={tm.subtitle} title={tm.title} />
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }, (_, i) => (
            <Skeleton className="h-28 rounded-2xl" key={i} />
          ))}
        </div>
      </>
    );
  }
  if (!settings) {
    return (
      <>
        <PageHeader subtitle={tm.subtitle} title={tm.title} />
        <EmptyState
          action={<Button onClick={query.reload} variant="outline">{t.common.retry}</Button>}
          hint={errorText(query.error, uiLang)}
          icon={CpuIcon}
          title={t.common.loadError}
        />
      </>
    );
  }

  // a key or model check: the provider's own words ("401: invalid x-api-key") say more than "unavailable"
  const detail = (err: unknown) => (err instanceof ApiRequestError && err.body.message ? err.body.message : errorText(err, uiLang));
  const label = (id: LLMProvider) => tm.providers[id];
  const loaded = draftOf(settings);
  const draft: Draft = { ...loaded, ...roles };
  const provider = (id: LLMProvider) => settings.providers.find((p) => p.id === id)!;
  const typedKey = (id: LLMProvider) => keys[id]?.trim() || undefined;
  const typedUrl = (id: LLMProvider) => urls[id]?.trim() || undefined;
  const dirty =
    Object.keys(roles).length > 0 ||
    Object.values(keys).some((k) => k?.trim()) ||
    Object.entries(urls).some(([id, u]) => (u ?? "") !== (provider(id as LLMProvider).base_url ?? "")) ||
    removed.size > 0;

  const setRole = (role: LLMRole, value: LLMRoleModel | null) => {
    setRoles((r) => ({ ...r, [role]: value }));
    setTests((x) => ({ ...x, [role]: undefined }));
  };

  const checkKey = async (id: LLMProvider) => {
    setChecks((c) => ({ ...c, [id]: { busy: true } }));
    try {
      const list = await admin.llmModels({ provider: id, api_key: typedKey(id), base_url: typedUrl(id) });
      setModels((m) => ({ ...m, [id]: list }));
      setChecks((c) => ({ ...c, [id]: { busy: false, ok: true, text: tm.modelsFound(list.length) } }));
    } catch (err) {
      setChecks((c) => ({ ...c, [id]: { busy: false, ok: false, text: detail(err) } }));
    }
  };

  const loadModels = (id: LLMProvider) => {
    if (!models[id] && !checks[id]?.busy && (provider(id).has_key || typedKey(id) || (id === "custom" && (typedUrl(id) || provider(id).base_url)))) {
      void checkKey(id);
    }
  };

  const test = async (role: LLMRole) => {
    const r = draft[role];
    if (!r?.model.trim()) return;
    setTests((x) => ({ ...x, [role]: "busy" }));
    try {
      const res = await admin.testLLM({ provider: r.provider, model: r.model.trim(), api_key: typedKey(r.provider), base_url: typedUrl(r.provider) });
      setTests((x) => ({ ...x, [role]: res }));
    } catch (err) {
      setTests((x) => ({ ...x, [role]: { ok: false, model: null, latency_ms: 0, error: detail(err) } }));
    }
  };

  // the server's .env model isn't known here: saved at once, the reload shows it
  const backToEnv = async (role: LLMRole) => {
    setSaving(true);
    try {
      await admin.saveLLM({ roles: { [role]: null } });
      toast.success(tm.saved);
      query.reload();
    } catch (err) {
      toast.error(errorText(err, uiLang));
    } finally {
      setSaving(false);
    }
  };

  const save = async () => {
    const update: LLMSettingsUpdate = { providers: {}, roles: {} };
    for (const p of settings.providers) {
      const change: { api_key?: string; base_url?: string } = {};
      if (typedKey(p.id)) change.api_key = typedKey(p.id);
      else if (removed.has(p.id)) change.api_key = "";
      if (p.id in urls && (urls[p.id] ?? "").trim() !== (p.base_url ?? "")) change.base_url = (urls[p.id] ?? "").trim();
      if (Object.keys(change).length) update.providers![p.id] = change;
    }
    for (const r of ROLES) {
      if (!(r in roles)) continue;
      const v = roles[r] ?? null;
      if (v && !v.model.trim()) continue;
      update.roles![r] = v && { ...v, model: v.model.trim() };
    }
    setSaving(true);
    try {
      await admin.saveLLM(update);
      toast.success(tm.saved);
      setRoles({});
      setKeys({});
      setUrls({});
      setRemoved(new Set());
      query.reload();
    } catch (err) {
      toast.error(errorText(err, uiLang));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <PageHeader
        actions={
          <Button className="rounded-xl" disabled={!dirty || saving} onClick={save}>
            {saving ? <Spinner className="size-4" /> : <CheckIcon />}
            {tm.save}
          </Button>
        }
        subtitle={tm.subtitle}
        title={tm.title}
      />
      {dirty && <p className="-mt-3 mb-4 text-warning text-xs">{tm.unsaved}</p>}

      <h2 className="mb-3 font-medium text-sm">{tm.rolesTitle}</h2>
      <div className="mb-8 flex flex-col gap-3">
        {ROLES.map((role) => {
          const r = draft[role];
          const Icon = ROLE_ICONS[role];
          const source = role in roles ? null : settings.roles[role]?.source;
          const result = tests[role];
          const list = r ? models[r.provider] : undefined;
          return (
            <Card className="rounded-2xl py-4 shadow-none" key={role}>
              <CardContent className="px-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="flex items-center gap-1.5 font-medium text-sm">
                      <Icon className="size-4 text-brand" /> {tm.roles[role].title}
                      {source && <Badge variant={source === "env" ? "slate" : "blue"}>{source === "env" ? tm.fromEnv : tm.fromAdmin}</Badge>}
                    </p>
                    <p className="mt-1 text-muted-foreground text-xs">{tm.roles[role].hint}</p>
                  </div>
                  {settings.roles[role]?.source === "admin" && role !== "deep" && !(role in roles) && (
                    <Button className="h-7 rounded-lg text-xs" disabled={saving} onClick={() => backToEnv(role)} size="sm" variant="ghost">
                      <RotateCcwIcon className="size-3.5" /> {tm.backToEnv}
                    </Button>
                  )}
                </div>

                <div className="mt-3 grid gap-2 sm:grid-cols-[200px_1fr_auto]">
                  <Select
                    onValueChange={(v) => {
                      if (v === "off") return setRole(role, null);
                      const id = v as LLMProvider;
                      setRole(role, { provider: id, model: r?.provider === id ? r.model : "" });
                      loadModels(id);
                    }}
                    value={r?.provider ?? "off"}
                  >
                    <SelectTrigger aria-label={tm.provider} className="w-full rounded-xl">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {role === "deep" && <SelectItem value="off">{tm.sameAsAnswer}</SelectItem>}
                      {settings.providers.map((p) => (
                        <SelectItem key={p.id} value={p.id}>
                          {label(p.id)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {r && (
                    <>
                      <Input
                        aria-label={tm.model}
                        className="rounded-xl font-mono text-sm"
                        list={`models-${role}`}
                        onChange={(e) => setRole(role, { provider: r.provider, model: e.target.value })}
                        onFocus={() => loadModels(r.provider)}
                        placeholder={tm.modelPlaceholder}
                        spellCheck={false}
                        value={r.model}
                      />
                      <datalist id={`models-${role}`}>
                        {list?.map((m) => (
                          <option key={m} value={m} />
                        ))}
                      </datalist>
                      <Button className="rounded-xl" disabled={!r.model.trim() || result === "busy"} onClick={() => test(role)} variant="outline">
                        {result === "busy" ? <Spinner className="size-4" /> : <ZapIcon />}
                        {tm.test}
                      </Button>
                    </>
                  )}
                </div>
                {result && result !== "busy" && (
                  <p className={cn("mt-2 flex items-start gap-1.5 text-xs", result.ok ? "text-success" : "text-destructive")}>
                    {result.ok ? <CheckIcon className="mt-px size-3.5 shrink-0" /> : <CircleAlertIcon className="mt-px size-3.5 shrink-0" />}
                    <span className="min-w-0 break-words">
                      {result.ok ? `${tm.testOk(result.latency_ms)}${result.model ? ` · ${result.model}` : ""}` : `${tm.testFail}: ${result.error}`}
                    </span>
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>

      <h2 className="font-medium text-sm">{tm.connectionsTitle}</h2>
      <p className="mt-1 mb-3 text-muted-foreground text-xs">{tm.connectionsSubtitle}</p>
      <div className="grid gap-3 lg:grid-cols-2">
        {settings.providers.map((p) => {
          const check = checks[p.id];
          const willRemove = removed.has(p.id);
          return (
            <Card className="rounded-2xl py-4 shadow-none" key={p.id}>
              <CardContent className="flex flex-col gap-2.5 px-4">
                <div className="flex flex-wrap items-center gap-2">
                  {p.id === "custom" ? <ServerIcon className="size-4 text-brand" /> : <KeyRoundIcon className="size-4 text-brand" />}
                  <span className="font-medium text-sm">{label(p.id)}</span>
                  {p.has_key && !willRemove ? (
                    <Badge variant={p.key_source === "env" ? "slate" : "emerald"}>{tm.keySaved(p.key_hint ?? "", p.key_source === "env")}</Badge>
                  ) : (
                    p.needs_key && <Badge variant="amber">{tm.noKey}</Badge>
                  )}
                </div>
                {p.needs_url && (
                  <>
                    <Input
                      aria-label={tm.url}
                      className="rounded-xl font-mono text-sm"
                      inputMode="url"
                      onChange={(e) => setUrls((u) => ({ ...u, [p.id]: e.target.value }))}
                      placeholder="http://localhost:11434/v1"
                      spellCheck={false}
                      value={urls[p.id] ?? p.base_url ?? ""}
                    />
                    <p className="text-muted-foreground text-xs">{tm.urlHint}</p>
                  </>
                )}
                <div className="flex gap-2">
                  <Input
                    aria-label={tm.keyPlaceholder}
                    autoComplete="off"
                    className="rounded-xl font-mono text-sm"
                    onChange={(e) => setKeys((k) => ({ ...k, [p.id]: e.target.value }))}
                    placeholder={tm.keyPlaceholder}
                    spellCheck={false}
                    type="password"
                    value={keys[p.id] ?? ""}
                  />
                  <Button
                    aria-label={tm.checkKey}
                    className="shrink-0 rounded-xl"
                    disabled={check?.busy || !(p.has_key || typedKey(p.id) || (p.needs_url && (typedUrl(p.id) || p.base_url)))}
                    onClick={() => checkKey(p.id)}
                    variant="outline"
                  >
                    {check?.busy ? <Spinner className="size-4" /> : <CheckIcon />}
                    <span className="hidden sm:inline">{tm.checkKey}</span>
                  </Button>
                  {p.key_source === "admin" && (
                    <Button
                      aria-label={tm.removeKey}
                      className={cn("shrink-0 rounded-xl", willRemove && "bg-destructive/10 text-destructive")}
                      onClick={() =>
                        setRemoved((s) => {
                          const next = new Set(s);
                          if (next.has(p.id)) next.delete(p.id);
                          else next.add(p.id);
                          return next;
                        })
                      }
                      size="icon"
                      title={tm.removeKey}
                      variant="ghost"
                    >
                      <TrashIcon />
                    </Button>
                  )}
                </div>
                {check && !check.busy && (
                  <p className={cn("flex items-start gap-1.5 text-xs", check.ok ? "text-success" : "text-destructive")}>
                    {check.ok ? <CheckIcon className="mt-px size-3.5 shrink-0" /> : <CircleAlertIcon className="mt-px size-3.5 shrink-0" />}
                    <span className="min-w-0 break-words">{check.text}</span>
                  </p>
                )}
              </CardContent>
            </Card>
          );
        })}
      </div>
    </>
  );
}
