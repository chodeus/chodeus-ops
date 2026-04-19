#!/usr/bin/env python3
"""Aggregate pending Renovate Dependency Dashboard checkboxes across repos.

Usage:
  renovate_dashboard.py --owner chodeus --repos repo1,repo2 [--format markdown|json]

Exit 0 on success. Prints 'EMPTY' on stderr and exits 0 if no pending items
found — the caller workflow decides whether to notify.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from typing import Any

# Renovate's dashboard body has lines like:
#   - [ ] <!-- unlimit-branch=renovate/foo -->chore(deps): ...
# Checked items start with "- [x]". We count unchecked ("- [ ]") lines whose
# comment identifies a specific branch (unlimit-branch or create-all-...).
PENDING_RE = re.compile(r"^\s*-\s*\[\s\]\s+<!--\s*(unlimit-branch|create-all)")


def gh_api(path: str) -> Any:
    r = subprocess.run(
        ["gh", "api", path, "--paginate"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if r.returncode != 0:
        return []
    try:
        return json.loads(r.stdout or "[]")
    except json.JSONDecodeError:
        return []


def find_dashboard(owner: str, repo: str) -> dict[str, Any] | None:
    """Locate the open Renovate Dependency Dashboard issue, if any.

    Renovate doesn't consistently apply a label to the dashboard issue
    across repos, so we find it by title instead.
    """
    issues = gh_api(f"/repos/{owner}/{repo}/issues?state=open&per_page=100")
    if not isinstance(issues, list):
        return None
    for issue in issues:
        # Skip PRs (the /issues endpoint returns both).
        if issue.get("pull_request"):
            continue
        title = (issue.get("title") or "").lower()
        if "dependency dashboard" in title:
            return issue
    return None


def parse_pending(body: str) -> list[str]:
    """Return pending checkbox lines (stripped) from a dashboard body."""
    return [ln.strip() for ln in body.splitlines() if PENDING_RE.match(ln)]


def collect(owner: str, repos: list[str]) -> dict[str, Any]:
    out = {"repos": [], "total_pending": 0}
    for name in repos:
        issue = find_dashboard(owner, name)
        if not issue:
            out["repos"].append({"repo": f"{owner}/{name}", "dashboard": None, "pending": []})
            continue
        pending = parse_pending(issue.get("body") or "")
        out["repos"].append({
            "repo": f"{owner}/{name}",
            "dashboard": issue.get("html_url"),
            "issue_number": issue.get("number"),
            "pending": pending,
        })
        out["total_pending"] += len(pending)
    return out


def render_markdown(data: dict[str, Any]) -> str:
    lines = ["**Renovate dashboard — pending rate-limited items**\n"]
    any_pending = False
    for r in data["repos"]:
        if not r.get("dashboard"):
            continue
        if not r["pending"]:
            continue
        any_pending = True
        lines.append(f"- [`{r['repo']}`]({r['dashboard']}): **{len(r['pending'])}** pending")
        for item in r["pending"][:5]:
            # Strip checkbox prefix and HTML comment for readability
            clean = re.sub(r"^\s*-\s*\[\s\]\s*", "", item)
            clean = re.sub(r"<!--.*?-->", "", clean).strip()
            lines.append(f"  - {clean}")
        if len(r["pending"]) > 5:
            lines.append(f"  - …and {len(r['pending']) - 5} more")
    if not any_pending:
        lines.append("All dashboards clean — no pending checkboxes.")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repos", required=True, help="Comma-separated list of repo names")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = ap.parse_args()

    if not shutil.which("gh"):
        print("gh CLI not found on PATH", file=sys.stderr)
        return 2

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    data = collect(args.owner, repos)

    if args.format == "json":
        print(json.dumps(data, indent=2))
    else:
        print(render_markdown(data))

    if data["total_pending"] == 0:
        print("EMPTY", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
