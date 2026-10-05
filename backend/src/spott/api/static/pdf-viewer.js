// PDF source preview (docs/history/tasks/11): the cited page first, the others as they scroll into view; the citation's
// boxes drawn over the page, the line's text highlighted in pdf.js's text layer when the page has one (a scanned
// page has none: the box is the highlight). Same postMessage protocol as preview.js.
import * as pdfjsLib from "./pdfjs/pdf.min.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = new URL("./pdfjs/pdf.worker.min.mjs", import.meta.url).href;

const D = JSON.parse(document.getElementById("src-preview-data").textContent);
const TIERS = ["exact", "words", "start", "none"];
const container = document.getElementById("pages");
const pages = new Map(); // page number → {el, rendered, textLayer, viewport}

const CHAR_MAP = { "ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț", "’": "'", "‘": "'", "“": '"',
  "”": '"', "„": '"', "«": '"', "»": '"', "–": "-", "—": "-" };
const normalize = (s) => [...s.normalize("NFKC")].map((c) => CHAR_MAP[c] || c).join("")
  .replace(/[­​-‍﻿]/g, "").replace(/\s+/g, " ").trim().toLowerCase();

function pageWidth() {
  return Math.min(container.clientWidth - 16, 1100);
}

function placeholders(count) {
  const sizes = new Map((D.page_sizes || []).map((p) => [p.n, p]));
  for (let n = 1; n <= count; n++) {
    const size = sizes.get(n) || { width: 595, height: 842 };
    const el = document.createElement("div");
    el.className = "page";
    el.dataset.page = n;
    el.style.width = pageWidth() + "px";
    el.style.height = Math.round(pageWidth() * size.height / size.width) + "px";
    container.appendChild(el);
    pages.set(n, { el, rendered: null, textLayer: null, viewport: null });
  }
}

async function render(pdf, n) {
  const p = pages.get(n);
  if (!p || p.rendered) return p && p.rendered;
  p.rendered = (async () => {
    const page = await pdf.getPage(n);
    const base = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: pageWidth() / base.width });
    p.viewport = viewport;
    p.el.style.height = Math.round(viewport.height) + "px";
    p.el.style.setProperty("--scale-factor", viewport.scale);
    const canvas = document.createElement("canvas");
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.floor(viewport.width * ratio);
    canvas.height = Math.floor(viewport.height * ratio);
    canvas.style.width = viewport.width + "px";
    canvas.style.height = viewport.height + "px";
    p.el.appendChild(canvas);
    await page.render({ canvas, viewport, transform: ratio !== 1 ? [ratio, 0, 0, ratio, 0, 0] : null }).promise;
    const layer = document.createElement("div");
    layer.className = "textLayer";
    p.el.appendChild(layer);
    try {
      const text = new pdfjsLib.TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport });
      await text.render();
      p.textLayer = layer;
    } catch (_) { /* no text layer: a scanned page */ }
  })();
  return p.rendered;
}

function clear() {
  document.querySelectorAll(".src-box").forEach((b) => b.remove());
  document.querySelectorAll(".textLayer .src-on").forEach((s) => s.classList.remove("src-on"));
}

function drawBoxes(lines) {
  let first = null;
  for (const line of lines) {
    for (const b of line.bboxes || []) {
      const p = pages.get(b.page);
      if (!p) continue;
      const scale = pageWidth() / b.page_width;
      const box = document.createElement("div");
      box.className = "src-box";
      Object.assign(box.style, { left: b.l * scale + "px", top: b.t * scale + "px",
        width: (b.r - b.l) * scale + "px", height: (b.b - b.t) * scale + "px" });
      p.el.appendChild(box);
      first = first || box;
    }
  }
  return first;
}

function markText(p, quote) { // the line in the text layer: spans whose joined text contains it
  if (!p || !p.textLayer) return null;
  const spans = [...p.textLayer.querySelectorAll("span")];
  let joined = "";
  const owners = [];
  for (const s of spans) {
    const t = normalize(s.textContent) + " ";
    joined += t;
    for (let i = 0; i < t.length; i++) owners.push(s);
  }
  const q = normalize(quote);
  const words = q.split(" ");
  const tries = [["exact", q]];
  for (let len = words.length - 1; len >= 8; len--) {
    for (let s = 0; s + len <= words.length; s++) tries.push(["words", words.slice(s, s + len).join(" ")]);
  }
  if (words.length > 3) tries.push(["start", words.slice(0, 12).join(" ")]);
  for (const [tier, text] of tries) {
    const at = joined.indexOf(text);
    if (at < 0) continue;
    const hit = new Set(owners.slice(at, at + text.length));
    hit.forEach((s) => s.classList.add("src-on"));
    return { tier, first: [...hit][0] };
  }
  return null;
}

function scrollTo(el) {
  if (!el) return;
  const rect = el.getBoundingClientRect();
  window.scrollTo({ top: Math.max(0, rect.top + window.scrollY - window.innerHeight / 3), behavior: "instant" });
}

async function highlight(pdf, lineIds) {
  clear();
  const lines = lineIds.map((id) => D.lines[id]).filter(Boolean);
  const wanted = [...new Set(lines.map((l) => l.page).filter(Boolean))];
  await Promise.all(wanted.map((n) => render(pdf, n)));
  const box = drawBoxes(lines);
  let best = 3;
  let target = box;
  for (const line of lines) {
    const m = markText(pages.get(line.page), line.text);
    if (m) {
      best = Math.min(best, TIERS.indexOf(m.tier));
      target = target || m.first;
    }
  }
  scrollTo(target || (wanted.length && pages.get(wanted[0]).el));
  // A box is our exact location of the line even when the page has no text layer (a scan).
  return best < 3 ? TIERS[best] : box ? "exact" : "none";
}

// ─── messages and banner, as in preview.js ───
const allowed = D.allowed_origins;
const any = allowed.includes("*");
function send(found) {
  const msg = { type: "src-preview:ready", doc_id: D.doc_id, found, kind: D.kind };
  for (const target of [window.parent !== window ? window.parent : null, window.opener]) {
    if (!target) continue;
    for (const origin of any ? ["*"] : allowed) {
      try { target.postMessage(msg, origin); } catch (_) { /* closed */ }
    }
  }
  document.documentElement.dataset.srcFound = found;
}
function banner(found) {
  const note = document.querySelector("#src-preview-banner .src-note");
  if (!note) return;
  note.hidden = found !== "none";
  const q = D.selected.map((id) => D.lines[id] && D.lines[id].text).filter(Boolean).join(" … ");
  note.textContent = D.text.not_found.replace("{quote}", q.length > 300 ? q.slice(0, 299) + "…" : q);
  const bar = document.getElementById("src-preview-banner");
  const spacer = document.getElementById("src-preview-spacer");
  if (bar && spacer) spacer.style.height = bar.offsetHeight + 4 + "px";
}
const back = document.querySelector("[data-src-back]");
if (back) back.addEventListener("click", (e) => {
  e.preventDefault();
  if (window.opener) window.close(); else history.back();
});

async function main() {
  const status = document.getElementById("pdf-status");
  let pdf;
  try {
    const source = D.file_data ? { data: Uint8Array.from(atob(D.file_data), (c) => c.charCodeAt(0)) } : { url: D.file_url };
    pdf = await pdfjsLib.getDocument({
      ...source,
      // Scans are mostly JPEG 2000 / JBIG2 images: pdf.js decodes them with these WebAssembly modules.
      wasmUrl: new URL("./pdfjs/wasm/", import.meta.url).href,
      standardFontDataUrl: new URL("./pdfjs/standard_fonts/", import.meta.url).href,
    }).promise;
  } catch (e) {
    status.textContent = D.text.pdf_failed;
    status.hidden = false;
    banner("none");
    send("none");
    return;
  }
  status.hidden = true;
  placeholders(pdf.numPages);
  const found = await highlight(pdf, D.selected);
  banner(found);
  send(found);
  // The other pages as they come into view.
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) render(pdf, Number(e.target.dataset.page));
  }, { rootMargin: "800px 0px" });
  pages.forEach((p) => io.observe(p.el));
  window.addEventListener("message", async (e) => {
    if (!any && !allowed.includes(e.origin)) return;
    const m = e.data;
    if (!m || m.type !== "src-preview:highlight" || !Array.isArray(m.line_ids)) return;
    D.selected = m.line_ids.slice(0, 5);
    const f = await highlight(pdf, D.selected);
    banner(f);
    send(f);
  });
}
main();
