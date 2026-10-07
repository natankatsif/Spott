// The admin session: the token from POST /api/admin/login, kept in localStorage until expires_at; every admin call
// runs with it (withToken), and a 401 ends it. In mock mode (../mode.ts) there is no token: the call goes to the demo.

import { useSyncExternalStore } from "react";
import { type AdminSession, adminLogin, ApiRequestError, isMock } from "../api";
import sessionMock from "../mocks/admin/session.json";

const KEY = "adminSession";
const listeners = new Set<() => void>();
let cache: { raw: string | null; session: AdminSession | null } = { raw: null, session: null };
/** Set when the server ended the session (401), so the login form can say why. */
let expiredFlag = false;

export function readSession(): AdminSession | null {
  if (typeof window === "undefined") return null;
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(KEY);
  } catch {
    /* storage blocked */
  }
  if (raw === cache.raw) return cache.session;
  let session: AdminSession | null = null;
  try {
    session = raw ? (JSON.parse(raw) as AdminSession) : null;
  } catch {
    session = null;
  }
  if (session && Date.parse(session.expires_at) <= Date.now()) session = null;
  cache = { raw, session };
  return session;
}

function writeSession(session: AdminSession | null): void {
  try {
    if (session) window.localStorage.setItem(KEY, JSON.stringify(session));
    else window.localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
  listeners.forEach((l) => l());
}

export function useAdminSession(): AdminSession | null {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    readSession,
    () => null,
  );
}

/** Whether the store has been read on the client yet (the server snapshot is always "signed out"). */
export function useHydrated(): boolean {
  return useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
}

export async function signIn(login: string, password: string): Promise<void> {
  const session = isMock()
    ? { ...(sessionMock as AdminSession), login: login || "admin", expires_at: new Date(Date.now() + 12 * 3600e3).toISOString() }
    : await adminLogin({ login, password });
  expiredFlag = false;
  writeSession(session);
}

export function signOut(): void {
  writeSession(null);
}

export function takeExpiredFlag(): boolean {
  const was = expiredFlag;
  expiredFlag = false;
  return was;
}

/** Runs an admin call with the current token; a 401 ends the session. */
export async function withToken<T>(live: (token: string) => Promise<T>, mock: () => T | Promise<T>): Promise<T> {
  if (isMock()) {
    await new Promise((r) => setTimeout(r, 250));
    return structuredClone(await mock());
  }
  const session = readSession();
  if (!session) throw new ApiRequestError(401, { error: "unauthorized", message: "no session", retry_after_s: null });
  try {
    return await live(session.token);
  } catch (e) {
    if (e instanceof ApiRequestError && e.status === 401) {
      expiredFlag = true;
      writeSession(null);
    }
    throw e;
  }
}
