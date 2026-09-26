// Streaming client for POST /api/ask/stream. EventSource can't POST, so we read the SSE body with fetch.
//
//   const state = await askStream({ question, lang }, (s) => setAnswer(s));
//
// `onUpdate` gets a fresh StreamState after every event: render sentences as they grow,
// citation cards as they arrive, trace as "searching…" steps.

import {
  ApiRequestError,
  API_MOCK,
  API_URL,
  checked,
  mockAnswer,
  type AnswerSentence,
  type AskRequest,
  type AskResponse,
  type Citation,
  type StreamEvent,
  type TraceStep,
} from "./api";

export type StreamState = {
  id: string | null;
  lang: AskRequest["lang"];
  trace: TraceStep[];
  citations: Citation[];
  sentences: (AnswerSentence & { done: boolean; verified: boolean })[];
  response: AskResponse | null; // set by `done` — the final, authoritative answer
  error: string | null;
};

export const emptyStreamState = (): StreamState => ({
  id: null,
  lang: null,
  trace: [],
  citations: [],
  sentences: [],
  response: null,
  error: null,
});

/** Pure reducer: state + event → new state. */
export function applyStreamEvent(s: StreamState, ev: StreamEvent): StreamState {
  switch (ev.type) {
    case "start":
      return { ...s, id: ev.id, lang: ev.lang };
    case "trace":
      return { ...s, trace: [...s.trace, ev.step] };
    case "citation":
      return { ...s, citations: [...s.citations, ev.citation] };
    case "delta": {
      const sentences = [...s.sentences];
      const cur = sentences[ev.index] ?? { text: "", cites: [], done: false, verified: false };
      sentences[ev.index] = { ...cur, text: cur.text + ev.text };
      return { ...s, sentences };
    }
    case "sentence": {
      const sentences = [...s.sentences];
      sentences[ev.index] = { ...ev.sentence, done: true, verified: ev.verified };
      return { ...s, sentences };
    }
    case "done": {
      const r = ev.response;
      return {
        ...s,
        id: r.id,
        lang: r.lang,
        trace: r.trace,
        citations: r.citations,
        sentences: r.sentences.map((x) => ({ ...x, done: true, verified: r.meta.verified })),
        response: r,
      };
    }
    case "error":
      return { ...s, error: ev.message };
  }
}

/** Parses an SSE byte stream into events. Exported for tests. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<StreamEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
    let sep: number;
    while ((sep = buf.indexOf("\n\n")) !== -1) {
      const raw = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      const data = raw
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).trimStart())
        .join("\n");
      if (data) yield JSON.parse(data) as StreamEvent;
    }
  }
}

/** Replays a mock answer as a realistic stream (used when NEXT_PUBLIC_API_MOCK=1). */
export async function* mockStream(question: string): AsyncGenerator<StreamEvent> {
  const r = mockAnswer(question);
  const wait = (ms: number) => new Promise((res) => setTimeout(res, ms));
  yield { type: "start", id: r.id, lang: r.lang };
  for (const step of r.trace) {
    await wait(350);
    yield { type: "trace", step };
  }
  const sent = new Set<string>();
  for (const [index, sentence] of r.sentences.entries()) {
    for (const cid of sentence.cites) {
      const c = r.citations.find((x) => x.id === cid);
      if (c && !sent.has(cid)) {
        sent.add(cid);
        yield { type: "citation", citation: c };
      }
    }
    for (const word of sentence.text.split(" ")) {
      await wait(35);
      yield { type: "delta", index, text: word + " " };
    }
    yield { type: "sentence", index, sentence, verified: r.meta.verified };
  }
  for (const c of r.citations) {
    if (!sent.has(c.id)) yield { type: "citation", citation: c }; // e.g. cited only by checklist steps
  }
  yield { type: "done", response: r };
}

export async function askStream(
  req: AskRequest,
  onUpdate: (s: StreamState) => void,
  signal?: AbortSignal,
): Promise<StreamState> {
  let state = emptyStreamState();
  const push = (ev: StreamEvent) => {
    state = applyStreamEvent(state, ev);
    onUpdate(state);
  };
  try {
    if (API_MOCK) {
      for await (const ev of mockStream(req.question)) {
        if (signal?.aborted) break;
        push(ev);
      }
      return state;
    }
    const res = await fetch(`${API_URL}/api/ask/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(req),
      signal,
    });
    await checked(res);
    if (!res.body) throw new Error("empty stream body");
    for await (const ev of parseSse(res.body)) push(ev);
  } catch (e) {
    if (signal?.aborted) return state;
    if (e instanceof ApiRequestError) push({ type: "error", code: e.body.error, message: e.body.message });
    else push({ type: "error", code: "unavailable", message: e instanceof Error ? e.message : String(e) });
  }
  return state;
}
