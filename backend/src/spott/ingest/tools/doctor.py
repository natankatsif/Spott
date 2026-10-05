"""Environment check for Mac and Windows: prints ✅/⚠️/❌ with a hint, exits 1 if a required check fails.

    uv run python -m spott.ingest.tools.doctor        # full check
    uv run python -m spott.ingest.tools.doctor --ci   # no Docker, DB, OCR or network (GitHub Actions)
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable

from spott.core.paths import REPO_ROOT

from .common import utf8_console

OK, WARN, FAIL = "ok", "warn", "fail"
Result = tuple[str, str, str]  # status, message, hint


def check_python() -> Result:
    # requires-python is >=3.12 so that Intel Macs can stay on torch 2.2; the server and CI run 3.14
    v = sys.version_info
    if v >= (3, 14):
        return OK, f"Python {v.major}.{v.minor}.{v.micro}", ""
    if v >= (3, 12):
        return WARN, f"Python {v.major}.{v.minor} (работает; сервер и CI на 3.14)", (
            "как на сервере: `uv sync --python 3.14 --all-extras` (Intel Mac — только 3.12)")
    return FAIL, f"Python {v.major}.{v.minor} (нужен ≥ 3.12)", "`uv sync --python 3.14 --all-extras` поставит его сам"


def check_uv() -> Result:
    if shutil.which("uv"):
        return OK, subprocess.check_output(["uv", "--version"], text=True).strip(), ""
    return FAIL, "uv не найден", "https://docs.astral.sh/uv/getting-started/installation/"


def check_env_file() -> Result:
    if (REPO_ROOT / ".env").exists():
        return OK, ".env найден", ""
    return FAIL, ".env не найден", "Скопируй .env.example в .env (в корне репо)"


def check_docker() -> Result:
    if not shutil.which("docker"):
        return FAIL, "Docker не найден", "Поставь Docker Desktop: https://docs.docker.com/desktop/"
    try:
        subprocess.run(["docker", "info"], capture_output=True, check=True, timeout=15)
    except (subprocess.SubprocessError, OSError):
        return FAIL, "Docker не запущен", "Открой Docker Desktop и дождись статуса Running"
    return OK, "Docker запущен", ""


def check_database() -> Result:
    try:
        from spott.core.db import get_connection

        with get_connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension")
            exts = {r[0] for r in cur.fetchall()}
            missing = {"vector", "pg_trgm", "unaccent"} - exts
            if missing:
                return FAIL, f"В базе нет расширений: {', '.join(sorted(missing))}", "uv run python -m spott.ingest.tools.index_io import <dump>"
            cur.execute("SELECT (SELECT count(*) FROM documents), (SELECT count(*) FROM chunks), (SELECT count(*) FROM lines)")
            docs, chunks, lines = cur.fetchone()
    except Exception as e:
        return FAIL, f"База недоступна: {str(e).splitlines()[0]}", "docker compose up -d (в корне репо), проверь POSTGRES_* в .env"
    if not chunks:
        return FAIL, "База пустая", "Импортируй дамп: uv run python -m spott.ingest.tools.index_io import <файл.dump>"
    return OK, f"Индекс: {docs} документов, {chunks} кусков, {lines} строк", ""


def check_tesseract() -> Result:
    if sys.platform == "darwin":
        return OK, "OCR: Apple Vision (Tesseract на Mac не нужен)", ""
    cmd = os.getenv("TESSERACT_CMD") or "tesseract"
    try:
        out = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True, timeout=15)
    except OSError:
        return (WARN, "Tesseract не найден (нужен только для OCR сканов)",
                "Windows: https://github.com/UB-Mannheim/tesseract/wiki, отметь Romanian и Russian; путь в TESSERACT_CMD")
    langs = set((out.stdout + out.stderr).split())
    missing = {"ron", "rus"} - langs
    if missing:
        return WARN, f"Tesseract без языков: {', '.join(sorted(missing))}", "Переустанови, отметив Romanian и Russian"
    return OK, "Tesseract: ron + rus", ""


def check_disk() -> Result:
    free = shutil.disk_usage(REPO_ROOT).free / 1024**3
    if free >= 20:
        return OK, f"Диск: {free:.0f} ГБ свободно", ""
    return WARN, f"Диск: {free:.0f} ГБ свободно (лучше ≥ 20)", "Модели ~3 ГБ, зависимости ~3 ГБ, данные обхода до нескольких ГБ"


def check_device() -> Result:
    try:
        from spott.core.embeddings import get_device

        dev = get_device()
    except Exception as e:
        return FAIL, f"torch не импортируется: {e}", "uv sync --all-extras"
    if dev == "cpu":
        return WARN, "Модель поиска будет на CPU (медленнее, но работает)", ""
    return OK, f"Модель поиска на {dev}", ""


def check_network() -> Result:
    import httpx

    try:
        httpx.get("https://autosalubritate.md", timeout=15, follow_redirects=True)
    except httpx.HTTPError as e:
        return WARN, f"autosalubritate.md не отвечает: {type(e).__name__}", "Нужно только для обхода сайтов"
    return OK, "Сеть: autosalubritate.md отвечает", ""


def check_encoding() -> Result:
    enc = (sys.stdout.encoding or "").lower()
    if "utf" in enc:
        return OK, f"Кодировка консоли: {enc}", ""
    return WARN, f"Кодировка консоли: {enc}", "Windows: используй Windows Terminal / PowerShell 7"


def main(argv: list[str] | None = None) -> None:
    utf8_console()
    p = argparse.ArgumentParser(description="Проверка окружения")
    p.add_argument("--ci", action="store_true", help="без Docker, базы, OCR и сети")
    args = p.parse_args(argv)

    checks: list[Callable[[], Result]] = [check_python, check_uv, check_encoding, check_device, check_disk]
    if not args.ci:
        checks += [check_env_file, check_docker, check_database, check_tesseract, check_network]

    print(f"\nDoctor · {platform.system()} {platform.release()} · {REPO_ROOT}\n")
    failed = 0
    for check in checks:
        try:
            status, msg, hint = check()
        except Exception as e:
            status, msg, hint = FAIL, f"{check.__name__}: {e}", ""
        print(f"  {'✅' if status == OK else '⚠️ ' if status == WARN else '❌'} {msg}")
        if hint and status != OK:
            print(f"     → {hint}")
        failed += status == FAIL
    print(f"\n{'Всё готово.' if not failed else f'Ошибок: {failed}. Исправь пункты с ❌ и запусти снова.'}\n")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
