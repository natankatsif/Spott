// Anonymous per-browser id for AskRequest.session_id (docs/API.md: analytics). No personal data.

const KEY = "sessionId";
let cached: string | null = null;

export function sessionId(): string {
  if (cached) return cached;
  try {
    cached = window.localStorage.getItem(KEY);
    if (!cached) {
      cached = `anon-${crypto.randomUUID()}`;
      window.localStorage.setItem(KEY, cached);
    }
  } catch {
    cached ??= `anon-${Math.random().toString(36).slice(2)}`; // storage blocked: id for this tab only
  }
  return cached;
}
