// Small formatters for the admin panel, in the interface language.

import type { UILang } from "./i18n";

const LOCALE: Record<UILang, string> = { ro: "ro-RO", ru: "ru-RU", en: "en-GB" };

export function timeAgo(iso: string | null | undefined, lang: UILang, never = "—"): string {
  if (!iso) return never;
  const s = (Date.parse(iso) - Date.now()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(LOCALE[lang], { numeric: "auto" });
  const abs = Math.abs(s);
  if (abs < 60) return rtf.format(Math.round(s), "second");
  if (abs < 3600) return rtf.format(Math.round(s / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(s / 3600), "hour");
  return rtf.format(Math.round(s / 86400), "day");
}

export function dateTime(iso: string | null | undefined, lang: UILang): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(LOCALE[lang], { dateStyle: "medium", timeStyle: "short" }).format(new Date(iso));
}

export function shortDay(day: string, lang: UILang): string {
  return new Intl.DateTimeFormat(LOCALE[lang], { day: "numeric", month: "short" }).format(new Date(day));
}

/** 412 → "6:52", 3900 → "1:05:00" */
export function duration(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${r}` : `${m}:${r}`;
}

export function number(n: number | null | undefined, lang: UILang): string {
  return n == null ? "—" : new Intl.NumberFormat(LOCALE[lang]).format(n);
}
