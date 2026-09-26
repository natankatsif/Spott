// AI SDK ChatTransport over our own contract (docs/API.md → POST /api/ask/stream).
// useChat() talks to this class; it turns our SSE events (live backend) or a replayed mock
// into AI SDK UI message chunks. Mock ↔ live is decided per request by isMock() (./mode.ts),
// so the header toggle switches the whole chat without a reload.
//
// Parts of an assistant message:
//   text (id "s<N>")      — sentence N, streamed; no citation markers inside
//   data-sentence (id "s<N>") — {index, cites, verified} when sentence N is complete
//   data-citation (id = citation.id) — full Citation from the DB (quote, deep link, bboxes…)
//   data-trace            — agent step ("search", "open"…)
//   data-answer (id "answer") — final AskResponse: status, conflict, checklist, nav_links, followups
//   source-url            — one per citation, so generic AI Elements <Sources> work too

import type { ChatTransport, UIMessage, UIMessageChunk } from "ai";
import { API_URL, checked, ApiRequestError, type AskResponse, type ChatTurn, type Citation, type Lang, type StreamEvent, type TraceStep } from "./api";
import { isMock } from "./mode";
import { mockStream, parseSse } from "./stream";

export type SentenceMeta = { index: number; cites: string[]; verified: boolean };

export type ChatData = {
  trace: TraceStep;
  citation: Citation;
  sentence: SentenceMeta;
  answer: AskResponse;
};

export type ChatMessage = UIMessage<never, ChatData>;

export type TransportOptions = { lang: () => Lang | null };

function historyOf(messages: ChatMessage[]): ChatTurn[] {
  return messages.slice(0, -1).flatMap((m): ChatTurn[] => {
    const text = m.parts.flatMap((p) => (p.type === "text" ? [p.text.trim()] : [])).join(" ");
    return text ? [{ role: m.role === "user" ? "user" : "assistant", text }] : [];
  }).slice(-10);
}

async function* liveEvents(body: unknown, signal?: AbortSignal): AsyncGenerator<StreamEvent> {
  const res = await fetch(`${API_URL}/api/ask/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal,
  });
  await checked(res);
  if (!res.body) throw new Error("empty stream body");
  yield* parseSse(res.body);
}

/** Our stream event → AI SDK chunks. `open` tracks sentences whose text part has started. */
function toChunks(ev: StreamEvent, open: Set<number>): UIMessageChunk<never, ChatData>[] {
  switch (ev.type) {
    case "start":
      return [{ type: "start", messageId: ev.id }];
    case "trace":
      return [{ type: "data-trace", data: ev.step }];
    case "citation": {
      const c = ev.citation;
      return [
        { type: "data-citation", id: c.id, data: c },
        { type: "source-url", sourceId: c.id, url: c.deep_link, title: c.document_title },
      ];
    }
    case "delta": {
      const id = `s${ev.index}`;
      const start: UIMessageChunk<never, ChatData>[] = open.has(ev.index) ? [] : [{ type: "text-start", id }];
      open.add(ev.index);
      return [...start, { type: "text-delta", id, delta: ev.text }];
    }
    case "sentence": {
      const id = `s${ev.index}`;
      const out: UIMessageChunk<never, ChatData>[] = [];
      if (!open.has(ev.index)) out.push({ type: "text-start", id }, { type: "text-delta", id, delta: ev.sentence.text });
      open.delete(ev.index);
      out.push({ type: "text-end", id });
      out.push({ type: "data-sentence", id, data: { index: ev.index, cites: ev.sentence.cites, verified: ev.verified } });
      return out;
    }
    case "done":
      return [{ type: "data-answer", id: "answer", data: ev.response }, { type: "finish" }];
    case "error":
      return [{ type: "error", errorText: ev.code }];
  }
}

export class MunicipalChatTransport implements ChatTransport<ChatMessage> {
  constructor(private readonly opts: TransportOptions) {}

  async sendMessages({ messages, abortSignal }: Parameters<ChatTransport<ChatMessage>["sendMessages"]>[0]) {
    const last = messages.at(-1);
    const question = last?.parts.flatMap((p) => (p.type === "text" ? [p.text] : [])).join(" ").trim() ?? "";
    const request = { question, lang: this.opts.lang(), history: historyOf(messages), mode: "auto" as const };
    const events = isMock() ? mockStream(question) : liveEvents(request, abortSignal);

    return new ReadableStream<UIMessageChunk>({
      async start(controller) {
        const open = new Set<number>();
        try {
          for await (const ev of events) {
            if (abortSignal?.aborted) break;
            for (const chunk of toChunks(ev, open)) controller.enqueue(chunk as UIMessageChunk);
          }
        } catch (e) {
          if (!abortSignal?.aborted) {
            const code = e instanceof ApiRequestError ? e.body.error : "unavailable";
            controller.enqueue({ type: "error", errorText: code });
          }
        } finally {
          controller.close();
        }
      },
    });
  }

  async reconnectToStream() {
    return null; // answers are short; no resumable streams
  }
}

/** Everything the UI needs from one assistant message, assembled from its parts. */
export type AnswerView = {
  sentences: { index: number; text: string; cites: string[]; verified: boolean; done: boolean }[];
  citations: Citation[];
  trace: TraceStep[];
  answer: AskResponse | null;
};

export function viewOf(message: ChatMessage): AnswerView {
  const texts = new Map<number, { text: string; done: boolean }>();
  const metas = new Map<number, SentenceMeta>();
  const citations: Citation[] = [];
  const trace: TraceStep[] = [];
  let answer: AskResponse | null = null;
  for (const p of message.parts) {
    if (p.type === "text") {
      // Text parts carry no id in UIMessage; sentences start in order, so position = sentence index.
      texts.set(texts.size, { text: p.text, done: p.state !== "streaming" });
    } else if (p.type === "data-sentence") metas.set(p.data.index, p.data);
    else if (p.type === "data-citation") citations.push(p.data);
    else if (p.type === "data-trace") trace.push(p.data);
    else if (p.type === "data-answer") answer = p.data;
  }
  if (answer) {
    const a: AskResponse = answer;
    return {
      sentences: a.sentences.map((s, index) => ({ index, text: s.text, cites: s.cites, verified: a.meta.verified, done: true })),
      citations: a.citations,
      trace: a.trace,
      answer: a,
    };
  }
  const sentences = [...texts.entries()]
    .sort(([a], [b]) => a - b)
    .map(([index, t]) => ({
      index,
      text: t.text.trim(),
      cites: metas.get(index)?.cites ?? [],
      verified: metas.get(index)?.verified ?? false,
      done: metas.has(index),
    }));
  return { sentences, citations, trace, answer };
}
