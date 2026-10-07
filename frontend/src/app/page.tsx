"use client";

import { useChat } from "@ai-sdk/react";
import { SquarePenIcon, UsersIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { AssistantAnswer } from "@/components/chat/assistant-answer";
import { ChatSidebar, ChatSidebarProvider } from "@/components/chat/chat-sidebar";
import {
  canPreview,
  type PreviewState,
  SourcePreviewPanel,
  SourcePreviewProvider,
  SourcePreviewSheet,
  useIsDesktop,
} from "@/components/chat/source-preview";
import { LogoMark } from "@/components/logo-mark";
import { PromptInput } from "@/components/prompt-input";
import { Button } from "@/components/ui/button";
import { SidebarTrigger } from "@/components/ui/sidebar";
import { type ErrorCode, health, type Suggestion as QuickQuestion, suggestions as fetchSuggestions, visit } from "@/lib/api";
import { loadChat, newChatId, saveChat } from "@/lib/chat-history";
import { type ChatMessage, MunicipalChatTransport, viewOf } from "@/lib/chat-transport";
import { UI, type UILang } from "@/lib/i18n";
import { getUILang, setUILang, UI_LANGS, useUILang } from "@/lib/lang";
import { type ApiMode, /* setApiMode, */ useApiMode } from "@/lib/mode";
import { sessionId } from "@/lib/session";
import { cn } from "@/lib/utils";

const SPEECH: Record<UILang, string> = { ro: "ro-RO", ru: "ru-RU", en: "en-US" };

// Effort switch in the input: index 0 = fast (one retrieval), 1 = deep (agent). Read by the transport at send time.
let askMode: "fast" | "deep" = "deep";

/**
 * Quick questions on the empty screen: only the ones an admin pinned (GET /api/suggestions, pinned come first). One
 * set for every language: each question carries its text in RO, RU and EN, and the UI language only picks the text.
 */
function useQuickQuestions(lang: UILang, mode: string): readonly string[] {
  const [loaded, setLoaded] = useState<{ mode: string; items: QuickQuestion[] } | null>(null);
  useEffect(() => {
    let alive = true;
    fetchSuggestions("ro", 20)
      .then((r) => alive && setLoaded({ mode, items: r.items.filter((s) => s.pinned).slice(0, 6) }))
      .catch(() => alive && setLoaded({ mode, items: [] }));
    return () => {
      alive = false;
    };
  }, [mode]);
  return loaded?.mode === mode ? loaded.items.map((s) => s.texts?.[lang] ?? s.question) : [];
}

type BackendStatus = "ok" | "warming" | "down";

/**
 * docs/API.md, GET /health: "warming up" while models_loaded=false (~20 s after a backend start), polled every 3 s
 * only in that state. An unreachable backend is checked again when the tab gets focus, not polled in the background.
 */
function useBackendStatus(enabled: boolean): BackendStatus {
  const [status, setStatus] = useState<BackendStatus>("ok");
  useEffect(() => {
    if (!enabled) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const check = async () => {
      clearTimeout(timer);
      try {
        const h = await health();
        if (stopped) return;
        setStatus(h.models_loaded ? "ok" : "warming");
        if (!h.models_loaded) timer = setTimeout(check, 3000);
      } catch {
        if (!stopped) setStatus("down");
      }
    };
    const onFocus = () => void check();
    void check();
    window.addEventListener("focus", onFocus);
    return () => {
      stopped = true;
      clearTimeout(timer);
      window.removeEventListener("focus", onFocus);
      setStatus("ok");
    };
  }, [enabled]);
  return enabled ? status : "ok";
}

/** Unique visitors for the header: this browser is counted once (POST /api/visits); null until known or on error. */
function useVisitorCount(mode: ApiMode): number | null {
  const [count, setCount] = useState<number | null>(null);
  useEffect(() => {
    let stopped = false;
    visit(sessionId())
      .then((r) => { if (!stopped) setCount(r.visitors); })
      .catch(() => { if (!stopped) setCount(null); }); // no counter rather than an error
    return () => { stopped = true; };
  }, [mode]);
  return count;
}

/**
 * Phones: keeps the chat exactly as tall as the visible area and the document at scroll 0. iOS Safari ignores
 * interactive-widget and, when the keyboard opens, scrolls the whole page up to the focused input; sizing the page
 * to visualViewport and undoing that scroll keeps the header and the input in place.
 */
function useLockedViewport(): void {
  useEffect(() => {
    const root = document.documentElement;
    root.classList.add("chat-locked");

    // No zooming on the chat screen. Android honours the viewport meta; iOS Safari ignores user-scalable=no, so its
    // pinch (gesture events) and two-finger moves are cancelled too. Only here: /sources and /admin still zoom.
    const meta = document.querySelector<HTMLMetaElement>('meta[name="viewport"]');
    const metaBefore = meta?.content;
    if (meta) meta.content = `${metaBefore}, maximum-scale=1, user-scalable=no`;
    const cancel = (e: Event) => e.preventDefault();
    const cancelPinch = (e: TouchEvent) => e.touches.length > 1 && e.preventDefault();
    document.addEventListener("gesturestart", cancel);
    document.addEventListener("gesturechange", cancel);
    document.addEventListener("touchmove", cancelPinch, { passive: false });

    const vv = window.visualViewport;
    const update = () => {
      if (vv) root.style.setProperty("--chat-h", `${Math.round(vv.height)}px`);
      if (window.scrollY !== 0 || window.scrollX !== 0) window.scrollTo(0, 0);
    };
    update();
    vv?.addEventListener("resize", update);
    vv?.addEventListener("scroll", update);
    window.addEventListener("scroll", update);
    return () => {
      if (meta && metaBefore !== undefined) meta.content = metaBefore;
      document.removeEventListener("gesturestart", cancel);
      document.removeEventListener("gesturechange", cancel);
      document.removeEventListener("touchmove", cancelPinch);
      root.classList.remove("chat-locked");
      root.style.removeProperty("--chat-h");
      vv?.removeEventListener("resize", update);
      vv?.removeEventListener("scroll", update);
      window.removeEventListener("scroll", update);
    };
  }, []);
}

/** `/?embed=1`: the chat inside the site widget (components/widget): the widget's own header carries the buttons, so
 * this page drops its header. "Open in the app" moves the conversation to a new tab of the app, relayed by the
 * widget on the host page (the frame and the tab can't talk directly: a third-party frame's storage is its own):
 *   widget → frame  {type: "spott:export"}           frame → widget  {type: "spott:chat", messages}
 *   tab → widget    {type: "spott:ready"}            widget → tab    {type: "spott:chat", messages}
 * The tab is `/?from=widget`, opened by the widget, so the widget is its opener. */
const noSubscribe = () => () => {};
function useWidgetBridge(messages: ChatMessage[], onImport: (messages: ChatMessage[]) => void): boolean {
  const embed = useSyncExternalStore(
    noSubscribe,
    () => new URLSearchParams(window.location.search).get("embed") === "1",
    () => false,
  );
  const current = useRef(messages);
  const importRef = useRef(onImport);
  useEffect(() => {
    current.current = messages;
    importRef.current = onImport;
  });
  // in the widget: hand the conversation to the page around it when it asks
  useEffect(() => {
    if (!embed) return;
    const onMessage = (e: MessageEvent) => {
      if (e.source !== window.parent || e.data?.type !== "spott:export") return;
      window.parent.postMessage({ type: "spott:chat", messages: current.current }, e.origin);
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [embed]);
  // opened from the widget: say we're here, take the conversation, then drop the marker from the address
  // (read once: in development React runs this effect twice, and the address has changed by the second time)
  const fromWidget = useRef<boolean | null>(null);
  useEffect(() => {
    fromWidget.current ??= new URLSearchParams(window.location.search).get("from") === "widget";
    if (embed || !fromWidget.current || !window.opener) return;
    const opener = window.opener as Window;
    const onMessage = (e: MessageEvent) => {
      if (e.source !== opener || e.data?.type !== "spott:chat" || !Array.isArray(e.data.messages)) return;
      window.removeEventListener("message", onMessage);
      fromWidget.current = false;
      if (e.data.messages.length > 0) importRef.current(e.data.messages as ChatMessage[]);
    };
    window.addEventListener("message", onMessage);
    opener.postMessage({ type: "spott:ready" }, "*");
    if (window.location.search) window.history.replaceState(null, "", window.location.pathname);
    return () => window.removeEventListener("message", onMessage);
  }, [embed]);
  return embed;
}

const textOf = (m: ChatMessage) => m.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
/** The answer comes in the question's language, so its labels should too (before `done` tells us for sure). */

export default function Home() {
  const lang = useUILang();
  const mode = useApiMode();
  const t = UI[lang];

  const transport = useMemo(() => new MunicipalChatTransport({ lang: () => getUILang(), mode: () => askMode }), []);
  const { messages, sendMessage, status, stop, setMessages, error, clearError } = useChat<ChatMessage>({ transport });
  const busy = status === "submitted" || status === "streaming";

  // Local chat history (lib/chat-history.ts): kept in this browser only, never sent to the backend.
  const [chatId, setChatId] = useState(newChatId);
  // what is already stored for this chat: opening a saved chat must not re-save it (and move it to the top)
  const savedRef = useRef<{ chatId: string; count: number; lastId?: string } | null>(null);
  useEffect(() => {
    if (busy || messages.length === 0) return;
    const saved = savedRef.current;
    const lastId = messages.at(-1)?.id;
    if (saved && saved.chatId === chatId && saved.count === messages.length && saved.lastId === lastId) return;
    saveChat(chatId, messages);
    savedRef.current = { chatId, count: messages.length, lastId };
  }, [messages, busy, chatId]);

  const startNewChat = () => {
    stop();
    setMessages([]);
    setPreview(null);
    setChatId(newChatId());
    savedRef.current = null;
  };

  const openChat = (id: string) => {
    const stored = loadChat(id);
    if (!stored || id === chatId) return;
    stop();
    setPreview(null);
    savedRef.current = { chatId: id, count: stored.length, lastId: stored.at(-1)?.id };
    setChatId(id);
    setMessages(stored);
  };

  // Source preview (task 11). On desktop it opens by itself on each new answer: at focus_citation_id, else the first
  // citation (docs/FRONTEND-11.md §1); smaller screens open it from the citation chips.
  const isDesktop = useIsDesktop();
  const [preview, setPreview] = useState<PreviewState>(null);
  const [autoOpenedFor, setAutoOpenedFor] = useState<string | null>(null);
  const lastMessage = messages.at(-1);
  const lastView = !busy && lastMessage?.role === "assistant" ? viewOf(lastMessage) : null;
  const lastAnswer = lastView?.answer;
  // only citations with a line to find can be shown in the document (canPreview); none → no panel
  const previewable = lastView?.citations.filter(canPreview) ?? [];
  if (isDesktop && lastView && lastAnswer && lastAnswer.id !== autoOpenedFor) {
    setAutoOpenedFor(lastAnswer.id);
    if (previewable.length > 0) {
      const focus = previewable.findIndex((c) => c.id === lastAnswer.focus_citation_id);
      setPreview({ citations: previewable, index: Math.max(0, focus) });
    }
  }
  // a conversation carried over from the site widget: a chat of its own here, saved like any other
  const embed = useWidgetBridge(messages, (carried) => {
    stop();
    setPreview(null);
    const id = newChatId();
    saveChat(id, carried);
    savedRef.current = { chatId: id, count: carried.length, lastId: carried.at(-1)?.id };
    setChatId(id);
    setMessages(carried);
  });
  const [speechError, setSpeechError] = useState<string | null>(null);
  useEffect(() => {
    if (!speechError) return;
    const timer = setTimeout(() => setSpeechError(null), 6000);
    return () => clearTimeout(timer);
  }, [speechError]);

  // AskRequest.question is 1–2000 characters; say so here instead of a round trip to a 422
  const [tooLong, setTooLong] = useState(false);
  const backend = useBackendStatus(mode === "live");
  const visitors = useVisitorCount(mode);
  useLockedViewport();
  const quickQuestions = useQuickQuestions(lang, mode);

  const ask = (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    clearError();
    setTooLong(q.length > 2000);
    if (q.length > 2000) return;
    void sendMessage({ text: q });
  };

  const errorText = tooLong
    ? t.errors.validation_error
    : error ? (t.errors[error.message as ErrorCode] ?? t.errors.internal) : null;

  return (
    <SourcePreviewProvider setState={setPreview} state={preview}>
      <div className="h-[var(--chat-h,100dvh)]">
        <ChatSidebarProvider>
          <ChatSidebar
            activeId={chatId}
            onDeleted={(id) => (id === null || id === chatId) && startNewChat()}
            onNew={startNewChat}
            onSelect={openChat}
            t={t}
          />
          <main className="relative flex h-full min-w-0 flex-1 overflow-hidden">
            <div className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
              <div aria-hidden className="top-blur pointer-events-none absolute inset-x-0 top-0 z-10 h-20" />
              <header className={cn("absolute inset-x-0 top-0 z-20 flex items-center justify-end gap-2 p-4", embed && "hidden")}>
                {/* phones: the menu is a slide-over opened from here; on desktop it is docked on the left */}
                <SidebarTrigger aria-label={t.history.toggle} className="mr-auto md:hidden" />
                {visitors !== null && (
                  <span className="flex items-center gap-1 text-muted-foreground text-xs tabular-nums" title={t.visitors}>
                    <UsersIcon aria-hidden className="size-3.5" />
                    <span className="sr-only">{t.visitors}: </span>
                    {visitors.toLocaleString(lang)}
                  </span>
                )}
                {messages.length > 0 && (
                  <Button className="md:hidden" onClick={startNewChat} size="sm" variant="ghost">
                    <SquarePenIcon className="size-3.5" /> {t.newChat}
                  </Button>
                )}
                {/* Mock/Live switch: mock mode is off (lib/mode.ts always returns "live"); kept to bring it back
                <div className="flex rounded-full border bg-card p-0.5 text-xs shadow-sm" title={t.modeHint}>
                  {(["mock", "live"] as const).map((m) => (
                    <button
                      className={cn(
                        "rounded-full px-3 py-1 font-medium transition-colors",
                        mode === m ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground",
                      )}
                      key={m}
                      onClick={() => setApiMode(m)}
                      type="button"
                    >
                      {m === "mock" ? t.mock : t.live}
                    </button>
                  ))}
                </div>
                */}
              </header>

              <Conversation className="flex-1">
                <ConversationContent className={cn("mx-auto w-full max-w-3xl px-4 pb-56", embed ? "pt-6" : "pt-16")}>
                  {messages.length === 0 ? (
                    <ConversationEmptyState className="min-h-[55vh] gap-4">
                      <LogoMark className="h-[88px] w-auto" />
                      <h1 className="font-semibold text-[32px] leading-tight tracking-tight">{t.greeting}</h1>
                    </ConversationEmptyState>
                  ) : (
                    messages.map((m, i) =>
                      m.role === "user" ? (
                        <Message from="user" key={m.id}>
                          <MessageContent>{textOf(m)}</MessageContent>
                        </Message>
                      ) : (
                        <AssistantAnswer
                          key={m.id}
                          onFollowup={ask}
                          streaming={busy && i === messages.length - 1}
                          t={t}
                          view={viewOf(m)}
                        />
                      ),
                    )
                  )}
                  {status === "submitted" && messages.at(-1)?.role === "user" && (
                    <AssistantAnswer
                      onFollowup={ask}
                      streaming
                      t={t}
                      view={{ sentences: [], citations: [], trace: [], answer: null }}
                    />
                  )}
                  {errorText && <p className="text-destructive text-sm">{errorText}</p>}
                </ConversationContent>
                <ConversationScrollButton className="bottom-48" />
              </Conversation>

              <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 z-10 h-56">
                <div className="dock-blur absolute inset-0">
                  <div />
                  <div />
                  <div />
                  <div />
                </div>
              </div>
              <footer className="absolute inset-x-0 bottom-0 z-20 flex flex-col items-center gap-3 px-4 pb-4">
                {messages.length === 0 && quickQuestions.length > 0 && (
                  <div className="w-full max-w-[480px]">
                    <Suggestions>
                      {quickQuestions.map((s) => (
                        <Suggestion className="bg-card font-normal shadow-sm" key={s} onClick={ask} suggestion={s} variant="ghost" />
                      ))}
                    </Suggestions>
                  </div>
                )}
                <PromptInput
                  className="w-full"
                  activeWidth={736 /* the message column: max-w-3xl minus its px-4 */}
                  alwaysExpanded
                  forceActive={messages.length > 0}
                  efforts={[...t.efforts]}
                  maxAttachments={0}
                  model={lang.toUpperCase()}
                  models={UI_LANGS.map((l) => l.toUpperCase())}
                  onModelChange={(code) => setUILang(code.toLowerCase() as UILang)}
                  onSpeechError={(code) => setSpeechError(code)}
                  onSubmit={(value, { effort }) => {
                    askMode = t.efforts.indexOf(effort as never) === 0 ? "fast" : "deep";
                    ask(value);
                  }}
                  placeholder={t.placeholder}
                  speechLang={SPEECH[lang]}
                />
                {backend === "warming" && <p className="text-center text-muted-foreground text-xs">{t.warmingUp}</p>}
                {backend === "down" && (
                  <p className="text-center text-muted-foreground text-xs" role="status">
                    {t.errors.unavailable}
                  </p>
                )}
                {speechError && (
                  <p className="max-w-[480px] text-center text-destructive text-xs animate-in fade-in" role="alert">
                    {t.speechErrors[speechError as keyof typeof t.speechErrors] ?? t.speechErrors.other}
                  </p>
                )}
                <p className="text-center text-foreground/80 text-sm">
                  {t.poweredBy}{" "}
                  <a className="underline underline-offset-2" href="/sources">
                    {t.providers}
                  </a>
                </p>
              </footer>
            </div>
            {/* the glow spans the whole width, under the document panel too, not just the chat column */}
            <div aria-hidden className="dock-glow pointer-events-none absolute inset-x-0 bottom-0 z-10 h-56" />
            {isDesktop && preview && (
              <aside className="relative z-20 w-[min(46vw,760px)] shrink-0 py-3 pr-3 duration-300 animate-in fade-in slide-in-from-right-6">
                <SourcePreviewPanel className="h-full" t={t} />
              </aside>
            )}
          </main>
        </ChatSidebarProvider>
      </div>
      {!isDesktop && <SourcePreviewSheet t={t} />}
    </SourcePreviewProvider>
  );
}
