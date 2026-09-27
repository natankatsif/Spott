"use client";

// Source preview (task 11): the cited page / PDF scrolled to the quote and highlighted, served by the backend at
// citation.preview_url (docs/API.md → GET /api/preview, wiring guide docs/FRONTEND-11.md).
// Desktop (≥ 1024 px): a panel next to the chat built from AI Elements WebPreview. Smaller screens: a full-screen
// sheet with the same iframe. Another citation of the document already shown is re-highlighted over postMessage,
// without reloading the frame.
//
// Whether the quote is on the page is only known after the preview loaded (its "ready" message says found: exact |
// words | start | none), so the panel shows "looking for it…" first and then the result. What can be known up front:
// a citation without a line to look for can't be shown in the document at all → canPreview() hides the button.

import {
  ArrowLeftIcon,
  CheckCircle2Icon,
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleAlertIcon,
  ExternalLinkIcon,
  SearchXIcon,
  XIcon,
} from "lucide-react";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Shimmer } from "@/components/ai-elements/shimmer";
import {
  WebPreview,
  WebPreviewBody,
  WebPreviewNavigation,
  WebPreviewNavigationButton,
  WebPreviewUrl,
} from "@/components/ai-elements/web-preview";
import { Spinner } from "@/components/spell/spinner";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { type Citation, resolvePreviewUrl, signalOutdated } from "@/lib/api";
import type { UIText } from "@/lib/i18n";
import { cn } from "@/lib/utils";

/** A preview can point at the quote only when the citation has a line to look for (the backend puts it in the URL). */
export const canPreview = (c: Citation) => Boolean(c.preview_url) && c.line_ids.length > 0;

// ─────────────── state ───────────────

export type PreviewState = { citations: Citation[]; index: number } | null;

/** searching → found (exact) | approx (the page changed a little) | missing (not on the page) | failed (never loaded) */
export type LocateStatus = "searching" | "found" | "approx" | "missing" | "failed";

type PreviewApi = {
  state: PreviewState;
  /** Where the shown citation stands; "searching" until the preview answers. */
  status: LocateStatus;
  /** Show `id` out of `citations` (the answer's previewable citations: prev/next walk them). */
  open: (citations: Citation[], id: string) => void;
  close: () => void;
  go: (delta: number) => void;
  report: (citationId: string, status: LocateStatus) => void;
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
  // keyed by citation id: a newly shown citation is "searching" without an explicit reset
  const [located, setLocated] = useState<{ id: string; status: LocateStatus } | null>(null);
  // stable: the frame's listener and failure timer depend on it
  const report = useCallback((id: string, status: LocateStatus) => setLocated({ id, status }), []);
  const current = state?.citations[state.index];
  const api: PreviewApi = {
    state,
    status: current && located?.id === current.id ? located.status : "searching",
    open: (citations, id) => {
      const list = citations.filter(canPreview);
      if (list.length) setState({ citations: list, index: Math.max(0, list.findIndex((c) => c.id === id)) });
    },
    close: () => setState(null),
    go: (delta) => state && setState({ ...state, index: Math.min(state.citations.length - 1, Math.max(0, state.index + delta)) }),
    report,
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

const toStatus = (found: Found | undefined): LocateStatus =>
  found === "exact" ? "found" : found === "none" ? "missing" : found ? "approx" : "found";

/** No "ready" after this long: the document didn't open (unknown doc, site down…). The frame stays: it may finish.
 * Generous on purpose: a big PDF or a slow city hall page can take well over 10 s, and saying "didn't open" and
 * then showing the document a few seconds later is worse than waiting. */
const FAIL_MS = 30000;
/** From here the spinner says why it is taking long, instead of giving up. */
const SLOW_MS = 7000;

// allow-same-origin: pdf.js needs it; allow-popups-to-escape-sandbox: "Deschide originalul ↗" inside the preview
// opens the city hall site outside our sandbox (some pages break in it). docs/FRONTEND-11.md §1.
const SANDBOX = "allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox allow-presentation";

function PreviewFrame({ citation, t }: { citation: Citation; t: UIText }) {
  const { status, report } = useSourcePreview();
  const frameRef = useRef<HTMLIFrameElement>(null);
  const src = resolvePreviewUrl(citation);
  // the document loaded in the frame; another citation of it is re-highlighted instead of reloaded
  const [frame, setFrame] = useState({ docId: citation.doc_id, src, ready: false });
  if (frame.docId !== citation.doc_id) {
    setFrame({ docId: citation.doc_id, src, ready: false });
  }
  // the citation the next "ready" answers for (read inside the message listener)
  const citationRef = useRef(citation.id);
  const docIdRef = useRef(citation.doc_id);
  useEffect(() => {
    citationRef.current = citation.id;
    docIdRef.current = citation.doc_id;
  });

  // same document, another quote → ask the loaded preview to move its highlight; it answers with "ready" again
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
      setFrame((f) => ({ ...f, ready: true }));
      const status = toStatus(e.data.found);
      report(citationRef.current, status);
      // the live page no longer has the quote: tell the backend, so the site is checked sooner
      if (status === "missing") signalOutdated(docIdRef.current);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [report]);

  // still looking after SLOW_MS → say it is slow; after FAIL_MS → the document didn't open
  const [slowFor, setSlowFor] = useState<string | null>(null);
  useEffect(() => {
    if (status !== "searching") return;
    const id = citation.id;
    const slow = setTimeout(() => setSlowFor(id), SLOW_MS);
    const timer = setTimeout(() => report(id, "failed"), FAIL_MS);
    return () => {
      clearTimeout(slow);
      clearTimeout(timer);
    };
  }, [status, citation.id, report]);

  return (
    <div className="relative flex min-h-0 flex-1 flex-col p-1">
      {status === "missing" && (
        <div className="mb-1 flex gap-2.5 rounded-xl bg-warning-bg px-3 py-2.5 text-sm" role="status">
          <SearchXIcon className="mt-0.5 size-4 shrink-0 text-warning" />
          <div className="min-w-0">
            <p className="font-medium text-warning">{t.preview.notFound}</p>
            <p className="mt-0.5 text-foreground/70 text-xs">{t.preview.notFoundHint}</p>
            <blockquote className="mt-1.5 line-clamp-3 border-warning/40 border-l-2 pl-2 text-foreground/80 text-xs italic">{citation.quote}</blockquote>
          </div>
        </div>
      )}
      <WebPreviewBody
        className="block rounded-xl bg-white"
        ref={frameRef}
        referrerPolicy="no-referrer"
        sandbox={SANDBOX}
        src={frame.src}
        title={citation.document_title}
      />
      {/* an answer for a quote of the already loaded document comes fast: only cover a frame that is (re)loading */}
      {(!frame.ready || status === "failed") && (
        <div className="absolute inset-1 flex flex-col rounded-xl bg-card/95 p-6 backdrop-blur-[2px]">
          {status === "failed" ? (
            <div className="m-auto flex max-w-xs flex-col items-center gap-2 text-center">
              <CircleAlertIcon className="size-6 text-muted-foreground" />
              <p className="font-medium text-sm">{t.preview.failed}</p>
              <p className="text-muted-foreground text-xs">{t.preview.failedHint}</p>
              <a
                className="mt-2 inline-flex items-center gap-1.5 font-medium text-brand text-sm underline underline-offset-2"
                href={citation.deep_link}
                rel="noreferrer"
                target="_blank"
              >
                {t.preview.original} <ExternalLinkIcon className="size-3.5" />
              </a>
            </div>
          ) : (
            <div className="m-auto flex w-full max-w-sm flex-col items-center gap-3 text-center">
              <Spinner className="size-6 text-brand" />
              <Shimmer className="font-medium text-sm">{t.preview.searching}</Shimmer>
              {slowFor === citation.id && <p className="text-muted-foreground text-xs animate-in fade-in">{t.preview.slow}</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─────────────── the status next to a citation ───────────────

/** "looking…" / "found" / "approx" / "not found" for the citation shown in the preview (panel caption, chat list). */
export function LocateBadge({ status, t, className }: { status: LocateStatus; t: UIText; className?: string }) {
  const map = {
    searching: { icon: <Spinner className="size-3" />, text: t.preview.searching, tone: "text-muted-foreground" },
    found: { icon: <CheckCircle2Icon className="size-3.5" />, text: t.preview.found, tone: "text-success" },
    approx: { icon: <CheckCircle2Icon className="size-3.5" />, text: t.preview.foundApprox, tone: "text-warning" },
    missing: { icon: <SearchXIcon className="size-3.5" />, text: t.preview.notFound, tone: "text-warning" },
    failed: { icon: <CircleAlertIcon className="size-3.5" />, text: t.preview.failed, tone: "text-destructive" },
  }[status];
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1 text-xs", map.tone, className)} role="status">
      <span className="shrink-0">{map.icon}</span>
      <span className="truncate">{map.text}</span>
    </span>
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

function Caption({ citation, t }: { citation: Citation; t: UIText }) {
  const { status } = useSourcePreview();
  return (
    <div className="border-b px-3 py-2">
      <p className="truncate font-medium text-sm" title={citation.document_title}>
        {citation.document_title}
      </p>
      <div className="mt-0.5 flex items-center justify-between gap-3">
        <p className="min-w-0 truncate text-muted-foreground text-xs">
          {[citation.site, citation.location, citation.page ? `p. ${citation.page}` : null].filter(Boolean).join(" · ")}
        </p>
        <LocateBadge className="max-w-[60%] shrink-0" status={status} t={t} />
      </div>
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
      <Caption citation={c} t={t} />
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
            <Caption citation={c} t={t} />
            <PreviewFrame citation={c} t={t} />
          </WebPreview>
        )}
      </SheetContent>
    </Sheet>
  );
}
