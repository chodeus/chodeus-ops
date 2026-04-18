#!/usr/bin/env python3
"""Generate a weekly portfolio digest for chodeus-ops.

Queries GitHub via `gh` CLI for each repo and emits a consolidated Markdown
report: open Dependabot alerts, failing workflows, open PRs, stale issues,
last release age.

Usage:
  digest.py --owner chodeus --repos repo1,repo2 [--format markdown|github-issue|json]

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


def gh_api(path: str) -> Any:
    """Call `gh api <path>` and return parsed JSON, or None on failure."""
    try:
        r = subprocess.run(
            ["gh", "api", path, "--paginate"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        sys.stderr.write("error: `gh` CLI not found on PATH\n")
        sys.exit(2)
    if r.returncode != 0:
        # 404 / no-access is common (Dependabot alerts on public repos without advanced security)
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
    snap: dict[str, Any] = {"repo": full}

    alerts = gh_api(f"/repos/{full}/dependabot/alerts?state=open") or []
    snap["dependabot_open"] = len(alerts)
    snap["dependabot_critical"] = sum(
        1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "critical"
    )
    snap["dependabot_high"] = sum(
        1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "high"
    )

    prs = gh_api(f"/repos/{full}/pulls?state=open") or []
    snap["open_prs"] = len(prs)
    snap["stale_prs"] = [
        {"number": p["number"], "title": p["title"], "age": age_days(p["updated_at"])}
        for p in prs
        if age_days(p["updated_at"]) >= 14
    ]

    issues = gh_api(f"/repos/{full}/issues?state=open") or []
    # Filter out PRs (GitHub issues endpoint includes them)
    issues = [i for i in issues if "pull_request" not in i]
    snap["open_issues"] = len(issues)

    runs = gh_api(f"/repos/{full}/actions/runs?per_page=20") or {}
    runs_list = runs.get("workflow_runs", []) if isinstance(runs, dict) else []
    snap["recent_failures"] = [
        {"name": r["name"], "url": r["html_url"], "branch": r.get("head_branch")}
        for r in runs_list
        if r.get("conclusion") == "failure"
    ][:5]

    releases = gh_api(f"/repos/{full}/releases?per_page=1") or []
    if releases and isinstance(releases, list):
        latest = releases[0]
        snap["last_release"] = {
            "tag": latest.get("tag_name"),
            "age": age_days(latest.get("published_at") or latest.get("created_at")),
        }
    else:
        snap["last_release"] = None

    return snap


def render_markdown(data: dict[str, Any]) -> str:
    lines: list[str] = []
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# Portfolio digest — {now}\n")

    # Top-level roll-up
    total_crit = sum(r["dependabot_critical"] for r in data["repos"])
    total_high = sum(r["dependabot_high"] for r in data["repos"])
    total_prs = sum(r["open_prs"] for r in data["repos"])
    total_issues = sum(r["open_issues"] for r in data["repos"])
    total_fails = sum(len(r["recent_failures"]) for r in data["repos"])

    lines.append("## Summary\n")
    lines.append(f"- **Critical vulns:** {total_crit}")
    lines.append(f"- **High vulns:** {total_high}")
    lines.append(f"- **Open PRs:** {total_prs}")
    lines.append(f"- **Open issues:** {total_issues}")
    lines.append(f"- **Recent workflow failures:** {total_fails}\n")

    for r in data["repos"]:
        lines.append(f"## [{r['repo']}](https://github.com/{r['repo']})\n")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
