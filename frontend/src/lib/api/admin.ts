// The admin endpoints (/api/admin/*), each with the session's token. ../admin/ keeps the session and the demo backend.

import { adminCall, post } from "./http";
import type {
  AdminLogin,
  AdminSession,
  FeedbackItem,
  FeedbackStats,
  GapList,
  GapRecheck,
  Job,
  JobStatus,
  Lang,
  LLMCheck,
  LLMModelTest,
  LLMSettings,
  LLMSettingsUpdate,
  Pricing,
  SourceAdded,
  SourceCreate,
  SourceList,
  SourceRow,
  Suggestion,
  SuggestionList,
  UsageReport,
} from "./types";

export const adminLogin = (req: AdminLogin) => post<AdminSession>("/api/admin/login", req);
export const adminMe = (token: string) => adminCall<{ login: string }>(token, "GET", "/me");

export const adminSources = (token: string) => adminCall<SourceList>(token, "GET", "/sources");
export const adminAddSource = (token: string, req: SourceCreate) => adminCall<SourceAdded>(token, "POST", "/sources", req);
export const adminPatchSource = (
  token: string,
  id: number,
  patch: { enabled?: boolean; auto_update?: boolean; max_depth?: number; max_pages?: number; category?: string },
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
/** The same work again as a new job (only a finished one). */
export const adminRetryJob = (token: string, id: number) => adminCall<Job>(token, "POST", `/jobs/${id}/retry`);
/** Removes a finished job from the history. */
export const adminDeleteJob = (token: string, id: number) => adminCall<{ ok: boolean }>(token, "DELETE", `/jobs/${id}`);
/** Removes every finished job; queued and running ones stay. */
export const adminClearJobs = (token: string) => adminCall<{ deleted: number }>(token, "DELETE", "/jobs");

export const adminFeedback = (token: string, maxRating = 2, limit = 50) =>
  adminCall<{ items: FeedbackItem[] }>(token, "GET", `/feedback?max_rating=${maxRating}&limit=${limit}`);
export const adminFeedbackStats = (token: string) => adminCall<FeedbackStats>(token, "GET", "/feedback/stats");

export const adminPinSuggestion = (token: string, question: string, lang: Lang, pinned = true) =>
  adminCall<Suggestion>(token, "POST", "/suggestions", { question, lang, pinned });
export const adminSuggestions = (token: string) => adminCall<SuggestionList>(token, "GET", "/suggestions");
export const adminHideSuggestion = (token: string, id: number) =>
  adminCall<{ ok: boolean }>(token, "DELETE", `/suggestions/${id}`);

export type GapQuery = { status?: ("not_found" | "partial")[]; lang?: Lang; days?: number; limit?: number; hidden?: boolean };
export const adminGaps = (token: string, q: GapQuery = {}) => {
  const p = new URLSearchParams();
  if (q.status?.length) p.set("status", q.status.join(","));
  if (q.lang) p.set("lang", q.lang);
  if (q.days) p.set("days", String(q.days));
  if (q.limit) p.set("limit", String(q.limit));
  if (q.hidden) p.set("hidden", "1");
  return adminCall<GapList>(token, "GET", `/gaps${p.size ? `?${p}` : ""}`);
};
/** One model call: only on a click, never automatically. */
export const adminRecheckGap = (token: string, id: string) => adminCall<GapRecheck>(token, "POST", `/gaps/${encodeURIComponent(id)}/recheck`);
export const adminHideGap = (token: string, id: string, hide = true) =>
  adminCall<{ ok: boolean }>(token, "POST", `/gaps/${encodeURIComponent(id)}/${hide ? "hide" : "unhide"}`);

export const adminLLM = (token: string) => adminCall<LLMSettings>(token, "GET", "/llm");
export const adminSaveLLM = (token: string, update: LLMSettingsUpdate) => adminCall<LLMSettings>(token, "PUT", "/llm", update);
/** The provider's chat models; also checks the key (typed or saved). */
export const adminLLMModels = (token: string, check: LLMCheck) =>
  adminCall<{ models: string[] }>(token, "POST", "/llm/models", check);
/** One tiny structured call to the model: key, name and JSON output. */
export const adminTestLLM = (token: string, check: LLMCheck & { model: string }) =>
  adminCall<LLMModelTest>(token, "POST", "/llm/test", check);

export const adminUsage = (token: string, days: number) => adminCall<UsageReport>(token, "GET", `/usage?days=${days}`);
export const adminSavePricing = (token: string, pricing: Pricing) => adminCall<Pricing>(token, "PUT", "/usage/pricing", pricing);
