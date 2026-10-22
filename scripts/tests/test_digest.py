import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import digest  # noqa: E402


def _repo(name, crit=0, high=0, prs=0, stale=0, issues=0, fails=0):
    return {
        "repo": f"chodeus/{name}",
        "dependabot_open": crit + high,
        "dependabot_critical": crit,
        "dependabot_high": high,
        "open_prs": prs,
        "stale_prs": [{"number": n, "title": "t", "age": 20} for n in range(stale)],
        "open_issues": issues,
        "recent_failures": [{"name": "CI", "url": "u", "branch": "main"} for _ in range(fails)],
        "last_release": None,
    }


DATA = {"repos": [_repo("a", crit=1, high=2, prs=3, stale=1, issues=6, fails=2), _repo("b", high=1, prs=2, issues=1)]}


def test_totals_sum_every_repo():
    assert digest.totals(DATA) == {"critical": 1, "high": 3, "prs": 5, "stale_prs": 1, "issues": 7, "failures": 2, "unread": 0}


def test_summary_and_issue_show_the_same_counts():
    summary = digest.render_summary(DATA)
    issue = digest.render_markdown(DATA)
    assert "Critical vulns: 1 · 🟠 High: 3" in summary
    assert "Open PRs: 5 (1 stale)" in summary and "Open issues: 7" in summary
    assert "Recent workflow failures: 2" in summary
    for line in ("- **Critical vulns:** 1", "- **High vulns:** 3", "- **Open PRs:** 5", "- **Open issues:** 7", "- **Recent workflow failures:** 2"):
        assert line in issue


def test_summary_of_an_empty_portfolio_is_all_zero():
    assert digest.render_summary({"repos": []}).count(": 0") == 5


DEPENDABOT_OFF = '{"message":"Dependabot alerts are disabled for this repository.","status":"403"}'


def _fake_gh(failing, stdout=""):
    def run(cmd, **kwargs):
        path = cmd[2]
        if failing and failing in path:
            return subprocess.CompletedProcess(cmd, 1, stdout, "gh: HTTP 403")
        return subprocess.CompletedProcess(cmd, 0, "{}" if "/actions/runs" in path else "[]", "")
    return run


def test_a_failed_read_is_flagged_not_counted_as_clean(monkeypatch):
    monkeypatch.setattr(digest.subprocess, "run", _fake_gh("/dependabot/"))
    snap = digest.repo_snapshot("chodeus", "a")
    assert snap["unread"] == ["Dependabot alerts"]
    data = {"repos": [snap]}
    assert digest.render_summary(data).startswith("⚠️ Incomplete: 1 repo(s) could not be fully read")
    issue = digest.render_markdown(data)
    assert "- ⚠️ **Repos not fully read:** 1" in issue
    assert "- ⚠️ Could not read: Dependabot alerts" in issue


def test_a_full_read_carries_no_warning(monkeypatch):
    monkeypatch.setattr(digest.subprocess, "run", _fake_gh(None))
    data = {"repos": [digest.repo_snapshot("chodeus", "a")]}
    assert data["repos"][0]["unread"] == []
    assert "Incomplete" not in digest.render_summary(data)
    assert "not fully read" not in digest.render_markdown(data)


def test_dependabot_switched_off_is_a_setting_not_a_failed_read(monkeypatch):
    monkeypatch.setattr(digest.subprocess, "run", _fake_gh("/dependabot/", stdout=DEPENDABOT_OFF))
    snap = digest.repo_snapshot("chodeus", "a")
    assert (snap["unread"], snap["off"], snap["dependabot_open"]) == ([], ["Dependabot alerts"], 0)
    data = {"repos": [snap]}
    assert "Incomplete" not in digest.render_summary(data)
    assert "- Turned off for this repo: Dependabot alerts" in digest.render_markdown(data)
