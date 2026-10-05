"""A parse the kernel killed (OOM, exit -9) must not be retried first by every next run."""

from spott.ingest.common.registry import Registry


def add_file(reg: Registry, sha: str, status: str) -> None:
    with reg.conn:
        reg.conn.execute("INSERT INTO files (sha256, path, size, extension, downloaded_at, parse_status) "
                         "VALUES (?, ?, 1, '.pdf', '2026-09-27T00:00:00', ?)", (sha, f"raw/{sha}.pdf", status))


def status(reg: Registry, sha: str) -> tuple[str, str | None]:
    return reg.conn.execute("SELECT parse_status, parse_error FROM files WHERE sha256 = ?", (sha,)).fetchone()


def test_the_file_a_killed_run_was_on_is_failed_and_the_rest_left_alone(tmp_path):
    reg = Registry(tmp_path / "registry.sqlite")
    add_file(reg, "killed", "parsing")
    add_file(reg, "next", "pending")
    add_file(reg, "done", "parsed")
    assert reg.fail_interrupted_parses() == 1
    assert status(reg, "killed")[0] == "failed" and "out of memory" in status(reg, "killed")[1]
    assert status(reg, "next")[0] == "pending" and status(reg, "done")[0] == "parsed"
    # the next run's queue starts past it
    assert [r["sha256"] for r in reg.files_to_parse(["pending"], None)] == ["next"]
    assert reg.fail_interrupted_parses() == 0
