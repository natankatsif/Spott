"use client";

import { useState } from "react";
import { ask, type AskResponse, type Lang } from "@/lib/api";

const UI = {
  ro: {
    title: "Asistentul Primăriei Chișinău",
    placeholder: "Scrieți întrebarea…",
    send: "Trimite",
    sources: "Surse",
    error: "Serviciul nu este disponibil. Încercați mai târziu.",
  },
  ru: {
    title: "Ассистент Примэрии Кишинэу",
    placeholder: "Задайте вопрос…",
    send: "Отправить",
    sources: "Источники",
    error: "Сервис недоступен. Попробуйте позже.",
  },
} satisfies Record<Lang, Record<string, string>>;

type Message = { role: "user"; text: string } | { role: "assistant"; response: AskResponse };

export default function Home() {
  const [lang, setLang] = useState<Lang>("ro");
  const [question, setQuestion] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const t = UI[lang];

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    const text = question.trim();
    if (!text || loading) return;
    setMessages((m) => [...m, { role: "user", text }]);
    setQuestion("");
    setLoading(true);
    setError(false);
    try {
      const response = await ask(text, lang);
      setMessages((m) => [...m, { role: "assistant", response }]);
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 p-4">
      <header className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">{t.title}</h1>
        <div className="flex gap-1">
          {(["ro", "ru"] as const).map((l) => (
            <button
              key={l}
              onClick={() => setLang(l)}
              className={`rounded px-2 py-1 text-sm uppercase ${
                l === lang ? "bg-foreground text-background" : "border border-current/20"
              }`}
            >
              {l}
            </button>
          ))}
        </div>
      </header>

      <section className="flex flex-1 flex-col gap-3">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <p key={i} className="self-end rounded-lg bg-current/5 px-3 py-2">
              {m.text}
            </p>
          ) : (
            <article key={i} className="rounded-lg border border-current/15 px-3 py-2">
              <p>{m.response.answer}</p>
              {m.response.citations.length > 0 && (
                <ul className="mt-2 space-y-1 text-sm opacity-80">
                  <li className="font-medium">{t.sources}:</li>
                  {m.response.citations.map((c, j) => (
                    <li key={j}>
                      <a href={c.url} target="_blank" rel="noreferrer" className="underline">
                        {c.document_title}
                      </a>
                      {c.location && `, ${c.location}`} — «{c.passage}»
                    </li>
                  ))}
                </ul>
              )}
            </article>
          ),
        )}
        {error && <p className="text-sm text-red-600">{t.error}</p>}
      </section>

      <form onSubmit={onSubmit} className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t.placeholder}
          className="flex-1 rounded border border-current/20 bg-transparent px-3 py-2"
        />
        <button
          type="submit"
          disabled={loading}
          className="rounded bg-foreground px-4 py-2 text-background disabled:opacity-50"
        >
          {loading ? "…" : t.send}
        </button>
      </form>
    </main>
  );
}
