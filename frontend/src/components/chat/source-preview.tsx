"use client";

// Source preview (task 11): the cited page / PDF scrolled to the quote and highlighted, served by the backend at
// citation.preview_url (docs/API.md → GET /api/preview, wiring guide docs/FRONTEND-11.md).
// Desktop (≥ 1024 px): a panel next to the chat built from AI Elements WebPreview. Smaller screens: a full-screen
// sheet with the same iframe. Another citation of the document already shown is re-highlighted over postMessage,
// without reloading the frame.

import { ArrowLeftIcon, ChevronLeftIcon, ChevronRightIcon, ExternalLinkIcon, TriangleAlertIcon, XIcon } from "lucide-react";
import { createContext, type ReactNode, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewNavigationButton,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview";
import { Spinner } from "@/components/spell/spinner";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { type Citation, resolvePreviewUrl } from "@/lib/api";
import type { UIText } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// ─────────────── state ───────────────

export type PreviewState = { citations: Citation[]; index: number } | null;

type PreviewApi = {
  state: PreviewState;
  /** Show `id` out of `citations` (the answer's list: prev/next walk it). */
  open: (citations: Citation[], id: string) => void;
  close: () => void;
  go: (delta: number) => void;
};

const PreviewContext = createContext<PreviewApi | null>(null);

export function useSourcePreview(): PreviewApi {
  const ctx = useContext(PreviewContext);
  if (!ctx) throw new Error("useSourcePreview outside SourcePreviewProvider");
  return ctx;
}

export function SourcePreviewProvider({
  state,
  setState,
  children,
}: {
  state: PreviewState;
  setState: (s: PreviewState) => void;
  children: ReactNode;
}) {
  const api: PreviewApi = {
    state,
    open: (citations, id) => setState({ citations, index: Math.max(0, citations.findIndex((c) => c.id === id)) }),
    close: () => setState(null),
    go: (delta) => state && setState({ ...state, index: Math.min(state.citations.length - 1, Math.max(0, state.index + delta)) }),
  };
  return <PreviewContext.Provider value={api}>{children}</PreviewContext.Provider>;
}

/** Side panel from this width up; below it the preview opens full screen. */
const DESKTOP_QUERY = "(min-width: 1024px)";

export function useIsDesktop(): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const mql = window.matchMedia(DESKTOP_QUERY);
      mql.addEventListener("change", onChange);
      return () => mql.removeEventListener("change", onChange);
    },
    () => window.matchMedia(DESKTOP_QUERY).matches,
    () => false,
  );
}

// ─────────────── the frame ───────────────

type Found = "exact" | "words" | "start" | "none";

/** No "ready" after this long: offer the original next to the frame (the frame stays, it may still finish). */
const SLOW_MS = 8000;

// allow-same-origin: pdf.js needs it; allow-popups-to-escape-sandbox: "Deschide originalul ↗" inside the preview
// opens the city hall site outside our sandbox (some pages break in it). docs/FRONTEND-11.md §1.
const SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-presentation";

function PreviewFrame({ citation, t }: { citation: Citation; t: UIText }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  const src = resolvePreviewUrl(citation);
  // the document loaded in the frame; another citation of it is re-highlighted instead of reloaded
  const [frame, setFrame] = useState({ docId: citation.doc_id, src, ready: false, found: null as Found | null, slow: false });
  if (frame.docId !== citation.doc_id) {
    setFrame({ docId: citation.doc_id, src, ready: false, found: null, slow: false });
  }

  // same document, another quote → ask the loaded preview to move its highlight
  useEffect(() => {
    const win = frameRef.current?.contentWindow;
    if (!win || citation.doc_id !== frame.docId) return;
    win.postMessage({ type: "src-preview:highlight", line_ids: citation.line_ids }, new URL(frame.src, window.location.href).origin);
    // frame.src only changes together with docId; the ready flag of that load is not a dependency on purpose
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [citation.id]);

  // "ready" from this iframe only: several previews can exist, and in mock mode the origin is our own
  useEffect(() => {
    const onMessage = (e: MessageEvent) => {
      if (e.source !== frameRef.current?.contentWindow || e.data?.type !== "src-preview:ready") return;
      setFrame((f) => ({ ...f, ready: true, slow: false, found: e.data.found ?? null }));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  useEffect(() => {
    if (frame.ready) return;
    const timer = setTimeout(() => setFrame((f) => (f.ready ? f : { ...f, slow: true })), SLOW_MS);
    return () => clearTimeout(timer);
  }, [frame.src, frame.ready]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      {frame.ready && frame.found === "none" && (
        <div className="flex items-center gap-1.5 border-b bg-warning-bg px-3 py-1.5 text-warning text-xs">
          <TriangleAlertIcon className="size-3.5 shrink-0" /> {t.preview.changed}
        </div>
      )}
      <WebPreviewBody
        className="bg-white"
        ref={frameRef}
        referrerPolicy="no-referrer"
        sandbox={SANDBOX}
        src={frame.src}
        title={citation.document_title}
      />
      {!frame.ready && (
        <div className="absolute inset-0 flex flex-col gap-3 bg-card p-5">
          {frame.slow ? (
            <div className="m-auto flex max-w-xs flex-col items-center gap-3 text-center text-muted-foreground text-sm">
              <p>{t.preview.slow}</p>
              <a
                className="inline-flex items-center gap-1.5 font-medium text-brand underline underline-offset-2"
                href={citation.deep_link}
                rel="noreferrer"
                target="_blank"
              >
                {t.preview.original} <ExternalLinkIcon className="size-3.5" />
              </a>
            </div>
          ) : (
            <>
              <div className="h-7 w-2/3 animate-pulse rounded-md bg-muted" />
              {[92, 100, 85, 97, 64].map((w, i) => (
                <div className="h-3 animate-pulse rounded bg-muted" key={i} style={{ width: `${w}%` }} />
              ))}
              <div className="mt-2 h-16 animate-pulse rounded-lg bg-brand/10" />
              {[88, 95, 70].map((w, i) => (
                <div className="h-3 animate-pulse rounded bg-muted" key={`b${i}`} style={{ width: `${w}%` }} />
              ))}
              <Spinner className="mx-auto mt-auto size-5 text-muted-foreground" />
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────── panel (desktop) and sheet (mobile) ───────────────

function Header({ state, t, go, onClose, back }: { state: NonNullable<PreviewState>; t: UIText; go: (d: number) => void; onClose: () => void; back?: boolean }) {
  const c = state.citations[state.index];
  const many = state.citations.length > 1;
  return (
    <WebPreviewNavigation className="gap-1 px-2">
      {back ? (
        <button
          className="flex h-8 shrink-0 items-center gap-1 rounded-md px-2 font-medium text-sm hover:bg-accent"
          onClick={onClose}
          type="button"
        >
          <ArrowLeftIcon className="size-4" /> {t.preview.back}
        </button>
      ) : null}
      {many && (
        <>
          <WebPreviewNavigationButton aria-label={t.preview.prev} disabled={state.index === 0} onClick={() => go(-1)} tooltip={t.preview.prev}>
            <ChevronLeftIcon className="size-4" />
          </WebPreviewNavigationButton>
          <span className="w-9 shrink-0 text-center text-muted-foreground text-xs tabular-nums">
            {state.index + 1}/{state.citations.length}
          </span>
          <WebPreviewNavigationButton
            aria-label={t.preview.next}
            disabled={state.index === state.citations.length - 1}
            onClick={() => go(1)}
            tooltip={t.preview.next}
          >
            <ChevronRightIcon className="size-4" />
          </WebPreviewNavigationButton>
        </>
      )}
      {/* the city hall address, not ours */}
      <WebPreviewUrl className="h-8 min-w-0 flex-1 rounded-lg bg-muted/60 text-muted-foreground text-xs" readOnly title={c.url} value={c.url} />
      <WebPreviewNavigationButton aria-label={t.preview.original} onClick={() => window.open(c.deep_link, "_blank", "noopener")} tooltip={t.preview.original}>
        <ExternalLinkIcon className="size-4" />
      </WebPreviewNavigationButton>
      {!back && (
        <WebPreviewNavigationButton aria-label={t.preview.close} onClick={onClose} tooltip={t.preview.close}>
          <XIcon className="size-4" />
        </WebPreviewNavigationButton>
      )}
    </WebPreviewNavigation>
  );
}

function Caption({ citation }: { citation: Citation }) {
  return (
    <div className="border-b px-3 py-2">
      <p className="truncate font-medium text-sm" title={citation.document_title}>
        {citation.document_title}
      </p>
      <p className="truncate text-muted-foreground text-xs">
        {[citation.site, citation.location, citation.page ? `p. ${citation.page}` : null].filter(Boolean).join(" · ")}
      </p>
    </div>
  );
}

/** Desktop: fills its column (the page decides the width). */
export function SourcePreviewPanel({ t, className }: { t: UIText; className?: string }) {
  const { state, close, go } = useSourcePreview();
  if (!state) return null;
  const c = state.citations[state.index];
  return (
    <WebPreview className={cn("overflow-hidden rounded-2xl shadow-sm", className)} defaultUrl={c.url}>
      <Header go={go} onClose={close} state={state} t={t} />
      <Caption citation={c} />
      <PreviewFrame citation={c} t={t} />
    </WebPreview>
  );
}

/** Below 1024 px: the same preview full screen; "← Back" closes it and the chat stays as it was. */
export function SourcePreviewSheet({ t }: { t: UIText }) {
  const { state, close, go } = useSourcePreview();
  const c = state?.citations[state.index];
  return (
    <Sheet onOpenChange={(o) => !o && close()} open={!!state}>
      <SheetContent className="h-dvh w-full gap-0 p-0 sm:max-w-full [&>button:last-child]:hidden" side="bottom">
        <SheetTitle className="sr-only">{c?.document_title ?? ""}</SheetTitle>
        <SheetDescription className="sr-only">{c?.url ?? ""}</SheetDescription>
        {state && c && (
          <WebPreview className="rounded-none border-0" defaultUrl={c.url}>
            <Header back go={go} onClose={close} state={state} t={t} />
            <Caption citation={c} />
            <PreviewFrame citation={c} t={t} />
          </WebPreview>
        )}
      </SheetContent>
    </Sheet>
  );
}
