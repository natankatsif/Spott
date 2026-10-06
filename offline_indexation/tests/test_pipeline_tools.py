"""tools/: the pipeline never plans a robots-forbidden site; plans stay in stage order."""

from tools.pipeline import EXCLUDED_SITES, STAGES, allowed_sites, main, plan


def test_allowed_sites_skip_robots_forbidden():
    sites = allowed_sites()
    assert sites and not EXCLUDED_SITES & set(sites)


def test_plans_follow_stage_order():
    for mode in ("update", "full"):
        names = [name for name, _ in plan(mode, ["a.md"], None)]
        assert names == [s for s in STAGES if s in names]


def test_update_plan_resumes_and_refreshes():
    args = dict(plan("update", ["a.md"], 2))
    assert "--resume" in args["crawler"] and args["crawler"][-2:] == ["--max-depth", "2"]
    assert "--refresh" in args["downloader"]


def test_forbidden_site_is_refused(capsys):
    import pytest

    with pytest.raises(SystemExit):
        main(["full", "--sites", "chisinau.md", "--dry-run"])
    assert "robots.txt" in capsys.readouterr().err
