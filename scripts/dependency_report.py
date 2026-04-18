#!/usr/bin/env python3
"""Aggregate Dependabot alerts across repos.

Usage:
  dependency_report.py --owner chodeus --repos repo1,repo2 [--format markdown|json] [--output file.json]
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any


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


def collect(owner: str, repos: list[str]) -> dict[str, Any]:
    out = {"repos": []}
    for name in repos:
        full = f"{owner}/{name}"
        alerts = gh_api(f"/repos/{full}/dependabot/alerts?state=open")
        if not isinstance(alerts, list):
            alerts = []
        summary = {
            "repo": full,
            "total": len(alerts),
            "critical": sum(1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "critical"),
            "high": sum(1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "high"),
            "medium": sum(1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "medium"),
            "low": sum(1 for a in alerts if a.get("security_vulnerability", {}).get("severity") == "low"),
            "alerts": [
                {
                    "package": a.get("security_vulnerability", {}).get("package", {}).get("name"),
                    "severity": a.get("security_vulnerability", {}).get("severity"),
                    "summary": (a.get("security_advisory", {}).get("summary") or "")[:200],
                    "url": a.get("html_url"),
                }
                for a in alerts
            ],
        }
        out["repos"].append(summary)
    return out


def render_markdown(data: dict[str, Any]) -> str:
    lines = ["**Dependabot roll-up**\n"]
    for r in data["repos"]:
        if r["total"] == 0:
            lines.append(f"- `{r['repo']}` — clean")
        else:
            lines.append(
                f"- `{r['repo']}` — **{r['critical']} critical**, **{r['high']} high**, "
                f"{r['medium']} med, {r['low']} low"
            )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repos", required=True)
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ap.add_argument("--output", help="Write JSON to file (always, regardless of --format)")
    args = ap.parse_args()

    if not shutil.which("gh"):
        sys.stderr.write("error: `gh` CLI not found\n")
        return 2

    repos = [r.strip() for r in args.repos.split(",") if r.strip()]
    data = collect(args.owner, repos)

    if args.output:
        with open(args.output, "w") as f:
            json.dump(data, f, indent=2)

    if args.format == "json":
        print(json.dumps(data, indent=2))
    else:
        print(render_markdown(data))
    return 0


if __name__ == "__main__":
    sys.exit(main())
