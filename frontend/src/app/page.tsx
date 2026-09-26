"use client";

import { useChat } from "@ai-sdk/react";
import { RotateCcwIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { AssistantAnswer } from "@/components/chat/assistant-answer";
import { LogoMark } from "@/components/logo-mark";
import { PromptInput } from "@/components/PromptInput";
import { Button } from "@/components/ui/button";
import { type ErrorCode, health, type Lang, suggestions as fetchSuggestions } from "@/lib/api";
import { type ChatMessage, MunicipalChatTransport, viewOf } from "@/lib/chat-transport";
import { UI, type UILang } from "@/lib/i18n";
import { getUILang, setUILang, UI_LANGS, useUILang } from "@/lib/lang";
import { setApiMode, useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const SPEECH: Record<UILang, string> = { ro: "ro-RO", ru: "ru-RU", en: "en-US" };

// Effort switch in the input: index 0 = fast (one retrieval), 1 = deep (agent). Read by the transport at send time.
let askMode: "fast" | "deep" = "deep";

/**
 * Quick questions on the empty screen: only the ones an admin pinned (GET /api/suggestions, pinned come first).
 * The rest are proposals for the admin to pick from. None pinned (or the English UI: answers are RO/RU only) = none shown.
 */
function useQuickQuestions(lang: UILang, mode: string): readonly string[] {
  const [loaded, setLoaded] = useState<{ key: string; items: string[] } | null>(null);
  const key = `${lang}-${mode}`;
  useEffect(() => {
    if (lang === "en") return;
    let alive = true;
    fetchSuggestions(lang, 20)
      .then((r) => alive && setLoaded({ key, items: r.items.filter((s) => s.pinned).slice(0, 6).map((s) => s.question) }))
      .catch(() => alive && setLoaded({ key, items: [] }));
    return () => {
      alive = false;
    };
  }, [lang, key]);
  return loaded?.key === key ? loaded.items : [];
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

const textOf = (m: ChatMessage) => m.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
/** The answer comes in the question's language, so its labels should too (before `done` tells us for sure). */
const langOfQuestion = (q: string | undefined, fallback: Lang): Lang => (q && /[а-яё]/i.test(q) ? "ru" : q ? "ro" : fallback);

export default function Home() {
  const lang = useUILang();
  const mode = useApiMode();
  const t = UI[lang];

  const transport = useMemo(() => new MunicipalChatTransport({ lang: () => { const l = getUILang(); return l === "en" ? null : l; }, mode: () => askMode }), []);
  const { messages, sendMessage, status, stop, setMessages, error, clearError } = useChat<ChatMessage>({ transport });
  const busy = status === "submitted" || status === "streaming";
  const [speechError, setSpeechError] = useState<string | null>(null);
  useEffect(() => {
    if (!speechError) return;
    const timer = setTimeout(() => setSpeechError(null), 6000);
    return () => clearTimeout(timer);
  }, [speechError]);

  // AskRequest.question is 1–2000 characters; say so here instead of a round trip to a 422
  const [tooLong, setTooLong] = useState(false);
  const backend = useBackendStatus(mode === "live");
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
    <main className="relative flex h-dvh flex-col overflow-hidden">
      <header className="absolute inset-x-0 top-0 z-20 flex items-center justify-end gap-2 p-4">
        {messages.length > 0 && (
          <Button onClick={() => { stop(); setMessages([]); }} size="sm" variant="ghost">
            <RotateCcwIcon className="size-3.5" /> {t.newChat}
          </Button>
        )}
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
      </header>

      <Conversation className="flex-1">
        <ConversationContent className="mx-auto w-full max-w-3xl px-4 pt-16 pb-56">
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
                (() => {
                  const view = viewOf(m);
                  const answerLang = lang === "en" ? "en" : (view.answer?.lang ?? langOfQuestion(textOf(messages[i - 1] ?? m), lang));
                  return (
                    <AssistantAnswer
                      key={m.id}
                      onFollowup={ask}
                      streaming={busy && i === messages.length - 1}
                      t={UI[answerLang]}
                      view={view}
                    />
                  );
                })()
              ),
            )
          )}
          {status === "submitted" && messages.at(-1)?.role === "user" && (
            <AssistantAnswer
              onFollowup={ask}
              streaming
              t={UI[lang === "en" ? "en" : langOfQuestion(textOf(messages.at(-1)!), lang)]}
              view={{ sentences: [], citations: [], trace: [], answer: null }}
            />
          )}
          {errorText && <p className="text-destructive text-sm">{errorText}</p>}
        </ConversationContent>
        <ConversationScrollButton className="bottom-48" />
      </Conversation>

      <div className="dock-glow pointer-events-none absolute inset-x-0 bottom-0 z-10 h-56" />
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
    </main>
  );
}
