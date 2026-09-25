// Mirrors backend/app/schemas.py.

export type Lang = "ro" | "ru";

export type Citation = {
  document_title: string;
  url: string;
  passage: string;
  location: string | null;
  page: number | null;
  published: string | null;
};

export type NavLink = {
  title: string;
  url: string;
};

export type AskResponse = {
  status: "answered" | "not_found" | "conflict";
  lang: Lang;
  answer: string;
  citations: Citation[];
  nav_links: NavLink[];
};

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function ask(question: string, lang: Lang): Promise<AskResponse> {
  const res = await fetch(`${API_URL}/api/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, lang }),
  });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
