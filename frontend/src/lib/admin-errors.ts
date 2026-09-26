import { ApiRequestError } from "./api";
import { UI, type UILang } from "./i18n";

/** Error toast in the interface language; 409 carries the server's reason (duplicate, robots, job running). */
export function errorText(e: unknown, lang: UILang): string {
  if (e instanceof ApiRequestError) {
    const base = UI[lang].errors[e.body.error] ?? UI[lang].errors.internal;
    return e.body.error === "conflict" || e.body.error === "validation_error" ? `${base} ${e.body.message}` : base;
  }
  return UI[lang].errors.unavailable;
}
