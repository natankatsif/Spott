// Local chat history: kept only in this browser (localStorage), never sent to or stored on the backend.
// An index of chats (id, title, dates) under one key, each chat's messages under its own key, so saving one chat
// doesn't rewrite the others. At most MAX_CHATS; when the browser runs out of space the oldest chats go first.

import { useSyncExternalStore } from "react";
import type { ChatMessage } from "./chat-transport";

export type ChatMeta = { id: string; title: string; createdAt: number; updatedAt: number };

const INDEX_KEY = "chatHistory.v1";
const CHAT_KEY = (id: string) => `chatHistory.v1:${id}`;
const MAX_CHATS = 30;
const TITLE_MAX = 90;

const listeners = new Set<() => void>();
let cache: { raw: string | null; list: ChatMeta[] } = { raw: null, list: [] };

function readIndex(): ChatMeta[] {
  if (typeof window === "undefined") return [];
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(INDEX_KEY);
  } catch {
    return [];
  }
  if (raw === cache.raw) return cache.list;
  let list: ChatMeta[] = [];
  try {
    list = raw ? (JSON.parse(raw) as ChatMeta[]) : [];
  } catch {
    list = [];
  }
  cache = { raw, list: list.toSorted((a, b) => b.updatedAt - a.updatedAt) };
  return cache.list;
}

function writeIndex(list: ChatMeta[]): void {
  window.localStorage.setItem(INDEX_KEY, JSON.stringify(list));
  listeners.forEach((l) => l());
}

const textOf = (m: ChatMessage) => m.parts.map((p) => (p.type === "text" ? p.text : "")).join(" ").trim();

/** Saves (or updates) a chat; its title is the first question. Quietly drops the oldest chats if space runs out. */
export function saveChat(id: string, messages: ChatMessage[]): void {
  if (typeof window === "undefined" || messages.length === 0) return;
  const now = Date.now();
  const list = readIndex().filter((c) => c.id !== id);
  const existing = readIndex().find((c) => c.id === id);
  const first = messages.find((m) => m.role === "user");
  const title = (first ? textOf(first) : "").replace(/\s+/g, " ").slice(0, TITLE_MAX);
  const meta: ChatMeta = { id, title, createdAt: existing?.createdAt ?? now, updatedAt: now };
  let next = [meta, ...list];
  for (const old of next.slice(MAX_CHATS)) removeKey(CHAT_KEY(old.id));
  next = next.slice(0, MAX_CHATS);

  const body = JSON.stringify(messages);
  // out of space: drop the oldest other chats until it fits (or nothing is left to drop)
  for (;;) {
    try {
      window.localStorage.setItem(CHAT_KEY(id), body);
      writeIndex(next);
      return;
    } catch {
      const victim = next.findLast((c) => c.id !== id);
      if (!victim) return; // this one chat alone doesn't fit: keep what was there
      removeKey(CHAT_KEY(victim.id));
      next = next.filter((c) => c.id !== victim.id);
    }
  }
}

export function loadChat(id: string): ChatMessage[] | null {
  try {
    const raw = window.localStorage.getItem(CHAT_KEY(id));
    return raw ? (JSON.parse(raw) as ChatMessage[]) : null;
  } catch {
    return null;
  }
}

export function deleteChat(id: string): void {
  removeKey(CHAT_KEY(id));
  try {
    writeIndex(readIndex().filter((c) => c.id !== id));
  } catch {
    /* storage blocked */
  }
}

export function clearChats(): void {
  for (const c of readIndex()) removeKey(CHAT_KEY(c.id));
  removeKey(INDEX_KEY);
  listeners.forEach((l) => l());
}

function removeKey(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    /* storage blocked */
  }
}

/** The saved chats, newest first; re-renders on any change (and when another tab changes them). */
export function useChatHistory(): ChatMeta[] {
  return useSyncExternalStore(
    (l) => {
      listeners.add(l);
      const onStorage = (e: StorageEvent) => e.key === INDEX_KEY && l();
      window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(l);
        window.removeEventListener("storage", onStorage);
      };
    },
    readIndex,
    () => EMPTY,
  );
}
const EMPTY: ChatMeta[] = [];

export const newChatId = () =>
  typeof crypto !== "undefined" && "randomUUID" in crypto ? crypto.randomUUID() : `c-${Date.now()}-${Math.random().toString(36).slice(2)}`;
