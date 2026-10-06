"""Every command line the job worker and tools.pipeline build for a stage goes through that stage's own parser.

The orchestrators only build lists of strings, and a stage reads them in a child process: a flag renamed in a stage
and not in its caller would fail every such job at runtime (argparse exits with code 2). Here it fails a test.
"""

import importlib

import pytest

from spott.ingest.tools import pipeline
from spott.ingest.worker import core

SITE = {"id": 7, "kind": "site", "site_id": "acc.md", "url": "https://acc.md/", "max_depth": 2, "max_pages": 50}
DOCUMENT = {"id": 8, "kind": "document", "site_id": "dgaurf.md", "url": "https://dgaurf.md/storage/a.pdf"}
SITES = ["acc.md", "dgaurf.md"]


def worker_commands() -> list[list[str]]:
    # Every branch of plan(): each job kind, for a site (with and without its own limits), a document and no source
    # (the worker passes all sites then), a whole source, a deeper path and a document link added into it.
    sources = [SITE, SITE | {"max_depth": None, "max_pages": None}, DOCUMENT, None]
    urls = [None, "https://acc.md/ro/servicii", "https://acc.md/files/tarife.pdf"]
    return [step.args for kind in ("crawl", "refresh", "check", "backlog") for source in sources for url in urls
            for step in core.plan({"id": 1, "kind": kind, "url": url}, source, SITES)]


def pipeline_commands() -> list[list[str]]:
    return [args for mode in ("update", "full") for max_depth in (None, 3)
            for _, args in pipeline.plan(mode, SITES, max_depth)]


COMMANDS = sorted({tuple(args) for args in worker_commands() + pipeline_commands()})


def stage_parser(module: str):
    """parse_args of what `python -m <module>` runs: a package's __main__, or the module itself."""
    stage = importlib.import_module(module)
    if hasattr(stage, "__path__"):
        stage = importlib.import_module(f"{module}.__main__")
    return stage.parse_args


def test_the_commands_reach_every_stage():
    # plan() picks the stages by job kind and source: a stage these inputs stopped reaching would go unchecked
    assert {args[1] for args in COMMANDS} == {f"spott.ingest.{s}" for s in pipeline.STAGES} | {
        "spott.ingest.freshness", "spott.ingest.worker.register"}


@pytest.mark.parametrize("command", COMMANDS, ids=lambda command: " ".join(command[1:]))
def test_the_stage_accepts_its_command_line(command, capsys):
    flag, module, *argv = command
    assert flag == "-m"
    try:
        stage_parser(module)(argv)
    except SystemExit:
        error = capsys.readouterr().err.strip().splitlines()[-1]  # after the usage, e.g. "unrecognized arguments: …"
        pytest.fail(f"python -m {module} {' '.join(argv)}\n{error}")
