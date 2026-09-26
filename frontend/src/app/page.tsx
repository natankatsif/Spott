"use client";

import { useChat } from "@ai-sdk/react";
import { MessageSquareTextIcon, RotateCcwIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import { Message, MessageContent } from "@/components/ai-elements/message";
import { Suggestion, Suggestions } from "@/components/ai-elements/suggestion";
import { AssistantAnswer } from "@/components/chat/assistant-answer";
import { PromptInput } from "@/components/PromptInput";
import { Button } from "@/components/ui/button";
import type { ErrorCode, Lang } from "@/lib/api";
import { type ChatMessage, MunicipalChatTransport, viewOf } from "@/lib/chat-transport";
import { UI, type UILang } from "@/lib/i18n";
import { setApiMode, useApiMode } from "@/lib/mode";
import { cn } from "@/lib/utils";

const LANGS: Record<string, UILang> = { [UI.ro.langName]: "ro", [UI.ru.langName]: "ru", [UI.en.langName]: "en" };
const SPEECH: Record<UILang, string> = { ro: "ro-RO", ru: "ru-RU", en: "en-US" };

const textOf = (m: ChatMessage) => m.parts.map((p) => (p.type === "text" ? p.text : "")).join("");
/** The answer comes in the question's language, so its labels should too (before `done` tells us for sure). */
const langOfQuestion = (q: string | undefined, fallback: Lang): Lang => (q && /[а-яё]/i.test(q) ? "ru" : q ? "ro" : fallback);

export default function Home() {
  const [lang, setLang] = useState<UILang>("ro");
  const langRef = useRef(lang);
  langRef.current = lang;
  const mode = useApiMode();
  const t = UI[lang];

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const transport = useMemo(() => new MunicipalChatTransport({ lang: () => (langRef.current === "en" ? null : langRef.current) }), []);
  const { messages, sendMessage, status, stop, setMessages, error, clearError } = useChat<ChatMessage>({ transport });
  const busy = status === "submitted" || status === "streaming";

  const ask = (text: string) => {
    const q = text.trim();
    if (!q || busy) return;
    clearError();
    void sendMessage({ text: q });
  };

  const errorText = error ? (t.errors[error.message as ErrorCode] ?? t.errors.internal) : null;

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
            <ConversationEmptyState
              className="min-h-[55vh]"
              description={t.subtitle}
              icon={<MessageSquareTextIcon className="size-10" />}
              title={t.title}
            />
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
        {messages.length === 0 && (
          <div className="w-full max-w-[480px]">
            <Suggestions>
              {t.suggestions.map((s) => (
                <Suggestion className="bg-card font-normal shadow-sm" key={s} onClick={ask} suggestion={s} variant="ghost" />
              ))}
            </Suggestions>
          </div>
        )}
        <PromptInput
          className="w-full"
          alwaysExpanded
          efforts={[...t.efforts]}
          maxAttachments={0}
          model={UI[lang].langName}
          models={[UI.ro.langName, UI.ru.langName, UI.en.langName]}
          onModelChange={(name) => setLang(LANGS[name] ?? "ro")}
          onSubmit={(value) => ask(value)}
          placeholder={t.placeholder}
          speechLang={SPEECH[lang]}
        />
        <p className="text-foreground/80 text-sm">
          {t.poweredBy}{" "}
          <a className="underline underline-offset-2" href="/sources">
            {t.providers}
          </a>
        </p>
      </footer>
    </main>
  );
}
