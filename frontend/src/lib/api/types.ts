// The API's contract as TypeScript types: the requests and responses of docs/API.md (backend/openapi.json). They are
// checked against the generated ../api-schema.d.ts in ../api-contract.ts, so the build fails if they drift.

export type Lang = "ro" | "ru" | "en"; // answers follow the question; the documents are RO/RU
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
  /** The source preview (GET /api/preview/{doc_id}): scrolled to the quote, highlighted. `/api/…` → prefix API_URL;
   * mocks use a frontend path (`/mocks/preview/…`) → as is. See resolvePreviewUrl(). */
  preview_url: string;
  preview_kind: "page" | "pdf" | "text";
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
// Order: start → trace* → (citation* → delta* → sentence)* → citation* → done. `error` can come any time.
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
  /** admin list only: shown to people, waiting for the next re-check, or not answered well */
  check?: "ok" | "pending" | "failed" | null;
  /** the same question in each language it is shown in; the page picks the UI language's */
  texts?: Partial<Record<"ro" | "ru" | "en", string>> | null;
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
  // check: the automatic nightly look for changes; backlog: the autopilot finishing a source batch by batch
  kind: "crawl" | "refresh" | "check" | "backlog";
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

export type SourceStatus = "indexed" | "pending" | "running" | "queued" | "failed" | "blocked" | "disabled";

/** While a job is queued/running: poll GET /sources every 2 s. */
export type SourceProgress = {
  job_id: number;
  stage: "crawl" | "download" | "parse" | "index" | null;
  percent: number;
  eta_s: number | null;
  current: string | null; // the file or address the stage is on right now ("parsed tarife.pdf")
};

/** One row of the admin's single sources table: everything it shows, from one call. */
export type SourceRow = {
  id: number;
  kind: "site" | "document";
  url: string;
  site_id: string; // domain
  title: string | null;
  category: string | null;
  category_source: string | null; // toml | index | rule | keywords | default | manual
  start_urls: string[];
  max_depth: number | null;
  max_pages: number | null;
  enabled: boolean;
  robots: "allowed" | "blocked"; // blocked: no crawl from the UI
  // disabled > blocked > running > queued > failed (last job) > indexed (has chunks) > pending
  status: SourceStatus;
  pages: number;
  documents_found: number;
  documents_downloaded: number;
  chunks: number;
  lines: number;
  last_crawled: string | null;
  progress: SourceProgress | null;
  last_error: string | null;
  created_at: string;
  last_job: Job | null;
  // automatic updates
  auto_update: boolean;
  check_method: string | null; // wordpress | sitemap | sitemap-new+fingerprint | fingerprint …
  last_checked_at: string | null;
  next_check_at: string | null;
  stale_signals: number; // people's signals since the last check
  // what the autopilot still has to do here; all zero = this source is finished
  crawl_left: number; // pages the last crawl did not reach
  documents_pending: number; // found but never downloaded
  files_pending: number; // downloaded, not parsed yet
  pages_pending: number; // crawled, not parsed yet
};

export type SourceList = { sources: SourceRow[]; totals: CorpusTotals };

/** Only `url`: the server decides kind, category and crawl settings (the other fields are for older clients). */
export type SourceCreate = { url: string };

export type SourceDetected = {
  kind: "site" | "document";
  category: string;
  category_source: string; // rule | keywords | default | manual | existing (merged)
  title: string | null;
  crawl_depth: number | null;
  max_pages: number | null;
  reason: string; // one short English sentence
};

/** 201 new source · 200 merged into an existing one (`merged_into` = its id). */
export type SourceAdded = SourceRow & { detected: SourceDetected; merged_into: number | null };

// ─────────────── admin: gaps (questions without a (full) answer) ───────────────

export type GapQuestion = { answer_id: string; question: string; lang: Lang; status: "not_found" | "partial"; ts: string };
export type GapRecheck = { status: AskStatus; verified: boolean; answer_id: string; ts: string };

export type Gap = {
  id: string; // the answer_id of the group's first question
  example: string;
  title: { ro: string; ru: string } | null; // what is missing, named by the model that grouped the questions
  questions: GapQuestion[]; // the latest 20, oldest first
  count: number;
  last_asked: string;
  langs: Lang[];
  status: "not_found" | "partial"; // the worst in the group
  missing: string[]; // what the partial answers said is missing
  hint_sites: { site: string; hits: number }[]; // found but not used: whom to ask
  rechecked: GapRecheck | null;
  hidden: boolean;
  topic: GapTopic; // set by a small model, once per group
  last_answer: string | null; // what the assistant said the last time it answered part of it
};

export const GAP_TOPICS = [
  "transport", "urbanism", "education", "health", "social", "utilities", "taxes", "documents", "council", "environment", "culture", "other",
] as const;
export type GapTopic = (typeof GAP_TOPICS)[number];

export type GapList = {
  items: Gap[];
  totals: { not_found: number; partial: number; groups: number };
  topics: { topic: GapTopic; groups: number }[]; // biggest first
};

export type FeedbackItem = {
  answer_id: string;
  rating: number;
  tags: FeedbackTag[];
  comment: string | null;
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

// ─────────────── corpus totals (the admin's sources page) ───────────────

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

// ─────────────── GET /health ───────────────

export type HealthResponse = {
  status: string;
  device: string;
  models_loaded: boolean; // false for ~20 s after backend start — show "warming up"
  chunk_count: number;
};

// ─────────────── POST /api/visits ───────────────

export type VisitorCount = { visitors: number }; // unique browsers so far, this one included

// ─────────────── /api/admin/llm: API keys and the model of each role ───────────────

export type LLMProvider = "openai" | "anthropic" | "gemini" | "custom";
export type LLMRole = "answer" | "fast" | "deep";
export type LLMProviderView = {
  id: LLMProvider;
  label: string;
  api: "openai" | "anthropic";
  needs_key: boolean;
  needs_url: boolean;
  has_key: boolean;
  key_hint: string | null; // "…abcd": the key itself never leaves the server
  key_source: "admin" | "env" | null;
  base_url: string | null;
  default_url: string | null;
};
export type LLMRoleModel = { provider: LLMProvider; model: string };
export type LLMSettings = {
  providers: LLMProviderView[];
  roles: Record<LLMRole, (LLMRoleModel & { source: "admin" | "env" }) | null>;
};
/** api_key / base_url: omitted = keep, "" = remove the saved one. A role null = back to the server's .env (deep: off). */
export type LLMSettingsUpdate = {
  providers?: Partial<Record<LLMProvider, { api_key?: string; base_url?: string }>>;
  roles?: Partial<Record<LLMRole, LLMRoleModel | null>>;
};
export type LLMCheck = { provider: LLMProvider; api_key?: string; base_url?: string };
export type LLMModelTest = { ok: boolean; model: string | null; latency_ms: number; error: string | null };

// ─────────────── /api/admin/usage: tokens and money spent on models ───────────────

export type Currency = "USD" | "EUR" | "MDL";
/** USD per 1M tokens (the unit providers publish). */
export type ModelPrice = { input: number; output: number };
export type Pricing = {
  currency: Currency; // shown in
  rates: Record<Currency, number>; // units per 1 USD
  prices: Record<string, ModelPrice>; // model → price
  budget_usd: number | null; // per calendar month
};
export type UsageTotals = { calls: number; input_tokens: number; output_tokens: number; cost_usd: number; unpriced_calls: number };
export type UsageDay = { day: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
export type UsageModel = {
  provider: string;
  model: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number | null; // null: no price set
  price: ModelPrice | null;
};
export type UsageKind = { kind: string; calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
export type UsageReport = {
  days: number;
  pricing: Pricing;
  today: UsageTotals;
  month: UsageTotals;
  month_forecast_usd: number;
  range: UsageTotals;
  all_time: UsageTotals;
  questions: number;
  cost_per_question_usd: number | null;
  daily: UsageDay[];
  models: UsageModel[];
  kinds: UsageKind[];
  known_models: string[];
};
