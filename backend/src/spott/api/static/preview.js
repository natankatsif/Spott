// Source preview: finds the quoted lines on the page, highlights them, scrolls to them (docs/history/tasks/11).
// Runs inside our own preview page (GET /api/preview/{doc_id}), never on the city hall site.
//
// Protocol with the chat (parent frame / opener):
//   → {type: "src-preview:ready", doc_id, found: "exact" | "words" | "start" | "none", kind}
//   ← {type: "src-preview:highlight", line_ids: [...]}   re-highlight without reloading, then "ready" again
(() => {
  "use strict";
  const D = JSON.parse(document.getElementById("src-preview-data").textContent);
  const TIERS = ["exact", "words", "start", "none"];

  // ─── text normalization: the same on both sides ───
  const CHAR_MAP = {
    "ş": "ș", "Ş": "Ș", "ţ": "ț", "Ţ": "Ț",
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"', "«": '"', "»": '"', "″": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
  };
  const DROP = /[­​‌‍⁠﻿]/; // soft hyphen, zero-width
  function normChar(ch) {
    if (DROP.test(ch)) return "";
    if (ch === "|") return " "; // our table rows join cells with "|"; on the page the cells are separate blocks
    const n = ch.normalize("NFKC");
    let out = "";
    for (const c of n) out += CHAR_MAP[c] || c;
    return /\s/.test(out) ? " " : out.toLowerCase();
  }
  function normalize(s) {
    let out = "";
    for (const ch of s) {
      const c = normChar(ch);
      if (c === " " && (out === "" || out.endsWith(" "))) continue;
      out += c;
    }
    return out.trim();
  }

  // ─── the page's text as one normalized string, each character mapped back to (text node, offset) ───
  const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "SVG", "TEXTAREA", "SELECT", "OPTION"]);
  const BLOCK = /^(ADDRESS|ARTICLE|ASIDE|BLOCKQUOTE|BR|DD|DIV|DL|DT|FIGCAPTION|FIGURE|FOOTER|FORM|H[1-6]|HEADER|HR|LI|MAIN|NAV|OL|P|PRE|SECTION|TABLE|TBODY|TD|TFOOT|TH|THEAD|TR|UL)$/;
  function blockOf(node) {
    for (let el = node.parentElement; el; el = el.parentElement) if (BLOCK.test(el.tagName)) return el;
    return null;
  }
  function pageText(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode(n) {
        for (let el = n.parentElement; el; el = el.parentElement) {
          if (SKIP.has(el.tagName) || el.id === "src-preview-banner") return NodeFilter.FILTER_REJECT;
        }
        // Only what the reader sees: a hidden copy of the text (a collapsed block, data for the site's scripts)
        // would "match" out of view.
        return n.parentElement && n.parentElement.getClientRects().length ? NodeFilter.FILTER_ACCEPT
          : NodeFilter.FILTER_REJECT;
      },
    });
    let text = "";
    const map = []; // map[i] = [node, offset] of text[i]
    let lastBlock = null;
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const block = blockOf(node);
      if (block !== lastBlock && text && !text.endsWith(" ")) {
        text += " "; // a block boundary is a word boundary
        map.push(map[map.length - 1]);
      }
      lastBlock = block;
      const s = node.nodeValue;
      let i = 0;
      for (const ch of s) {
        const c = normChar(ch);
        const offset = i;
        i += ch.length;
        if (!c || (c === " " && (text === "" || text.endsWith(" ")))) continue;
        for (const piece of c) {
          text += piece;
          map.push([node, offset]);
        }
      }
    }
    return { text, map, end: (k) => { const [n, o] = map[k]; return [n, o + 1]; } };
  }

  // ─── find a quote: exact, else the longest run of ≥ 8 words, else the first 12 words ───
  function find(index, quote) {
    const q = normalize(quote);
    if (!q) return null;
    let at = index.text.indexOf(q);
    if (at >= 0) return { tier: "exact", start: at, end: at + q.length };
    const words = q.split(" ");
    for (let len = words.length - 1; len >= 8; len--) {
      for (let s = 0; s + len <= words.length; s++) {
        const part = words.slice(s, s + len).join(" ");
        at = index.text.indexOf(part);
        if (at >= 0) return { tier: "words", start: at, end: at + part.length };
      }
    }
    const head = words.slice(0, Math.min(12, words.length)).join(" ");
    if (words.length > 3) {
      at = index.text.indexOf(head);
      if (at >= 0) return { tier: "start", start: at, end: at + head.length };
    }
    return null;
  }
  function toRange(index, m) {
    const r = document.createRange();
    const [sn, so] = index.map[m.start];
    const [en, eo] = index.end(m.end - 1);
    r.setStart(sn, so);
    r.setEnd(en, Math.min(eo, en.nodeValue.length));
    return r;
  }

  // ─── highlight ───
  const HAS_HIGHLIGHT_API = typeof CSS !== "undefined" && CSS.highlights && typeof Highlight !== "undefined";
  function clear() {
    if (HAS_HIGHLIGHT_API) CSS.highlights.delete("src-quote");
    document.querySelectorAll("mark[data-src-quote]").forEach((m) => m.replaceWith(...m.childNodes));
    document.querySelectorAll(".src-line.src-on").forEach((el) => el.classList.remove("src-on"));
  }
  function markRange(range) { // fallback without the Custom Highlight API: wrap each text piece
    const nodes = [];
    const walker = document.createTreeWalker(range.commonAncestorContainer.nodeType === 3
      ? range.commonAncestorContainer.parentNode : range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    for (let n = walker.nextNode(); n; n = walker.nextNode()) if (range.intersectsNode(n)) nodes.push(n);
    let first = null;
    for (const n of nodes) {
      const s = n === range.startContainer ? range.startOffset : 0;
      const e = n === range.endContainer ? range.endOffset : n.nodeValue.length;
      if (e <= s) continue;
      const piece = n.splitText(s);
      piece.splitText(e - s);
      const mark = document.createElement("mark");
      mark.dataset.srcQuote = "";
      piece.replaceWith(mark);
      mark.appendChild(piece);
      first = first || mark;
    }
    return first;
  }
  function scrollTo(target) {
    if (!target) return;
    const rect = target.getBoundingClientRect();
    const banner = document.getElementById("src-preview-banner");
    const top = rect.top + window.scrollY - Math.max(0, (window.innerHeight - rect.height) / 2)
      - (banner ? banner.offsetHeight / 2 : 0);
    window.scrollTo({ top: Math.max(0, top), behavior: "instant" });
  }

  function highlightLines(lineIds) {
    clear();
    const ids = lineIds.filter((id) => D.lines[id]);
    if (D.mode === "text") { // our own text view: the lines are elements
      let first = null;
      for (const id of ids) {
        const el = document.getElementById("line-" + id);
        if (el) { el.classList.add("src-on"); first = first || el; }
      }
      scrollTo(first);
      return first ? "exact" : "none";
    }
    const index = pageText(document.body);
    let best = 3;
    const ranges = [];
    for (const id of ids) {
      const m = find(index, D.lines[id].text);
      if (!m) continue;
      best = Math.min(best, TIERS.indexOf(m.tier));
      ranges.push(toRange(index, m));
    }
    if (!ranges.length) return "none";
    let first = ranges[0];
    if (HAS_HIGHLIGHT_API) {
      CSS.highlights.set("src-quote", new Highlight(...ranges));
    } else {
      first = ranges.map(markRange).find(Boolean) || ranges[0];
    }
    scrollTo(first);
    return TIERS[best];
  }

  // ─── banner ───
  function banner(found) {
    const el = document.getElementById("src-preview-banner");
    if (!el) return;
    const note = el.querySelector(".src-note");
    if (note) {
      note.hidden = found !== "none";
      const q = D.selected.map((id) => D.lines[id] && D.lines[id].text).filter(Boolean).join(" … ");
      note.textContent = D.text.not_found.replace("{quote}", q.length > 300 ? q.slice(0, 299) + "…" : q);
    }
    const spacer = document.getElementById("src-preview-spacer");
    if (spacer) spacer.style.height = el.offsetHeight + 4 + "px";
  }
  const back = document.querySelector("[data-src-back]");
  if (back) back.addEventListener("click", (e) => {
    e.preventDefault();
    if (window.opener) window.close(); else history.back();
  });

  // ─── messages ───
  const allowed = D.allowed_origins;
  const any = allowed.includes("*");
  function send(found) {
    const msg = { type: "src-preview:ready", doc_id: D.doc_id, found, kind: D.kind };
    for (const target of [window.parent !== window ? window.parent : null, window.opener]) {
      if (!target) continue;
      for (const origin of any ? ["*"] : allowed) {
        try { target.postMessage(msg, origin); } catch (_) { /* a closed opener */ }
      }
    }
    document.documentElement.dataset.srcFound = found; // for tests and the curious
  }
  window.addEventListener("message", (e) => {
    if (!any && !allowed.includes(e.origin)) return;
    const m = e.data;
    if (!m || m.type !== "src-preview:highlight" || !Array.isArray(m.line_ids)) return;
    D.selected = m.line_ids.slice(0, 5);
    const found = highlightLines(D.selected);
    banner(found);
    send(found);
  });

  function run() {
    const found = highlightLines(D.selected);
    banner(found);
    send(found);
    return found;
  }
  const found = run();
  // Images and fonts move the text: scroll again once they are in (without re-reporting).
  if (document.readyState !== "complete") {
    window.addEventListener("load", () => { if (found !== "none") highlightLines(D.selected); }, { once: true });
  }
})();
