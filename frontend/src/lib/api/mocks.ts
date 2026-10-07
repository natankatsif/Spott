// Mock mode (../mode.ts, switched off for now): answers picked from the mocks by keywords, so every UI state can be
// reached from the chat box without a backend.

import answeredRo from "../mocks/ask/answered-ro.json";
import checklistRo from "../mocks/ask/checklist-ro.json";
import conflictRo from "../mocks/ask/conflict-ro.json";
import crosslingualRu from "../mocks/ask/crosslingual-ru.json";
import notFoundRu from "../mocks/ask/not-found-ru.json";
import partialRo from "../mocks/ask/partial-ro.json";
import refusedRo from "../mocks/ask/refused-ro.json";
import suggestionsMock from "../mocks/suggestions.json";
import type { AskResponse, Lang, SuggestionList } from "./types";

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

export function mockSuggestions(lang: Lang | "en"): SuggestionList {
  return { items: (suggestionsMock as unknown as SuggestionList).items.filter((s) => s.lang === lang) };
}
