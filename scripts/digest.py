#!/usr/bin/env python3
"""Generate a weekly portfolio digest for chodeus-ops.

Queries GitHub via `gh` CLI for each repo and emits a consolidated Markdown
report: open Dependabot alerts, failing workflows, open PRs, stale issues,
last release age.

Usage:
  digest.py --owner chodeus --repos repo1,repo2 [--format markdown|github-issue|json] [--summary-out FILE]

Requires `gh` CLI authenticated with `security_events:read` + `repo` scopes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import subprocess
import sys
from typing import Any


# gh_api's answer when GitHub says the feature is switched off for the repo: a setting, not a failed read.
DISABLED = object()


def gh_api(path: str, paginate: bool = True) -> Any:
    """Call `gh api <path>` and return parsed JSON, or None on failure.

    `paginate=False` honors any ?per_page= cap in the path. Use it for bounded
    queries (e.g. ?per_page=20) — otherwise --paginate ignores the cap and
    walks the full result set, which can time out on repos with deep history.
    """
    cmd = ["gh", "api", path]
    if paginate:
        cmd.append("--paginate")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except FileNotFoundError:
        sys.stderr.write("error: `gh` CLI not found on PATH\n")
        sys.exit(2)
    if r.returncode != 0:
        if "are disabled for this repository" in r.stdout + r.stderr:
            return DISABLED
        return None
    try:
        # --paginate may emit concatenated JSON arrays; flatten them
        text = r.stdout.strip()
        if not text:
            return None
        if text.startswith("["):
            # Possibly multiple arrays concatenated; wrap and split
            objs = []
            depth = 0
            start = 0
            for i, ch in enumerate(text):
                if ch == "[":
                    if depth == 0:
                        start = i
                    depth += 1
                elif ch == "]":
                    depth -= 1
                    if depth == 0:
                        objs.extend(json.loads(text[start : i + 1]))
            return objs
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def age_days(iso: str) -> int:
    when = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (dt.datetime.now(dt.timezone.utc) - when).days


def repo_snapshot(owner: str, repo: str) -> dict[str, Any]:
    full = f"{owner}/{repo}"
    snap: dict[str, Any] = {"repo": full, "unread": [], "off": []}

    def read(label: str, path: str, default: Any, paginate: bool = True) -> Any:
        """gh_api, noting a failed read as unread so a zero count is never mistaken for a clean one."""
        data = gh_api(path, paginate)
        if data is DISABLED:
            snap["off"].append(label)
            return default
        if data is None:
            snap["unread"].append(label)
            return default
        return data

    alerts = read("Dependabot alerts", f"/repos/{full}/dependabot/alerts?state=open", [])
    snap["dependabot_open"] = len(alerts)
    snap["dependabot_critical"] = sum(
        1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "critical"
    )
    snap["dependabot_high"] = sum(
        1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "high"
    )

    prs = read("pull requests", f"/repos/{full}/pulls?state=open", [])
    snap["open_prs"] = len(prs)
    snap["stale_prs"] = [
        {"number": p["number"], "title": p["title"], "age": age_days(p["updated_at"])}
        for p in prs
        if age_days(p["updated_at"]) >= 14
    ]

    issues = read("issues", f"/repos/{full}/issues?state=open", [])
    # Filter out PRs (GitHub issues endpoint includes them)
    issues = [i for i in issues if "pull_request" not in i]
    snap["open_issues"] = len(issues)

    runs = read("workflow runs", f"/repos/{full}/actions/runs?per_page=20", {}, paginate=False)
    runs_list = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    snap["recent_failures"] = [
        {"name": r["name"], "url": r["html_url"], "branch": r.get("head_branch")}
        for r in runs_list
        if r.get("conclusion") == "failure"
    ][:5]

    releases = read("releases", f"/repos/{full}/releases?per_page=1", [], paginate=False)
    if releases and isinstance(releases, list):
        latest = releases[0]
        snap["last_release"] = {
            "tag": latest.get("tag_name"),
            "age": age_days(latest.get("published_at") or latest.get("created_at")),
        }
    else:
        snap["last_release"] = None

    return snap


def totals(data: dict[str, Any]) -> dict[str, int]:
    """Portfolio-wide counts shared by the issue's summary and the Discord post."""
    repos = data["repos"]
    return {
        "critical": sum(r["dependabot_critical"] for r in repos),
        "high": sum(r["dependabot_high"] for r in repos),
        "prs": sum(r["open_prs"] for r in repos),
        "stale_prs": sum(len(r["stale_prs"]) for r in repos),
        "issues": sum(r["open_issues"] for r in repos),
        "failures": sum(len(r["recent_failures"]) for r in repos),
        "unread": sum(1 for r in repos if r.get("unread")),
    }


def render_summary(data: dict[str, Any]) -> str:
    t = totals(data)
    warning = [f"⚠️ Incomplete: {t['unread']} repo(s) could not be fully read, so these counts may be too low."] if t["unread"] else []
    return "\n".join(warning + [
        f"🔴 Critical vulns: {t['critical']} · 🟠 High: {t['high']}",
        f"🔀 Open PRs: {t['prs']} ({t['stale_prs']} stale) · 🐛 Open issues: {t['issues']}",
        f"⚙️ Recent workflow failures: {t['failures']}",
    ])


def render_markdown(data: dict[str, Any]) -> str:
    lines: list[str] = []
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# Portfolio digest — {now}\n")

    t = totals(data)
    lines.append("## Summary\n")
    if t["unread"]:
        lines.append(f"- ⚠️ **Repos not fully read:** {t['unread']} (their counts below are incomplete)")
    lines.append(f"- **Critical vulns:** {t['critical']}")
    lines.append(f"- **High vulns:** {t['high']}")
    lines.append(f"- **Open PRs:** {t['prs']}")
    lines.append(f"- **Open issues:** {t['issues']}")
    lines.append(f"- **Recent workflow failures:** {t['failures']}\n")

    for r in data["repos"]:
        lines.append(f"## [{r['repo']}](https://github.com/{r['repo']})\n")
        if r.get("unread"):
            lines.append(f"- ⚠️ Could not read: {', '.join(r['unread'])}")
        if r.get("off"):
            lines.append(f"- Turned off for this repo: {', '.join(r['off'])}")
        lines.append(
            f"- Dependabot — critical: **{r['dependabot_critical']}**, "
            f"high: **{r['dependabot_high']}**, "
            f"total open: {r['dependabot_open']}"
        )
        lines.append(f"- Open PRs: {r['open_prs']} · Open issues: {r['open_issues']}")
        if r["last_release"]:
            lines.append(
                f"- Last release: `{r['last_release']['tag']}` ({r['last_release']['age']} days ago)"
            )
        else:
            lines.append("- Last release: _none_")
        if r["stale_prs"]:
            lines.append("- Stale PRs (>14d):")
            for p in r["stale_prs"]:
                lines.append(f"  - #{p['number']} {p['title']} ({p['age']}d)")
        if r["recent_failures"]:
            lines.append("- Recent workflow failures:")
            for f in r["recent_failures"]:
                lines.append(f"  - [{f['name']}]({f['url']}) on `{f['branch']}`")
        lines.append("")

    lines.append("---\n_Auto-generated by `chodeus-ops/.github/workflows/weekly-digest.yml`_")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repos", required=True, help="Comma-separated repo names")
    ap.add_argument("--format", choices=["markdown", "github-issue", "json"], default="markdown")
    ap.add_argument("--summary-out", help="Also write the short summary (portfolio totals) to this file")
    args = ap.parse_args()

    if not shutil.which("gh"):
        sys.stderr.write("error: `gh` CLI not found\n")
        return 2

    data = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "repos": []}
    for name in [r.strip() for r in args.repos.split(",") if r.strip()]:
        data["repos"].append(repo_snapshot(args.owner, name))

    if args.format == "json":
        print(json.dumps(data, indent=2))
    else:
        print(render_markdown(data))
    if args.summary_out:
        with open(args.summary_out, "w", encoding="utf-8") as f:
            f.write(render_summary(data) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
