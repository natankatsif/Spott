// Mock ↔ live backend switch, flippable at runtime (the toggle in the corner), no rebuild.
// Default comes from NEXT_PUBLIC_API_MOCK; a choice made in the UI is remembered in localStorage.

import { useSyncExternalStore } from "react";

export type ApiMode = "mock" | "live";

const KEY = "apiMode";
const listeners = new Set<() => void>();
const envDefault: ApiMode = process.env.NEXT_PUBLIC_API_MOCK === "1" ? "mock" : "live";

export function getApiMode(): ApiMode {
  if (typeof window === "undefined") return envDefault;
  try {
    const v = window.localStorage.getItem(KEY);
    if (v === "mock" || v === "live") return v;
  } catch {
    /* storage blocked — fall back to env */
  }
  return envDefault;
}

export function setApiMode(mode: ApiMode): void {
  try {
    window.localStorage.setItem(KEY, mode);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export const isMock = (): boolean => getApiMode() === "mock";

export function useApiMode(): ApiMode {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    getApiMode,
    () => envDefault,
  );
}
