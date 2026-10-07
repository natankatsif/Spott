// Loading admin data in a component: refreshed when its key changes, polled while it asks to be.

import { useCallback, useEffect, useRef, useState } from "react";

export type Query<T> = { data: T | undefined; error: unknown; loading: boolean; reload: () => void };

/**
 * Loads `fn` now and whenever `key` changes; `every(data)` = ms until the next refresh, or null to stop
 * (jobs poll every 1–2 s while running). Keeps the last data while refreshing.
 */
export function useAdminQuery<T>(key: string, fn: () => Promise<T>, every?: (data: T) => number | null): Query<T> {
  const [state, setState] = useState<{ key: string; data: T | undefined; error: unknown }>({ key, data: undefined, error: null });
  const [nonce, setNonce] = useState(0);
  const fnRef = useRef(fn);
  const everyRef = useRef(every);
  useEffect(() => {
    fnRef.current = fn;
    everyRef.current = every;
  });

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const run = async () => {
      try {
        const data = await fnRef.current();
        if (!alive) return;
        setState({ key, data, error: null });
        const next = everyRef.current?.(data);
        if (next != null) timer = setTimeout(run, next);
      } catch (error) {
        if (alive) setState((s) => ({ key, data: s.key === key ? s.data : undefined, error }));
      }
    };
    void run();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [key, nonce]);

  const reload = useCallback(() => setNonce((n) => n + 1), []);
  const data = state.key === key ? state.data : undefined;
  const error = state.key === key ? state.error : null;
  return { data, error, loading: data === undefined && !error, reload };
}
