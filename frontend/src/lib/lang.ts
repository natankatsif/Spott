// Interface language shared by every page (chat input, /sources footer). Default Romanian;
// a choice is remembered in localStorage and pushed to all subscribers, like ./mode.ts.

import { useEffect, useSyncExternalStore } from "react";
import type { UILang } from "./i18n";

export const UI_LANGS: readonly UILang[] = ["ro", "ru", "en"];

const KEY = "uiLang";
const DEFAULT: UILang = "ro";
const listeners = new Set<() => void>();

export function getUILang(): UILang {
  if (typeof window === "undefined") return DEFAULT;
  try {
    const v = window.localStorage.getItem(KEY);
    if (v && (UI_LANGS as readonly string[]).includes(v)) return v as UILang;
  } catch {
    /* storage blocked — default */
  }
  return DEFAULT;
}

export function setUILang(lang: UILang): void {
  try {
    window.localStorage.setItem(KEY, lang);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function useUILang(): UILang {
  const lang = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      // another tab changed it
      const onStorage = (e: StorageEvent) => e.key === KEY && l();
      window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(l);
        window.removeEventListener("storage", onStorage);
      };
    },
    getUILang,
    () => DEFAULT,
  );
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);
  return lang;
}
