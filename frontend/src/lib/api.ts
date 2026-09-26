// API contract with the backend. Mirrors backend/app/schemas.py field-for-field
// (backend/tests/test_contract.py fails if they drift). Human-readable spec: docs/API.md.

import { isMock } from "./mode";
import answeredRo from "./mocks/ask/answered-ro.json";
import checklistRo from "./mocks/ask/checklist-ro.json";
import conflictRo from "./mocks/ask/conflict-ro.json";
import crosslingualRu from "./mocks/ask/crosslingual-ru.json";
import notFoundRu from "./mocks/ask/not-found-ru.json";
import partialRo from "./mocks/ask/partial-ro.json";
import refusedRo from "./mocks/ask/refused-ro.json";
import corpusStatsMock from "./mocks/corpus-stats.json";
import suggestionsMock from "./mocks/suggestions.json";
import wallMock from "./mocks/wall.json";

export type Lang = "ro" | "ru";
export type SearchLang = "ro" | "ru" | "en" | "uk";
export type AskStatus = "answered" | "partial" | "not_found" | "conflict" | "refused";
export type ErrorCode =
  | "validation_error"
  | "unauthorized" // admin endpoints without the token
  | "not_found"
  | "conflict" // admin: duplicate source, robots.txt forbids crawling, a job already running
  | "rate_limited"
  | "unavailable"
  | "not_implemented"
  | "internal";

// ─────────────── POST /api/ask ───────────────

export type ChatTurn = {
  role: "user" | "assistant";
  text: string;
};

export type PageContext = {
  url?: string | null;
  title?: string | null;
};

export type AskRequest = {
  question: string;
  lang?: Lang | null; // omit → backend detects from the question
  history?: ChatTurn[]; // previous turns, oldest first, max 10
  page_context?: PageContext | null; // the widget sends the host page
  mode?: "auto" | "fast" | "deep";
  session_id?: string | null;
};

/** PDF points, origin TOP-LEFT. Scale by (rendered width / page_width). */
export type BBox = {
  page: number;
  l: number;
  t: number;
  r: number;
  b: number;
  page_width: number;
  page_height: number;
};

export type Citation = {
  id: string; // "c1" — referenced from AnswerSentence.cites
  doc_id: string;
  chunk_id: string;
  line_ids: string[];
  kind: "file" | "page";
  document_title: string;
  doc_type: string | null;
  act_number: string | null;
  published: string | null; // ISO date
  location: string | null; // "Anexa 1, pct. 3.2"
  page: number | null;
  quote: string; // verbatim from the corpus, never generated
  quote_lang: SearchLang;
  translation: string | null; // AI translation when quote_lang !== answer lang — label it as such
  url: string; // original on the city hall site
  deep_link: string; // url#page=N or url#:~:text=…
  found_on: string | null;
  site: string | null;
  file_url: string | null; // our PDF copy for the viewer (relative to API_URL)
  bboxes: BBox[];
};

export type AnswerSentence = {
  text: string;
  cites: string[]; // Citation.id values
};

export type ConflictInfo = {
  kind: "outdated" | "contradiction";
  explanation: string;
  citation_ids: string[];
  preferred_citation_id: string | null;
};

export type ChecklistStep = {
  text: string;
  cites: string[];
};

export type Checklist = {
  title: string;
  steps: ChecklistStep[];
  documents_needed: string[];
  fee: string | null;
  deadline: string | null;
};

export type NavLink = {
  title: string;
  url: string;
  kind: "page" | "service" | "contact" | "document";
  selector: string | null; // widget: CSS selector to highlight when url === current page
};

export type TraceStep = {
  tool: "search" | "grep" | "toc" | "open" | "verify";
  input: string;
  summary: string;
  ms: number;
};

export type AnswerMeta = {
  model: string | null;
  path: "fast" | "agent" | "none" | "cache"; // cache: a quick question's checked answer, replayed
  latency_ms: number;
  verified: boolean;
};

export type AskResponse = {
  id: string; // answer id for /api/feedback
  status: AskStatus;
  lang: Lang;
  answer: string; // sentences joined — for simple rendering
  sentences: AnswerSentence[];
  citations: Citation[];
  conflict: ConflictInfo | null;
  checklist: Checklist | null;
  nav_links: NavLink[];
  followups: string[];
  trace: TraceStep[];
  meta: AnswerMeta;
  /** Citation to open right away in the source viewer ("where exactly is it written?"); null = on click. */
  focus_citation_id: string | null;
  /** not_found / partial: who can help, real contacts from the corpus; [] otherwise. */
  contacts: ContactCard[];
};

export type ContactCard = {
  name: string; // institution / department
  area: string | null; // what it handles
  phone: string[];
  email: string[];
  address: string | null;
  hours: string | null;
  url: string;
  site: string;
  reason: string; // why this contact, one sentence in the answer's language
  line_ids: string[]; // every phone, e-mail and address is in these lines
  deep_link: string;
};

// ─────────────── POST /api/ask/stream (SSE) ───────────────
// Order: start → trace* → (citation* → delta* → sentence)* → done. `error` can come any time.
// `done.response` is authoritative — replace the assembled state with it. Client: ./stream.ts

export type StreamStart = {
  type: "start";
  id: string;
  lang: Lang;
};

export type StreamTrace = {
  type: "trace";
  step: TraceStep;
};

export type StreamCitation = {
  type: "citation";
  citation: Citation;
};

export type StreamDelta = {
  type: "delta";
  index: number; // sentence index
  text: string;
};

export type StreamSentence = {
  type: "sentence";
  index: number;
  sentence: AnswerSentence;
  verified: boolean;
};

export type StreamDone = {
  type: "done";
  response: AskResponse;
};

export type StreamError = {
  type: "error";
  code: ErrorCode;
  message: string;
};

export type StreamEvent =
  | StreamStart
  | StreamTrace
  | StreamCitation
  | StreamDelta
  | StreamSentence
  | StreamDone
  | StreamError;

// ─────────────── POST /api/feedback ───────────────

export type FeedbackTag = "wrong" | "outdated" | "incomplete" | "wrong_source" | "not_understood" | "helpful";

export type FeedbackRequest = {
  answer_id: string;
  rating?: number; // 1..5 stars; rating again from the same session_id overwrites
  vote?: "up" | "down"; // older clients: up = 5, down = 1 (rating or vote is required)
  tags?: FeedbackTag[];
  comment?: string | null;
  citation_id?: string | null;
  session_id?: string | null;
};

// ─────────────── GET /api/suggestions (quick questions) ───────────────

export type Suggestion = {
  id: number;
  question: string;
  lang: Lang;
  answer_id: string | null;
  asked_count: number;
  rating_avg: number | null;
  pinned: boolean;
};

export type SuggestionList = { items: Suggestion[] };

// ─────────────── /api/admin/* ───────────────
// POST /api/admin/login with the login/password from the server's env (ADMIN_LOGIN / ADMIN_PASSWORD) → session
// token (12 h). Every other admin call: Authorization: Bearer <token>; 401 → show the login form again.

export type AdminLogin = { login: string; password: string };
export type AdminSession = { token: string; login: string; expires_at: string };

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export type Job = {
  id: number;
  source_id: number | null; // null = all sources
  kind: "crawl" | "refresh";
  status: JobStatus;
  stage: "crawl" | "download" | "parse" | "index" | null;
  stage_done: number;
  stage_total: number;
  percent: number; // 0..100 over all stages: crawl 20, download 20, parse 40, index 20
  eta_s: number | null;
  started_at: string | null;
  finished_at: string | null;
  stats: Record<string, number>; // pages, documents_found, documents_downloaded, files_parsed, chunks, lines, …
  log_tail: string[];
  error: string | null;
};

export type SourceRow = {
  id: number;
  kind: "site" | "document";
  url: string;
  site_id: string;
  category: string | null;
  start_urls: string[];
  max_depth: number | null;
  max_pages: number | null;
  enabled: boolean;
  robots: "allowed" | "blocked"; // blocked: no crawl from the UI
  created_at: string;
  last_job: Job | null;
  chunks: number;
};

export type SourceCreate = {
  kind: "site" | "document";
  url: string;
  category?: string;
  max_depth?: number;
  max_pages?: number;
  start?: boolean;
};

export type FeedbackItem = {
  answer_id: string;
  rating: number;
  tags: FeedbackTag[];
  comment: string | null;
  citation_id: string | null;
  question: string | null;
  lang: Lang | null;
  status: AskStatus | null;
  answer: string | null;
  doc_ids: string[];
  path: string | null;
  created_at: string;
  updated_at: string;
};

export type FeedbackStats = {
  count: number;
  average: number | null;
  per_star: Record<string, number>; // "1".."5"
  top_tags: { tag: FeedbackTag; count: number }[];
  by_day: { day: string; count: number; average: number }[];
};

// ─────────────── errors: body of every non-2xx response ───────────────

export type ApiError = {
  error: ErrorCode;
  message: string; // English, for logs — show your own localized text by `error`
  retry_after_s: number | null;
};

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public body: ApiError,
  ) {
    super(`${status} ${body.error}: ${body.message}`);
  }
}

// ─────────────── GET /api/wall (live "break the bot" wall, poll every 2–3 s) ───────────────

export type WallItem = {
  id: string;
  ts: string;
  question: string; // personal data already masked as •••
  lang: Lang;
  status: AskStatus;
  verified: boolean;
  latency_ms: number;
  top_source: string | null;
};

export type WallResponse = {
  items: WallItem[]; // newest first
  total_questions: number;
  by_status: Record<string, number>;
};

// ─────────────── GET /api/corpus/stats (corpus health dashboard) ───────────────

export type SiteStats = {
  site: string;
  category: string | null;
  status: "indexed" | "pending" | "blocked";
  pages: number;
  documents_found: number;
  documents_downloaded: number;
  chunks: number;
  last_crawled: string | null;
};

export type CorpusTotals = {
  sites_total: number;
  sites_indexed: number;
  pages: number;
  documents_found: number;
  documents_downloaded: number;
  chunks: number;
  lines: number;
  documents_replaced: number;
  documents_removed: number;
};

export type CorpusStats = {
  updated_at: string | null;
  totals: CorpusTotals;
  sites: SiteStats[];
};

// ─────────────── GET /health ───────────────

export type HealthResponse = {
  status: string;
  device: string;
  models_loaded: boolean; // false for ~20 s after backend start — show "warming up"
  chunk_count: number;
};

// ─────────────── POST /api/search (works now, no LLM) ───────────────

export type MatchedLine = { line_id: string; idx: number; text: string; score: number | null };

export type SearchResultItem = {
  chunk_id: string;
  doc_id: string;
  citation_label: string;
  text: string;
  url: string;
  found_on: string | null;
  site: string | null;
  lang: string | null;
  page: number | null;
  matched_lines: MatchedLine[];
};

export type SearchResponse = {
  results: SearchResultItem[];
  timings_ms: { embed: number; vector_sql: number; fts_sql: number; rerank: number; total: number };
  not_found: boolean;
};

// ─────────────── client ───────────────

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
// Mock vs live backend is decided per call by isMock() (./mode.ts): runtime toggle, default NEXT_PUBLIC_API_MOCK.
export { isMock } from "./mode";

/** Throws ApiRequestError with the parsed ApiError body on any non-2xx. */
export async function checked(res: Response): Promise<Response> {
  if (res.ok) return res;
  let body: ApiError = { error: "internal", message: res.statusText, retry_after_s: null };
  try {
    body = (await res.json()) as ApiError;
  } catch {
    /* non-JSON error (proxy, network) — keep the default */
  }
  throw new ApiRequestError(res.status, body);
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checked(res)).json() as Promise<T>;
}

async function get<T>(path: string): Promise<T> {
  return (await checked(await fetch(`${API_URL}${path}`))).json() as Promise<T>;
}

export const MOCKS = {
  answered: answeredRo,
  crosslingual: crosslingualRu,
  not_found: notFoundRu,
  conflict: conflictRo,
  checklist: checklistRo,
  refused: refusedRo,
  partial: partialRo,
} as unknown as Record<string, AskResponse>;

/** Picks a mock by keywords so every UI state can be reached from the chat box. */
export function mockAnswer(question: string): AskResponse {
  const q = question.toLowerCase();
  const pick =
    /крокод|crocodil|рецепт|pizza/.test(q) ? "not_found"
    : /ignor|prompt|игнорир|забудь/.test(q) ? "refused"
    : /conflict|contradic|противореч|конфликт/.test(q) ? "conflict"
    : /formular|școal|scoal|школ|шаг|pas/.test(q) ? "checklist"
    : /când|cand|termen|когда|срок/.test(q) ? "partial"
    : /[а-яё]/.test(q) ? "crosslingual"
    : "answered";
  return { ...MOCKS[pick], id: `${MOCKS[pick].id}-${Date.now()}` };
}

export async function ask(req: AskRequest): Promise<AskResponse> {
  if (isMock()) {
    await new Promise((r) => setTimeout(r, 700));
    return mockAnswer(req.question);
  }
  return post<AskResponse>("/api/ask", req);
}

export async function sendFeedback(req: FeedbackRequest): Promise<void> {
  if (isMock()) return;
  await post<{ ok: boolean }>("/api/feedback", req);
}

export async function search(query: string, lang?: SearchLang, k = 5): Promise<SearchResponse> {
  return post<SearchResponse>("/api/search", { query, lang, k });
}

/** Absolute URL of our PDF copy for the source viewer, or null for web pages. */
export function documentFileUrl(c: Citation): string | null {
  return c.file_url ? `${API_URL}${c.file_url}` : null;
}

export async function health(): Promise<HealthResponse> {
  return get<HealthResponse>("/health");
}

export async function wall(after?: string): Promise<WallResponse> {
  if (isMock()) return wallMock as unknown as WallResponse;
  return get<WallResponse>(`/api/wall${after ? `?after=${encodeURIComponent(after)}` : ""}`);
}

export async function suggestions(lang: Lang, limit = 6): Promise<SuggestionList> {
  if (isMock()) return { items: (suggestionsMock as unknown as SuggestionList).items.filter((s) => s.lang === lang) };
  return get<SuggestionList>(`/api/suggestions?lang=${lang}&limit=${limit}`);
}

export async function corpusStats(): Promise<CorpusStats> {
  if (isMock()) return corpusStatsMock as unknown as CorpusStats;
  return get<CorpusStats>("/api/corpus/stats");
}

// ─────────────── admin client ───────────────
// const session = await adminLogin({ login, password }); keep session.token (e.g. sessionStorage);
// every call below takes it. ApiRequestError with status 401 = session over, log in again.

async function adminCall<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}/api/admin${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await checked(res)).json() as Promise<T>;
}

export const adminLogin = (req: AdminLogin) => post<AdminSession>("/api/admin/login", req);
export const adminMe = (token: string) => adminCall<{ login: string }>(token, "GET", "/me");

export const adminSources = (token: string) => adminCall<{ sources: SourceRow[] }>(token, "GET", "/sources");
export const adminAddSource = (token: string, req: SourceCreate) => adminCall<SourceRow>(token, "POST", "/sources", req);
export const adminPatchSource = (
  token: string,
  id: number,
  patch: { enabled?: boolean; max_depth?: number; max_pages?: number; category?: string },
) => adminCall<SourceRow>(token, "PATCH", `/sources/${id}`, patch);
export const adminDeleteSource = (token: string, id: number, purge = false) =>
  adminCall<{ ok: boolean }>(token, "DELETE", `/sources/${id}${purge ? "?purge=true" : ""}`);

export const adminStartJob = (token: string, sourceId: number, kind: "crawl" | "refresh") =>
  adminCall<Job>(token, "POST", `/sources/${sourceId}/jobs`, { kind });
export const adminJobs = (token: string, status?: JobStatus) =>
  adminCall<{ jobs: Job[] }>(token, "GET", `/jobs${status ? `?status=${status}` : ""}`);
/** Poll every 1–2 s while status is queued or running. */
export const adminJob = (token: string, id: number) => adminCall<Job>(token, "GET", `/jobs/${id}`);
export const adminCancelJob = (token: string, id: number) => adminCall<Job>(token, "POST", `/jobs/${id}/cancel`);

export const adminFeedback = (token: string, maxRating = 2, limit = 50) =>
  adminCall<{ items: FeedbackItem[] }>(token, "GET", `/feedback?max_rating=${maxRating}&limit=${limit}`);
export const adminFeedbackStats = (token: string) => adminCall<FeedbackStats>(token, "GET", "/feedback/stats");

export const adminPinSuggestion = (token: string, question: string, lang: Lang) =>
  adminCall<Suggestion>(token, "POST", "/suggestions", { question, lang, pinned: true });
export const adminHideSuggestion = (token: string, id: number) =>
  adminCall<{ ok: boolean }>(token, "DELETE", `/suggestions/${id}`);
