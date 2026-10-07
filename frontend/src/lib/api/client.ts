// The public endpoints: questions, ratings and signals, the preview URL, health, quick questions, visits. In mock mode
// (../mode.ts) they answer from ./mocks.ts.

import { isMock } from "../mode";
import { API_URL, get, post } from "./http";
import { mockAnswer, mockSuggestions } from "./mocks";
import type { AskRequest, AskResponse, Citation, FeedbackRequest, HealthResponse, Lang, SuggestionList, VisitorCount } from "./types";

export async function ask(req: AskRequest): Promise<AskResponse> {
  if (isMock()) {
    await new Promise((r) => setTimeout(r, 700));
    return mockAnswer(req.question);
  }
  return post<AskResponse>("/api/ask", req);
}

const signalled = new Set<string>();
/** A cited passage the preview no longer finds on the live page: the backend checks that site sooner. Once per
 * document per visit, and it never gets in the way (errors are ignored). */
export function signalOutdated(docId: string): void {
  if (isMock() || signalled.has(docId)) return;
  signalled.add(docId);
  void post<{ ok: boolean }>("/api/signals/outdated", { doc_id: docId }).catch(() => {});
}

export async function sendFeedback(req: FeedbackRequest): Promise<void> {
  if (isMock()) return;
  await post<{ ok: boolean }>("/api/feedback", req);
}

/** The source preview URL for an iframe / new tab: backend paths get API_URL, mock paths (/mocks/preview/…) stay. */
export function resolvePreviewUrl(c: Pick<Citation, "preview_url">): string {
  return c.preview_url.startsWith("/api/") ? `${API_URL}${c.preview_url}` : c.preview_url;
}

export async function health(): Promise<HealthResponse> {
  return get<HealthResponse>("/health");
}

/** `en`: the Romanian questions with their English text (answers are RO/RU only). */
export async function suggestions(lang: Lang | "en", limit = 6): Promise<SuggestionList> {
  if (isMock()) return mockSuggestions(lang);
  return get<SuggestionList>(`/api/suggestions?lang=${lang}&limit=${limit}`);
}

/** Counts this browser once (its anonymous session id) and returns the number of unique visitors. */
export async function visit(visitorId: string): Promise<VisitorCount> {
  if (isMock()) return { visitors: 1284 };
  return post<VisitorCount>("/api/visits", { visitor_id: visitorId });
}
