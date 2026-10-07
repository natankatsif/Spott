// HTTP to the backend: its URL, the error every non-2xx response becomes, JSON calls, admin calls with the token.

import type { ApiError } from "./types";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiRequestError extends Error {
  constructor(
    public status: number,
    public body: ApiError,
  ) {
    super(`${status} ${body.error}: ${body.message}`);
  }
}

/** Throws ApiRequestError with the parsed ApiError body on any non-2xx. */
export async function checked(res: Response): Promise<Response> {
  if (res.ok) return res;
  let body: ApiError = { error: "internal", message: res.statusText, retry_after_s: null };
  try {
    body = (await res.json()) as ApiError;
  } catch {
    /* non-JSON error (proxy, network) — keep the default */
  }
  throw new ApiRequestError(res.status, body);
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return (await checked(res)).json() as Promise<T>;
}

export async function get<T>(path: string): Promise<T> {
  return (await checked(await fetch(`${API_URL}${path}`))).json() as Promise<T>;
}

// const session = await adminLogin({ login, password }); keep session.token; every admin call takes it.
// ApiRequestError with status 401 = session over, log in again.
export async function adminCall<T>(token: string, method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API_URL}/api/admin${path}`, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body === undefined ? {} : { "Content-Type": "application/json" }) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return (await checked(res)).json() as Promise<T>;
}
