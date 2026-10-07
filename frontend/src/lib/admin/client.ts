// Every admin call: to the server with the session's token, or, in mock mode, to the demo backend (./demo.ts).

import {
  adminAddSource,
  adminCancelJob,
  adminClearJobs,
  adminDeleteJob,
  adminDeleteSource,
  adminFeedback,
  adminFeedbackStats,
  adminGaps,
  adminHideGap,
  adminHideSuggestion,
  adminJob,
  adminJobs,
  adminLLM,
  adminLLMModels,
  adminMe,
  adminPatchSource,
  adminPinSuggestion,
  adminRecheckGap,
  adminRetryJob,
  adminSavePricing,
  adminSaveLLM,
  adminSources,
  adminStartJob,
  adminSuggestions,
  adminTestLLM,
  adminUsage,
  type Job,
  type JobStatus,
  type Lang,
  type LLMCheck,
  type LLMSettingsUpdate,
  type Pricing,
} from "../api";
import { demo } from "./demo";
import { readSession, withToken } from "./session";

type SourcePatch = { enabled?: boolean; auto_update?: boolean; max_depth?: number; max_pages?: number; category?: string };

export const admin = {
  me: () => withToken(adminMe, () => ({ login: readSession()?.login ?? "admin" })),
  /** The whole sources page in one call: rows + header totals. */
  sources: () => withToken(adminSources, demo.sources),
  addSource: (url: string) => withToken((t) => adminAddSource(t, { url }), () => demo.addSource(url)),
  patchSource: (id: number, patch: SourcePatch) =>
    withToken((t) => adminPatchSource(t, id, patch), () => demo.patchSource(id, patch)),
  deleteSource: (id: number, purge: boolean) => withToken((t) => adminDeleteSource(t, id, purge), () => demo.deleteSource(id)),
  startJob: (sourceId: number, kind: "crawl" | "refresh") =>
    withToken((t) => adminStartJob(t, sourceId, kind), () => demo.startJob(sourceId, kind)),

  // all of them: the page filters, sorts and pages them
  gaps: (hidden: boolean) => withToken((t) => adminGaps(t, { hidden, limit: 500 }), () => demo.gaps(hidden)),
  /** One model call on the server: only on a click. */
  recheckGap: (id: string) => withToken((t) => adminRecheckGap(t, id), () => demo.recheckGap(id)),
  hideGap: (id: string, hide: boolean) => withToken((t) => adminHideGap(t, id, hide), () => demo.hideGap(id, hide)),

  jobs: (status?: JobStatus) => withToken(async (t) => (await adminJobs(t, status)).jobs, () => demo.jobs(status)),
  job: (id: number) => withToken((t) => adminJob(t, id), () => demo.job(id)),
  cancelJob: (id: number) => withToken((t) => adminCancelJob(t, id), () => demo.cancelJob(id)),
  retryJob: (id: number) => withToken((t) => adminRetryJob(t, id), () => demo.retryJob(id)),
  deleteJob: (id: number) => withToken((t) => adminDeleteJob(t, id), () => demo.deleteJob(id)),
  clearJobs: () => withToken(adminClearJobs, demo.clearJobs),

  feedback: (maxRating: number) =>
    withToken(async (t) => (await adminFeedback(t, maxRating)).items, () => demo.feedback(maxRating)),
  feedbackStats: () => withToken(adminFeedbackStats, demo.feedbackStats),

  usage: (days: number) => withToken((t) => adminUsage(t, days), () => demo.usage(days)),
  savePricing: (pricing: Pricing) => withToken((t) => adminSavePricing(t, pricing), () => demo.savePricing(pricing)),

  llm: () => withToken(adminLLM, demo.llm),
  saveLLM: (update: LLMSettingsUpdate) => withToken((t) => adminSaveLLM(t, update), () => demo.saveLLM(update)),
  llmModels: (check: LLMCheck) => withToken(async (t) => (await adminLLMModels(t, check)).models, () => demo.llmModels(check)),
  testLLM: (check: LLMCheck & { model: string }) => withToken((t) => adminTestLLM(t, check), () => demo.testLLM(check)),

  /** Every quick question once (a pinned one with its texts in RO, RU and EN), also the ones waiting for their check. */
  suggestions: async () => (await withToken(adminSuggestions, demo.suggestions)).items,
  /** Pins (or unpins) by question text: the backend upserts on (lang, question), so an existing one is updated. */
  pinSuggestion: (question: string, lang: Lang, pinned = true) =>
    withToken((t) => adminPinSuggestion(t, question, lang, pinned), () => demo.pinSuggestion(question, lang, pinned)),
  hideSuggestion: (id: number) => withToken((t) => adminHideSuggestion(t, id), () => demo.hideSuggestion(id)),
};

export const isActive = (job: Job | null | undefined) => job?.status === "queued" || job?.status === "running";
