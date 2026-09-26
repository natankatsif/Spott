"""qsearch — console search over the indexed corpus, for manual testing without LLM or frontend.

    uv run qsearch                     interactive mode (type :help)
    uv run qsearch "вопрос"            one-shot search
    uv run qsearch "вопрос" --json     one-shot, JSON output
    uv run qsearch --report            summary of the tester's marks

Every search and every mark (:ok N / :bad / :none / :note) is appended to
<repo>/data/test_logs/<date>.jsonl; marks reference the query by query_id.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.text import Text

console = Console(highlight=False)

HELP = """\
[bold]Поиск:[/] просто напиши вопрос (RO или RU) и нажми Enter.

[bold]Отметки (пишутся в лог):[/]
  :ok N          правильный ответ — в результате N
  :bad           правильного ответа нет в выдаче
  :none          информации в корпусе нет, и это правильно
  :note текст    комментарий к последнему вопросу

[bold]Инструменты:[/]
  :grep фраза            точный поиск фразы по строкам
  :toc N|doc_id          оглавление документа
  :open N|doc_id         открыть текст (N — кусок из результата N)
  :lang ro|ru|all        фильтр языка документов
  :k N                   сколько результатов показывать
  :help   :q             помощь / выход"""

MARKS = ("ok", "bad", "none")


# ── paths & log ─────────────────────────────────────────────────────────────


def repo_root(start: Path | None = None) -> Path:
    """Directory with .git above this file (or cwd); logs always land in <repo>/data/test_logs."""
    for base in (start or Path(__file__).resolve(), Path.cwd()):
        for p in (base, *base.parents):
            if (p / ".git").exists():
                return p
    return Path.cwd()


def log_dir() -> Path:
    return Path(os.getenv("QSEARCH_LOG_DIR") or repo_root() / "data" / "test_logs")


def append_log(entry: dict[str, Any], directory: Path | None = None) -> None:
    d = directory or log_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{datetime.now(UTC):%Y-%m-%d}.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")


def read_logs(directory: Path | None = None) -> list[dict[str, Any]]:
    d = directory or log_dir()
    entries: list[dict[str, Any]] = []
    for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
        entries += [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
    return entries


def git_rev() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=repo_root(), stderr=subprocess.DEVNULL, text=True
        ).strip()
    except OSError, subprocess.CalledProcessError:
        return "unknown"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── pure logic (tested) ─────────────────────────────────────────────────────


def parse_command(line: str) -> tuple[str, str]:
    """':ok 2' -> ('ok', '2'); plain text -> ('search', text)."""
    line = line.strip()
    if not line.startswith(":"):
        return "search", line
    name, _, arg = line[1:].partition(" ")
    return name.lower(), arg.strip()


def result_summary(rank: int, item: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": rank,
        "chunk_id": item.get("chunk_id"),
        "doc_id": item.get("doc_id"),
        "lang": item.get("lang"),
        "score": round(float(item.get("score") or 0.0), 4),
        "line_ids": [ml.get("line_id") for ml in item.get("matched_lines", [])],
        "url": item.get("url"),
    }


def summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregates the log: the last mark per query wins; notes are attached to their query."""
    queries = {e["query_id"]: e for e in entries if e.get("type") == "query" and e.get("query_id")}
    marks: dict[str, dict[str, Any]] = {}
    notes: dict[str, list[str]] = {}
    for e in entries:
        qid = e.get("query_id")
        if qid not in queries:
            continue
        if e.get("type") in MARKS:
            marks[qid] = e
        elif e.get("type") == "note":
            notes.setdefault(qid, []).append(e.get("note", ""))
    counts = {m: sum(1 for e in marks.values() if e["type"] == m) for m in MARKS}
    ok_ranks = [e["rank"] for e in marks.values() if e["type"] == "ok" and e.get("rank")]
    bad = [
        {"query": queries[qid]["query"], "notes": notes.get(qid, [])} for qid, e in marks.items() if e["type"] == "bad"
    ]
    return {
        "queries": len(queries),
        "marked": len(marks),
        "unmarked": len(queries) - len(marks),
        **counts,
        "ok_pct": counts["ok"] / len(marks) if marks else 0.0,
        "avg_ok_rank": sum(ok_ranks) / len(ok_ranks) if ok_ranks else 0.0,
        "bad_list": bad,
    }


def resolve_doc(token: str, results: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """'2' -> (doc_id, chunk_id) of result 2; anything else is taken as a doc_id."""
    if token.isdigit():
        n = int(token)
        if 1 <= n <= len(results):
            return results[n - 1].get("doc_id"), results[n - 1].get("chunk_id")
        return None, None
    return (token or None), None


# ── rendering ───────────────────────────────────────────────────────────────


def highlighted(text: str, query: str) -> Text:
    t = Text(text)
    words = [w.strip(".,;:!?«»\"'()") for w in query.split()]
    t.highlight_words([w for w in words if len(w) >= 4], style="bold yellow", case_sensitive=False)
    return t


def fetch_context(pool: Any, chunk_id: str, idx: int) -> list[dict[str, Any]]:
    """Line before and after the matched one, from the same chunk."""
    from psycopg.rows import dict_row

    from .pipeline import acquire_conn

    with acquire_conn(pool) as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT idx, text FROM lines WHERE chunk_id = %s AND idx BETWEEN %s AND %s ORDER BY idx",
            (chunk_id, idx - 1, idx + 1),
        )
        return cur.fetchall()


def print_result(n: int, item: dict[str, Any], query: str, pool: Any) -> None:
    pages = item.get("pages") or []
    head = Text.assemble(
        (f"[{n}] ", "bold"),
        (f"{float(item.get('score') or 0):.4f}  ", "cyan"),
        (f"{(item.get('lang') or '?').upper()}  ", "magenta"),
        (f"{item.get('site') or ''}  ", "dim"),
        item.get("citation_label") or item.get("doc_id") or "",
        (f"  p.{pages[0]}" if pages else "", "dim"),
    )
    console.print(head)
    lines = item.get("matched_lines") or []
    if lines:
        best = lines[0]
        try:
            ctx = fetch_context(pool, item["chunk_id"], best["idx"])
        except Exception:  # context is a convenience; never break the search on it
            ctx = [best]
        for row in ctx:
            if row["idx"] == best["idx"]:
                console.print(Text("   ▸ ", style="green") + highlighted(row["text"], query))
            else:
                console.print(Text("     " + row["text"], style="dim"))
        link = best.get("deep_link") or item.get("url")
        if link:
            console.print(Text("     " + link, style="dim underline"))
    console.print()


# ── session ─────────────────────────────────────────────────────────────────


class Session:
    def __init__(self, pool: Any, *, lang: str | None, k: int) -> None:
        self.pool = pool
        self.lang = lang
        self.k = k
        self.rev = git_rev()
        self.query_id: str | None = None
        self.query = ""
        self.results: list[dict[str, Any]] = []

    def search(self, query: str) -> None:
        from .pipeline import retrieve

        t0 = time.perf_counter()
        res = retrieve(self.pool, query, lang=self.lang, k=self.k, rerank=False)
        ms = (time.perf_counter() - t0) * 1000
        self.query_id, self.query, self.results = uuid.uuid4().hex[:12], query, res.items
        console.print()
        if not res.items:
            console.print("[dim]Ничего не найдено[/]")
        for n, item in enumerate(res.items, 1):
            print_result(n, item, query, self.pool)
        console.print(f"[dim]⏱ {ms:.0f} мс · отметь: :ok N / :bad / :none[/]\n")
        append_log(
            {
                "type": "query",
                "ts": now(),
                "query_id": self.query_id,
                "query": query,
                "lang_filter": self.lang,
                "k": self.k,
                "elapsed_ms": round(ms, 1),
                "rev": self.rev,
                "results": [result_summary(n, it) for n, it in enumerate(res.items, 1)],
            }
        )

    def mark(self, kind: str, arg: str) -> None:
        if not self.query_id:
            console.print("[red]Сначала задай вопрос[/]")
            return
        entry: dict[str, Any] = {
            "type": kind,
            "ts": now(),
            "query_id": self.query_id,
            "query": self.query,
            "rev": self.rev,
        }
        if kind == "ok":
            if not arg.isdigit() or not 1 <= int(arg) <= len(self.results):
                console.print(f"[red]Нужно :ok N, где N от 1 до {len(self.results)}[/]")
                return
            item = self.results[int(arg) - 1]
            entry.update(
                rank=int(arg),
                chunk_id=item.get("chunk_id"),
                doc_id=item.get("doc_id"),
                line_ids=[ml.get("line_id") for ml in item.get("matched_lines", [])],
            )
        if kind == "note":
            if not arg:
                console.print("[red]Нужно :note текст[/]")
                return
            entry["note"] = arg
        append_log(entry)
        console.print(f"[green]✓ записано ({kind})[/]")

    def grep(self, phrase: str) -> None:
        from .tools import grep_tool

        out = grep_tool(self.pool, phrase, limit=10)
        console.print(f"\n[bold]grep «{escape(phrase)}»: {out['total']}[/]")
        for ln in out["lines"]:
            console.print(Text(f"  {ln['doc_id']}  p.{ln.get('page') or '-'}", style="dim"))
            console.print(Text("    ") + highlighted(ln["text"], phrase))
        console.print()

    def toc(self, token: str) -> None:
        from .tools import toc_tool

        doc_id, _ = resolve_doc(token, self.results)
        if not doc_id:
            console.print("[red]Нужно :toc N или :toc doc_id[/]")
            return
        out = toc_tool(self.pool, doc_id)
        console.print(f"\n[bold]{escape(doc_id)}[/] — {out['total_nodes']} разделов")
        for i, node in enumerate(out["nodes"], 1):
            console.print(Text(f"  {i:3d}. {node['title']}  ({node['line_count']} строк)"))
        console.print()

    def open(self, token: str) -> None:
        from .tools import open_tool

        doc_id, chunk_id = resolve_doc(token, self.results)
        if not doc_id:
            console.print("[red]Нужно :open N или :open doc_id[/]")
            return
        out = open_tool(self.pool, doc_id, chunk_id=chunk_id, max_lines=40)
        console.print(f"\n[bold]{escape(doc_id)}[/] — {out['total_lines']} строк")
        for ln in out["lines"]:
            console.print(Text(f"  {ln['idx']:4d}  ", style="dim") + Text(ln["text"]))
        console.print()

    def handle(self, line: str) -> bool:
        """Returns False to quit."""
        cmd, arg = parse_command(line)
        if cmd == "search":
            if arg:
                self.search(arg)
        elif cmd in ("q", "quit", "exit"):
            return False
        elif cmd == "help":
            console.print(HELP)
        elif cmd in (*MARKS, "note"):
            self.mark(cmd, arg)
        elif cmd == "grep" and arg:
            self.grep(arg)
        elif cmd == "toc":
            self.toc(arg)
        elif cmd == "open":
            self.open(arg)
        elif cmd == "lang" and arg in ("ro", "ru", "all"):
            self.lang = None if arg == "all" else arg
            console.print(f"[green]язык: {arg}[/]")
        elif cmd == "k" and arg.isdigit() and int(arg) > 0:
            self.k = int(arg)
            console.print(f"[green]результатов: {self.k}[/]")
        else:
            console.print("[red]Не понял команду. :help — список[/]")
        return True


# ── entry points ────────────────────────────────────────────────────────────


def print_report() -> None:
    s = summarize(read_logs())
    if not s["queries"]:
        console.print(f"Логов пока нет ({log_dir()})")
        return
    console.print(f"\n[bold]Отчёт qsearch[/]  ({log_dir()})")
    console.print(f"  вопросов:            {s['queries']}  (без отметки: {s['unmarked']})")
    console.print(f"  ok:                  {s['ok']}  ({s['ok_pct']:.0%} от отмеченных)")
    console.print(f"  bad:                 {s['bad']}")
    console.print(f"  none:                {s['none']}")
    console.print(f"  средний ранг ok:     {s['avg_ok_rank']:.1f}")
    if s["bad_list"]:
        console.print("\n[bold]bad — на разбор:[/]")
        for b in s["bad_list"]:
            console.print(Text(f"  • {b['query']}" + (f"  — {'; '.join(b['notes'])}" if b["notes"] else "")))
    console.print()


def one_shot(pool: Any, query: str, *, lang: str | None, k: int, as_json: bool) -> None:
    from .pipeline import retrieve

    res = retrieve(pool, query, lang=lang, k=k, rerank=False)
    if as_json:
        out = {
            "query": query,
            "timings_ms": res.timings_ms,
            "results": [
                {
                    **result_summary(n, it),
                    "citation_label": it.get("citation_label"),
                    "site": it.get("site"),
                    "matched_lines": [
                        {"text": ml.get("text"), "deep_link": ml.get("deep_link")} for ml in it.get("matched_lines", [])
                    ],
                }
                for n, it in enumerate(res.items, 1)
            ],
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    for n, item in enumerate(res.items, 1):
        print_result(n, item, query, pool)
    console.print(f"[dim]⏱ {res.timings_ms.get('total', 0):.0f} мс[/]")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="qsearch", description="Консольный поиск по корпусу (для тестирования)")
    p.add_argument("query", nargs="?", help="вопрос для разового поиска")
    p.add_argument("--json", action="store_true", help="вывод в JSON (разовый режим)")
    p.add_argument("--lang", choices=["ro", "ru", "all"], default="all", help="фильтр языка документов")
    p.add_argument("-k", "--k", type=int, default=5, help="сколько результатов (по умолчанию 5)")
    p.add_argument("--report", action="store_true", help="сводка по отметкам тестировщика")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252/cp866
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    if args.report:
        print_report()
        return

    from .db import get_pool
    from .embeddings import get_device, get_embedding_model

    lang = None if args.lang == "all" else args.lang
    try:
        pool = get_pool(min_size=1, max_size=4)
        pool.wait(timeout=10)
    except Exception as e:
        console.print(
            f"[red]Нет соединения с базой: {escape(str(e))}[/]\n"
            "Запусти Docker Desktop и `docker compose up -d`, затем проверь `.env`."
        )
        sys.exit(1)

    try:
        if args.query:
            one_shot(pool, args.query, lang=lang, k=args.k, as_json=args.json)
            return
        with console.status("Загружаю модель поиска (первый раз до минуты)…"):
            device = get_device()
            get_embedding_model(device)
        console.print(f"[green]Готово[/] · устройство {device} · ревизия {git_rev()} · логи: {log_dir()}")
        console.print("Напиши вопрос. [dim]:help — команды, :q — выход[/]\n")
        session = Session(pool, lang=lang, k=args.k)
        while True:
            try:
                line = input("❯ ")
            except EOFError, KeyboardInterrupt:
                console.print()
                break
            try:
                if not session.handle(line):
                    break
            except Exception as e:  # keep the REPL alive on DB/tool errors
                console.print(f"[red]Ошибка: {escape(str(e))}[/]")
    finally:
        pool.close()


if __name__ == "__main__":
    main()
