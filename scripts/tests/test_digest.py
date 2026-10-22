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
    assert digest.totals(DATA) == {"critical": 1, "high": 3, "prs": 5, "stale_prs": 1, "issues": 7, "failures": 2}


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
