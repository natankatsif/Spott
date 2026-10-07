// The demo backend of mock mode (../mode.ts): the whole admin panel works without a server. Its state starts from the
// mocks (../mocks/admin/*.json); running jobs move a little on every read so progress, ETA and logs look alive.
// `demo` has a method for each call of ./client.ts, answering as the server would.

import {
  ApiRequestError,
  type CorpusTotals,
  type FeedbackItem,
  type FeedbackStats,
  type Gap,
  type GapList,
  type GapRecheck,
  type Job,
  type JobStatus,
  type Lang,
  type LLMCheck,
  type LLMModelTest,
  type LLMProvider,
  type LLMSettings,
  type LLMSettingsUpdate,
  type Pricing,
  type SourceAdded,
  type SourceList,
  type SourceRow,
  type Suggestion,
  type UsageReport,
} from "../api";
import addBlocked from "../mocks/admin/add-source-blocked.json";
import addDocument from "../mocks/admin/add-source-document.json";
import addMerged from "../mocks/admin/add-source-merged.json";
import addSite from "../mocks/admin/add-source-site.json";
import feedbackStatsMock from "../mocks/admin/feedback-stats.json";
import feedbackMock from "../mocks/admin/feedback.json";
import gapRecheckMock from "../mocks/admin/gap-recheck.json";
import gapsMock from "../mocks/admin/gaps.json";
import jobsMock from "../mocks/admin/jobs.json";
import sourcesMock from "../mocks/admin/sources.json";
import suggestionsMock from "../mocks/suggestions.json";

const db = {
  sources: structuredClone(sourcesMock.sources) as SourceRow[],
  totals: structuredClone(sourcesMock.totals) as CorpusTotals,
  jobs: structuredClone(jobsMock.jobs) as Job[],
  gaps: structuredClone(gapsMock.items) as Gap[],
  suggestions: structuredClone(suggestionsMock.items) as Suggestion[],
  feedback: structuredClone(feedbackMock.items) as FeedbackItem[],
  nextId: 1000,
  pricing: {
    currency: "USD",
    rates: { USD: 1, EUR: 0.86, MDL: 17 },
    prices: { "gpt-4o": { input: 2.5, output: 10 }, "gpt-6-luna": { input: 0.1, output: 0.4 } },
    budget_usd: 50,
  } as Pricing,
  llm: {
    keys: { openai: "sk-demo-4f2a" } as Partial<Record<LLMProvider, string>>,
    urls: {} as Partial<Record<LLMProvider, string>>,
    roles: {
      answer: { provider: "openai", model: "gpt-4o", source: "env" },
      fast: { provider: "openai", model: "gpt-6-luna", source: "env" },
      deep: null,
    } as LLMSettings["roles"],
  },
};

const DEMO_STAGES: [NonNullable<Job["stage"]>, number, number, number][] = [
  // stage, starts at %, width %, items in the stage
  ["crawl", 0, 20, 120],
  ["download", 20, 20, 37],
  ["parse", 40, 40, 37],
  ["index", 80, 20, 260],
];

function newJob(sourceId: number | null, kind: Job["kind"], from?: Partial<Job>): Job {
  return {
    id: db.nextId++,
    source_id: sourceId,
    kind,
    status: "queued",
    stage: null,
    stage_done: 0,
    stage_total: 0,
    percent: 0,
    eta_s: null,
    started_at: null,
    finished_at: null,
    stats: {},
    log_tail: [],
    error: null,
    ...from,
  };
}

// the mock's rows that are running/queued get a demo job, so their progress moves too
for (const row of db.sources) {
  if (row.progress && !db.jobs.some((j) => j.id === row.progress?.job_id)) {
    db.jobs.unshift(
      newJob(row.id, "crawl", {
        id: row.progress.job_id,
        status: row.status === "running" ? "running" : "queued",
        stage: row.progress.stage,
        percent: row.progress.percent,
        eta_s: row.progress.eta_s,
        started_at: row.status === "running" ? new Date().toISOString() : null,
      }),
    );
  }
}

/** docs/API.md: disabled > blocked > running > queued > failed (last job) > indexed (has chunks) > pending. */
function demoStatus(row: SourceRow, job: Job | undefined): SourceRow["status"] {
  if (!row.enabled) return "disabled";
  if (row.robots === "blocked") return "blocked";
  if (job?.status === "running") return "running";
  if (job?.status === "queued") return "queued";
  if (row.last_job?.status === "failed") return "failed";
  return row.chunks > 0 ? "indexed" : "pending";
}

/** Moves demo jobs forward a little on every read, so progress, ETA and the log look alive; rows follow their jobs. */
function tickDemo(): void {
  const now = new Date().toISOString();
  for (const job of db.jobs) {
    if (job.status === "queued") {
      Object.assign(job, { status: "running", started_at: now, stage: "crawl" } satisfies Partial<Job>);
      continue;
    }
    if (job.status !== "running") continue;
    job.percent = Math.min(100, job.percent + 1.5 + Math.random() * 2.5);
    const [stage, start, width, total] = DEMO_STAGES.findLast(([, st]) => job.percent >= st) ?? DEMO_STAGES[0];
    job.stage = stage;
    job.stage_total = total;
    job.stage_done = Math.min(total, Math.round(((job.percent - start) / width) * total));
    job.eta_s = Math.round((100 - job.percent) * 4.2);
    const site = db.sources.find((x) => x.id === job.source_id)?.site_id ?? "all";
    job.log_tail = [...job.log_tail, `${site} [${job.stage_done}/${total}] ${stage} ok`].slice(-12);
    if (job.percent >= 100) {
      Object.assign(job, {
        status: "done",
        percent: 100,
        eta_s: null,
        finished_at: now,
        stats: { pages: 118, documents_found: 37, documents_downloaded: 37, files_parsed: 36, chunks: 260, lines: 1804, embeddings_reused: 241, embeddings_computed: 19, errors: 1 },
      } satisfies Partial<Job>);
      job.log_tail = [...job.log_tail, "Indexing finished.", "  Chunks: 260"].slice(-12);
      const row = db.sources.find((x) => x.id === job.source_id);
      if (row) {
        const wasEmpty = row.chunks === 0;
        Object.assign(row, {
          chunks: Math.max(row.chunks, 260),
          lines: Math.max(row.lines, 1804),
          pages: Math.max(row.pages, 118),
          documents_found: Math.max(row.documents_found, 37),
          documents_downloaded: Math.max(row.documents_downloaded, 37),
          last_crawled: now,
        } satisfies Partial<SourceRow>);
        if (wasEmpty) db.totals.sites_indexed += 1;
      }
    }
  }
  for (const row of db.sources) {
    const jobs = db.jobs.filter((j) => j.source_id === row.id).sort((a, b) => b.id - a.id);
    if (jobs[0]) row.last_job = jobs[0];
    const active = jobs.find((j) => j.status === "queued" || j.status === "running");
    row.progress = active ? { job_id: active.id, stage: active.stage, percent: active.percent, eta_s: active.eta_s, current: null } : null;
    row.status = demoStatus(row, active);
  }
}

function demoError(status: number, error: "conflict" | "not_found" | "validation_error", message: string): never {
  throw new ApiRequestError(status, { error, message, retry_after_s: null });
}

function demoStartJob(sourceId: number, kind: Job["kind"]): Job {
  const source = db.sources.find((x) => x.id === sourceId) ?? demoError(404, "not_found", "unknown source");
  if (source.robots === "blocked") demoError(409, "conflict", "robots.txt forbids crawling");
  if (!source.enabled) demoError(409, "conflict", "the source is disabled");
  if (db.jobs.some((j) => j.source_id === sourceId && (j.status === "queued" || j.status === "running")))
    demoError(409, "conflict", "a job is already running");
  const job = newJob(sourceId, kind);
  db.jobs.unshift(job);
  tickDemo();
  return job;
}

const hostOf = (url: string) => new URL(url).hostname.replace(/^www\./, "");
const isDocumentUrl = (url: string) => /\.(pdf|docx?|odt|rtf)(\?|#|$)/i.test(url);

/**
 * POST /api/admin/sources {url} in mock mode, shaped like the add-source-*.json mocks: a document link → document;
 * a deeper path or a document of a known domain → 200 merged; robots-forbidden domains → blocked; "down"/".invalid"
 * hosts → 422; the same root again → 409.
 */
function demoAddSource(url: string): SourceAdded {
  if (!/^https?:\/\/[^/\s]+\.[^/\s]+/.test(url)) demoError(422, "validation_error", "Only http(s) links");
  const host = hostOf(url);
  if (/(^|\.)(down|invalid|example-dead)\b|\.invalid$/.test(host)) demoError(422, "validation_error", "The site doesn't answer (ConnectTimeout)");
  const path = new URL(url).pathname.replace(/\/+$/, "");
  const doc = isDocumentUrl(url);
  const existing = db.sources.find((x) => x.kind === "site" && x.site_id === host);
  if (existing && !path && !doc) demoError(409, "conflict", `${host} is already a source`);
  if (db.sources.some((x) => x.kind === "document" && x.url === url)) demoError(409, "conflict", "This document is already a source");

  if (existing) {
    // one row per domain: the path / document joins it and a crawl of it is queued
    existing.start_urls = [...new Set([...existing.start_urls, url])];
    const merged = { ...(addMerged as unknown as SourceAdded), ...structuredClone(existing) };
    if (existing.robots === "allowed" && existing.enabled && !db.jobs.some((j) => j.source_id === existing.id && (j.status === "queued" || j.status === "running"))) {
      db.jobs.unshift(newJob(existing.id, "crawl"));
    }
    return {
      ...merged,
      merged_into: existing.id,
      detected: {
        ...addMerged.detected,
        kind: doc ? "document" : "site",
        category: existing.category ?? "other",
        title: null,
        crawl_depth: doc ? null : 2,
        reason: doc ? `Added the document to ${host}; downloading and indexing it.` : `Added the path ${path} to ${host}; crawling it up to depth 2.`,
      },
    };
  }

  const blocked = /(^|\.)(chisinau\.md|actelocale\.gov\.md)$/.test(host);
  const template = (blocked ? addBlocked : doc ? addDocument : addSite) as unknown as SourceAdded;
  const row: SourceAdded = {
    ...structuredClone(template),
    id: db.nextId++,
    url,
    site_id: host,
    title: doc ? decodeURIComponent(path.split("/").at(-1) ?? host) : null,
    start_urls: [url],
    created_at: new Date().toISOString(),
    pages: 0,
    documents_found: 0,
    documents_downloaded: 0,
    chunks: 0,
    lines: 0,
    last_crawled: null,
    last_job: null,
    last_error: null,
    progress: null,
    merged_into: null,
  };
  db.sources.unshift({ ...row });
  db.totals.sites_total += 1;
  if (!blocked) db.jobs.unshift(newJob(row.id, "crawl"));
  tickDemo();
  const stored = db.sources[0];
  return { ...row, status: stored.status, progress: stored.progress };
}

const DEMO_PROVIDERS: { id: LLMProvider; label: string; api: "openai" | "anthropic"; url: string | null }[] = [
  { id: "openai", label: "OpenAI", api: "openai", url: null },
  { id: "anthropic", label: "Anthropic (Claude)", api: "anthropic", url: null },
  { id: "gemini", label: "Google (Gemini)", api: "openai", url: "https://generativelanguage.googleapis.com/v1beta/openai/" },
  { id: "custom", label: "Own server (OpenAI-compatible)", api: "openai", url: null },
];
const DEMO_MODELS: Record<LLMProvider, string[]> = {
  openai: ["gpt-4o", "gpt-4o-mini", "gpt-5.5", "gpt-6-luna", "gpt-6-sol"],
  anthropic: ["claude-opus-5-5", "claude-sonnet-5", "claude-haiku-4-5"],
  gemini: ["gemini-3-pro", "gemini-3-flash", "gemini-3-flash-lite"],
  custom: ["qwen3:8b", "llama4:scout"],
};

function demoLLM(): LLMSettings {
  const { keys, urls, roles } = db.llm;
  return {
    providers: DEMO_PROVIDERS.map((p) => ({
      id: p.id, label: p.label, api: p.api, needs_key: p.id !== "custom", needs_url: p.id === "custom",
      has_key: !!keys[p.id], key_hint: keys[p.id] ? `…${keys[p.id]!.slice(-4)}` : null,
      key_source: keys[p.id] ? (p.id === "openai" && keys[p.id] === "sk-demo-4f2a" ? "env" : "admin") : null,
      base_url: urls[p.id] ?? null, default_url: p.url,
    })),
    roles,
  };
}

function demoSaveLLM(u: LLMSettingsUpdate): LLMSettings {
  for (const [id, v] of Object.entries(u.providers ?? {}) as [LLMProvider, { api_key?: string; base_url?: string }][]) {
    if (v.api_key !== undefined) {
      if (v.api_key) db.llm.keys[id] = v.api_key;
      else delete db.llm.keys[id];
    }
    if (v.base_url !== undefined) {
      if (v.base_url) db.llm.urls[id] = v.base_url;
      else delete db.llm.urls[id];
    }
  }
  for (const [role, r] of Object.entries(u.roles ?? {}) as [keyof LLMSettings["roles"], LLMSettings["roles"]["answer"]][]) {
    if (r && r.provider !== "custom" && !db.llm.keys[r.provider]) {
      throw new ApiRequestError(422, { error: "validation_error", message: `${role}: no API key for ${r.provider}`, retry_after_s: null });
    }
    db.llm.roles[role] = r ? { provider: r.provider, model: r.model, source: "admin" } : role === "answer" ? { provider: "openai", model: "gpt-4o", source: "env" } : role === "fast" ? { provider: "openai", model: "gpt-6-luna", source: "env" } : null;
  }
  return demoLLM();
}

// admin → Spending in mock mode: a month of made-up calls, priced with the demo prices
function demoUsage(days: number): UsageReport {
  const pricing = db.pricing;
  const models = [
    { provider: "openai", model: "gpt-4o", kinds: ["answer"], calls: 38, inTok: 5200, outTok: 420 },
    { provider: "openai", model: "gpt-6-luna", kinds: ["rewrite", "route", "translate_quotes"], calls: 95, inTok: 700, outTok: 90 },
    { provider: "anthropic", model: "claude-sonnet-5", kinds: ["gap_groups"], calls: 3, inTok: 3100, outTok: 600 },
  ];
  const price = (m: string) => pricing.prices[m] ?? null;
  const costOf = (m: string, i: number, o: number) => {
    const p = price(m);
    return p ? (i * p.input + o * p.output) / 1e6 : null;
  };
  const today = new Date();
  const zero = () => ({ calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0, unpriced_calls: 0 });
  const totals = { today: zero(), month: zero(), range: zero(), all_time: zero() };
  const daily: UsageReport["daily"] = [];
  const byModel = new Map<string, UsageReport["models"][number]>();
  const byKind = new Map<string, UsageReport["kinds"][number]>();
  for (let back = 89; back >= 0; back--) {
    const d = new Date(today);
    d.setDate(d.getDate() - back);
    const wave = 0.55 + 0.45 * Math.sin(back / 3) + (back % 7 === 0 ? 0.6 : 0);
    const day = { day: d.toISOString().slice(0, 10), calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 };
    for (const m of models) {
      const calls = Math.max(0, Math.round(m.calls * wave * (back > 60 ? 0.4 : 1)));
      const i = calls * m.inTok;
      const o = calls * m.outTok;
      const c = costOf(m.model, i, o);
      const add = (t: UsageReport["today"]) => {
        t.calls += calls;
        t.input_tokens += i;
        t.output_tokens += o;
        if (c === null) t.unpriced_calls += calls;
        else t.cost_usd += c;
      };
      add(totals.all_time);
      if (d.getMonth() === today.getMonth()) add(totals.month);
      if (back === 0) add(totals.today);
      if (back >= days) continue;
      add(totals.range);
      day.calls += calls;
      day.input_tokens += i;
      day.output_tokens += o;
      day.cost_usd += c ?? 0;
      const row = byModel.get(m.model) ?? { provider: m.provider, model: m.model, calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: c === null ? null : 0, price: price(m.model) };
      row.calls += calls;
      row.input_tokens += i;
      row.output_tokens += o;
      if (c !== null) row.cost_usd = (row.cost_usd ?? 0) + c;
      byModel.set(m.model, row);
      m.kinds.forEach((k, n) => {
        const share = m.kinds.length === 1 ? 1 : [0.5, 0.3, 0.2][n];
        const r = byKind.get(k) ?? { kind: k, calls: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 };
        r.calls += Math.round(calls * share);
        r.input_tokens += Math.round(i * share);
        r.output_tokens += Math.round(o * share);
        r.cost_usd += (c ?? 0) * share;
        byKind.set(k, r);
      });
    }
    if (back < days) daily.push(day);
  }
  const questions = Math.round(totals.range.calls / 3.6);
  const dim = new Date(today.getFullYear(), today.getMonth() + 1, 0).getDate();
  return {
    days,
    pricing,
    ...totals,
    month_forecast_usd: (totals.month.cost_usd / today.getDate()) * dim,
    questions,
    cost_per_question_usd: questions ? totals.range.cost_usd / questions : null,
    daily,
    models: [...byModel.values()].sort((a, b) => (b.cost_usd ?? 0) - (a.cost_usd ?? 0)),
    kinds: [...byKind.values()].sort((a, b) => b.cost_usd - a.cost_usd),
    known_models: ["claude-sonnet-5", "gpt-4o", "gpt-6-luna"],
  };
}

const demoHasAccess = (c: LLMCheck) => !!(c.api_key || db.llm.keys[c.provider] || (c.provider === "custom" && (c.base_url || db.llm.urls.custom)));

const active = (j: Job) => j.status === "queued" || j.status === "running";

export const demo = {
  sources: (): SourceList => {
    tickDemo();
    return { sources: db.sources, totals: db.totals };
  },

  addSource: (url: string) => demoAddSource(url.trim()),

  patchSource: (id: number, patch: Partial<SourceRow>) => {
    const row = db.sources.find((x) => x.id === id) ?? demoError(404, "not_found", "unknown source");
    Object.assign(row, patch, patch.category ? { category_source: "manual" } : {});
    tickDemo();
    return row;
  },

  deleteSource: (id: number) => {
    db.sources = db.sources.filter((x) => x.id !== id);
    db.totals.sites_total = Math.max(0, db.totals.sites_total - 1);
    return { ok: true };
  },

  startJob: (sourceId: number, kind: Job["kind"]) => demoStartJob(sourceId, kind),

  gaps: (hidden: boolean): GapList => {
    const items = db.gaps.filter((g) => g.hidden === hidden).sort((a, b) => b.count - a.count);
    const all = db.gaps.filter((g) => !g.hidden);
    const topics = Object.entries(Object.groupBy(items, (g) => g.topic)).map(([topic, g]) => ({ topic, groups: g?.length ?? 0 }));
    return {
      items,
      topics: topics.sort((a, b) => b.groups - a.groups) as GapList["topics"],
      totals: {
        not_found: all.filter((g) => g.status === "not_found").reduce((n, g) => n + g.count, 0),
        partial: all.filter((g) => g.status === "partial").reduce((n, g) => n + g.count, 0),
        groups: all.length,
      },
    };
  },

  recheckGap: async (id: string) => {
    await new Promise((r) => setTimeout(r, 1200)); // a model call takes a moment
    const gap = db.gaps.find((g) => g.id === id) ?? demoError(404, "not_found", "unknown gap");
    const result = { ...(gapRecheckMock as GapRecheck), ts: new Date().toISOString() };
    gap.rechecked = result;
    if (result.status === "answered") db.gaps = db.gaps.filter((g) => g.id !== id); // solved: leaves the list
    return result;
  },

  hideGap: (id: string, hide: boolean) => {
    const gap = db.gaps.find((g) => g.id === id) ?? demoError(404, "not_found", "unknown gap");
    gap.hidden = hide;
    return { ok: true };
  },

  jobs: (status?: JobStatus) => {
    tickDemo();
    return db.jobs.filter((j) => !status || j.status === status);
  },

  job: (id: number) => {
    tickDemo();
    return db.jobs.find((j) => j.id === id) ?? demoError(404, "not_found", "unknown job");
  },

  cancelJob: (id: number) => {
    const job = db.jobs.find((j) => j.id === id) ?? demoError(404, "not_found", "unknown job");
    Object.assign(job, { status: "cancelled", eta_s: null, finished_at: new Date().toISOString() } satisfies Partial<Job>);
    return job;
  },

  retryJob: (id: number) => {
    const job = db.jobs.find((j) => j.id === id) ?? demoError(404, "not_found", "unknown job");
    return job.source_id == null ? demoError(409, "conflict", "demo: no source") : demoStartJob(job.source_id, job.kind);
  },

  deleteJob: (id: number) => {
    db.jobs = db.jobs.filter((j) => j.id !== id || active(j));
    return { ok: true };
  },

  clearJobs: () => {
    const before = db.jobs.length;
    db.jobs = db.jobs.filter(active);
    return { deleted: before - db.jobs.length };
  },

  feedback: (maxRating: number): FeedbackItem[] =>
    db.feedback.filter((f) => f.rating <= maxRating).sort((a, b) => a.rating - b.rating),

  feedbackStats: () => feedbackStatsMock as FeedbackStats,

  usage: (days: number) => demoUsage(days),

  savePricing: (pricing: Pricing) => (db.pricing = { ...pricing, rates: { ...pricing.rates, USD: 1 } }),

  llm: demoLLM,

  saveLLM: (update: LLMSettingsUpdate) => demoSaveLLM(update),

  llmModels: (check: LLMCheck) => {
    if (!demoHasAccess(check)) throw new ApiRequestError(502, { error: "unavailable", message: "no API key", retry_after_s: null });
    return DEMO_MODELS[check.provider];
  },

  testLLM: (check: LLMCheck & { model: string }): LLMModelTest =>
    demoHasAccess(check)
      ? { ok: true, model: check.model, latency_ms: 840, error: null }
      : { ok: false, model: null, latency_ms: 12, error: "no API key" },

  suggestions: () => ({ items: db.suggestions.sort((a, b) => Number(b.pinned) - Number(a.pinned)) }),

  pinSuggestion: (question: string, lang: Lang, pinned: boolean) => {
    const existing = db.suggestions.find((s) => s.lang === lang && s.question === question);
    if (existing) {
      existing.pinned = pinned;
      return existing;
    }
    const s: Suggestion = { id: db.nextId++, question, lang, answer_id: null, asked_count: 0, rating_avg: null, pinned };
    db.suggestions.unshift(s);
    return s;
  },

  hideSuggestion: (id: number) => {
    db.suggestions = db.suggestions.filter((s) => s.id !== id);
    return { ok: true };
  },
};
