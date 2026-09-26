// Admin panel data layer: session (token from POST /api/admin/login, kept in localStorage until expires_at),
// every /api/admin/* call with the token, 401 → sign out, and a demo backend for mock mode (./mode.ts) so the
// whole panel works without a server. Contract: docs/API.md → "Admin".

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  type AdminSession,
  adminAddSource,
  adminCancelJob,
  adminDeleteSource,
  adminFeedback,
  adminFeedbackStats,
  adminHideSuggestion,
  adminJob,
  adminJobs,
  adminLogin,
  adminMe,
  adminPatchSource,
  adminPinSuggestion,
  adminSources,
  adminStartJob,
  ApiRequestError,
  type FeedbackItem,
  type FeedbackStats,
  isMock,
  type Job,
  type JobStatus,
  type Lang,
  type SourceCreate,
  type SourceRow,
  type Suggestion,
  suggestions as publicSuggestions,
} from "./api";
import feedbackMock from "./mocks/admin/feedback.json";
import feedbackStatsMock from "./mocks/admin/feedback-stats.json";
import jobsMock from "./mocks/admin/jobs.json";
import sessionMock from "./mocks/admin/session.json";
import sourcesMock from "./mocks/admin/sources.json";
import suggestionsMock from "./mocks/suggestions.json";

// ─────────────── session ───────────────

const KEY = "adminSession";
const listeners = new Set<() => void>();
let cache: { raw: string | null; session: AdminSession | null } = { raw: null, session: null };
/** Set when the server ended the session (401), so the login form can say why. */
let expiredFlag = false;

function readSession(): AdminSession | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    /* storage blocked */
  }
  if (raw === cache.raw) return cache.session;
  let session: AdminSession | null = null;
  try {
    session = raw ? (JSON.parse(raw) as AdminSession) : null;
  } catch {
    session = null;
  }
  if (session && Date.parse(session.expires_at) <= Date.now()) session = null;
  cache = { raw, session };
  return session;
}

function writeSession(session: AdminSession | null): void {
  try {
    if (session) window.localStorage.setItem(KEY, JSON.stringify(session));
    else window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function useAdminSession(): AdminSession | null {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    readSession,
    () => null,
  );
}

/** Whether the store has been read on the client yet (the server snapshot is always "signed out"). */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

export async function signIn(login: string, password: string): Promise<void> {
  const session = isMock()
    ? { ...(sessionMock as AdminSession), login: login || "admin", expires_at: new Date(Date.now() + 12 * 3600e3).toISOString() }
    : await adminLogin({ login, password });
  expiredFlag = false;
  writeSession(session);
}

export function signOut(): void {
  writeSession(null);
}

export function takeExpiredFlag(): boolean {
  const was = expiredFlag;
  expiredFlag = false;
  return was;
}

/** Runs an admin call with the current token; a 401 ends the session. */
async function withToken<T>(live: (token: string) => Promise<T>, mock: () => T | Promise<T>): Promise<T> {
  if (isMock()) {
    await new Promise((r) => setTimeout(r, 250));
    return structuredClone(await mock());
  }
  const session = readSession();
  if (!session) throw new ApiRequestError(401, { error: "unauthorized", message: "no session", retry_after_s: null });
  try {
    return await live(session.token);
  } catch (e) {
    if (e instanceof ApiRequestError && e.status === 401) {
      expiredFlag = true;
      writeSession(null);
    }
    throw e;
  }
}

// ─────────────── demo backend (mock mode) ───────────────

const demo = {
  sources: structuredClone(sourcesMock.sources) as SourceRow[],
  jobs: structuredClone(jobsMock.jobs) as Job[],
  suggestions: structuredClone(suggestionsMock.items) as Suggestion[],
  feedback: structuredClone(feedbackMock.items) as FeedbackItem[],
  nextId: 100,
};

const DEMO_STAGES: [NonNullable<Job["stage"]>, number, number, number][] = [
  // stage, starts at %, width %, items in the stage
  ["crawl", 0, 20, 120],
  ["download", 20, 20, 37],
  ["parse", 40, 40, 37],
  ["index", 80, 20, 260],
];

/** Moves demo jobs forward a little on every read, so progress, ETA and the log look alive. */
function tickDemo(): void {
  const now = new Date().toISOString();
  for (const job of demo.jobs) {
    if (job.status === "queued") {
      job.status = "running";
      job.started_at = now;
      job.stage = "crawl";
      continue;
    }
    if (job.status !== "running") continue;
    job.percent = Math.min(100, job.percent + 1.5 + Math.random() * 2.5);
    const [stage, start, width, total] = DEMO_STAGES.findLast(([, s]) => job.percent >= s) ?? DEMO_STAGES[0];
    job.stage = stage;
    job.stage_total = total;
    job.stage_done = Math.min(total, Math.round(((job.percent - start) / width) * total));
    job.eta_s = Math.round((100 - job.percent) * 4.2);
    const site = demo.sources.find((s) => s.id === job.source_id)?.site_id ?? "all";
    job.log_tail = [...job.log_tail, `${site} [${job.stage_done}/${total}] ${stage} ok`].slice(-12);
    if (job.percent >= 100) {
      Object.assign(job, {
        status: "done",
        percent: 100,
        eta_s: null,
        finished_at: now,
        stats: {
          pages: 118,
          documents_found: 37,
          documents_downloaded: 37,
          files_parsed: 36,
          chunks: 260,
          lines: 1804,
          embeddings_reused: 241,
          embeddings_computed: 19,
          errors: 1,
        },
      } satisfies Partial<Job>);
      job.log_tail = [...job.log_tail, "Indexing finished.", "  Chunks: 260"].slice(-12);
      const source = demo.sources.find((s) => s.id === job.source_id);
      if (source) source.chunks = 260;
    }
  }
  for (const s of demo.sources) {
    const jobs = demo.jobs.filter((j) => j.source_id === s.id).sort((a, b) => b.id - a.id);
    if (jobs[0]) s.last_job = jobs[0];
  }
}

function demoError(status: number, error: "conflict" | "not_found" | "validation_error", message: string): never {
  throw new ApiRequestError(status, { error, message, retry_after_s: null });
}

function demoStartJob(sourceId: number, kind: Job["kind"]): Job {
  const source = demo.sources.find((s) => s.id === sourceId) ?? demoError(404, "not_found", "unknown source");
  if (source.robots === "blocked") demoError(409, "conflict", "robots.txt forbids crawling");
  if (demo.jobs.some((j) => j.source_id === sourceId && (j.status === "queued" || j.status === "running")))
    demoError(409, "conflict", "a job is already running");
  const job: Job = {
    id: demo.nextId++,
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
  };
  demo.jobs.unshift(job);
  source.last_job = job;
  return job;
}

// ─────────────── calls ───────────────

export const admin = {
  me: () => withToken((t) => adminMe(t), () => ({ login: readSession()?.login ?? "admin" })),

  sources: () =>
    withToken(
      async (t) => (await adminSources(t)).sources,
      () => {
        tickDemo();
        return demo.sources;
      },
    ),

  addSource: (req: SourceCreate) =>
    withToken(
      (t) => adminAddSource(t, req),
      () => {
        if (!/^https?:\/\//.test(req.url)) demoError(422, "validation_error", "only http(s)");
        const host = new URL(req.url).hostname.replace(/^www\./, "");
        if (req.kind === "site" && demo.sources.some((s) => s.kind === "site" && s.site_id === host))
          demoError(409, "conflict", "site already added");
        const row: SourceRow = {
          id: demo.nextId++,
          kind: req.kind,
          url: req.url,
          site_id: host,
          category: req.category ?? null,
          start_urls: [req.url],
          max_depth: req.max_depth ?? (req.kind === "site" ? 4 : null),
          max_pages: req.max_pages ?? (req.kind === "site" ? 2000 : null),
          enabled: true,
          robots: "allowed",
          created_at: new Date().toISOString(),
          last_job: null,
          chunks: 0,
        };
        demo.sources.unshift(row);
        if (req.start) demoStartJob(row.id, "crawl");
        return row;
      },
    ),

  patchSource: (id: number, patch: { enabled?: boolean; max_depth?: number; max_pages?: number; category?: string }) =>
    withToken(
      (t) => adminPatchSource(t, id, patch),
      () => {
        const row = demo.sources.find((s) => s.id === id) ?? demoError(404, "not_found", "unknown source");
        Object.assign(row, patch);
        return row;
      },
    ),

  deleteSource: (id: number, purge: boolean) =>
    withToken(
      (t) => adminDeleteSource(t, id, purge),
      () => {
        demo.sources = demo.sources.filter((s) => s.id !== id);
        return { ok: true };
      },
    ),

  startJob: (sourceId: number, kind: Job["kind"]) =>
    withToken((t) => adminStartJob(t, sourceId, kind), () => demoStartJob(sourceId, kind)),

  jobs: (status?: JobStatus) =>
    withToken(
      async (t) => (await adminJobs(t, status)).jobs,
      () => {
        tickDemo();
        return demo.jobs.filter((j) => !status || j.status === status);
      },
    ),

  job: (id: number) =>
    withToken(
      (t) => adminJob(t, id),
      () => {
        tickDemo();
        return demo.jobs.find((j) => j.id === id) ?? demoError(404, "not_found", "unknown job");
      },
    ),

  cancelJob: (id: number) =>
    withToken(
      (t) => adminCancelJob(t, id),
      () => {
        const job = demo.jobs.find((j) => j.id === id) ?? demoError(404, "not_found", "unknown job");
        Object.assign(job, { status: "cancelled", eta_s: null, finished_at: new Date().toISOString() } satisfies Partial<Job>);
        return job;
      },
    ),

  feedback: (maxRating: number) =>
    withToken(
      async (t) => (await adminFeedback(t, maxRating)).items,
      () => demo.feedback.filter((f) => f.rating <= maxRating).sort((a, b) => a.rating - b.rating),
    ),

  feedbackStats: () => withToken((t) => adminFeedbackStats(t), () => feedbackStatsMock as FeedbackStats),

  /**
   * No admin list endpoint: the public GET /api/suggestions is the list (docs/API.md). It returns only questions
   * whose answers passed the check, pinned first, at most 20.
   */
  suggestions: async (lang: Lang) =>
    isMock()
      ? structuredClone(demo.suggestions.filter((s) => s.lang === lang).sort((a, b) => Number(b.pinned) - Number(a.pinned)))
      : (await publicSuggestions(lang, 20)).items,

  /** Pins (or unpins) by question text: the backend upserts on (lang, question), so an existing one is updated. */
  pinSuggestion: (question: string, lang: Lang, pinned = true) =>
    withToken(
      (t) => adminPinSuggestion(t, question, lang, pinned),
      () => {
        const existing = demo.suggestions.find((s) => s.lang === lang && s.question === question);
        if (existing) {
          existing.pinned = pinned;
          return existing;
        }
        const s: Suggestion = { id: demo.nextId++, question, lang, answer_id: null, asked_count: 0, rating_avg: null, pinned };
        demo.suggestions.unshift(s);
        return s;
      },
    ),

  hideSuggestion: (id: number) =>
    withToken(
      (t) => adminHideSuggestion(t, id),
      () => {
        demo.suggestions = demo.suggestions.filter((s) => s.id !== id);
        return { ok: true };
      },
    ),
};

// ─────────────── fetching hook ───────────────

export type Query<T> = { data: T | undefined; error: unknown; loading: boolean; reload: () => void };

/**
 * Loads `fn` now and whenever `key` changes; `every(data)` = ms until the next refresh, or null to stop
 * (jobs poll every 1–2 s while running). Keeps the last data while refreshing.
 */
export function useAdminQuery<T>(key: string, fn: () => Promise<T>, every?: (data: T) => number | null): Query<T> {
  const [state, setState] = useState<{ key: string; data: T | undefined; error: unknown }>({ key, data: undefined, error: null });
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  const everyRef = useRef(every);
  useEffect(() => {
    fnRef.current = fn;
    everyRef.current = every;
  });

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const data = await fnRef.current();
        if (!alive) return;
        setState({ key, data, error: null });
        const next = everyRef.current?.(data);
        if (next != null) timer = setTimeout(run, next);
      } catch (error) {
        if (alive) setState((s) => ({ key, data: s.key === key ? s.data : undefined, error }));
      }
    };
    void run();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const data = state.key === key ? state.data : undefined;
  const error = state.key === key ? state.error : null;
  return { data, error, loading: data === undefined && !error, reload };
}

export const isActive = (job: Job | null | undefined) => job?.status === "queued" || job?.status === "running";
