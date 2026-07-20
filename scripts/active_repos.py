#!/usr/bin/env python3
"""List active chodeus repos for portfolio-wide automation.

Derives the repo set from the live `gh repo list` output instead of a
hand-maintained allowlist, so newly created repos are picked up
automatically instead of silently missing every workflow that iterates
"the portfolio" until someone remembers to add them.

A handful of forks are pure upstream trackers with no chodeus-specific
changes worth sweeping/digesting; those are excluded by name below. Forks
we DO actively maintain (folder.view3, appdata.cleanup.ng,
robsonfelix-hass-addons) are NOT in this list and so are included, along
with every other non-archived repo.

Used by both security-sweep.yml and weekly-digest.yml — keep SKIP_REPOS
here in sync (add a name below, not to either workflow) when a new
pure-tracking fork shows up.

Usage:
  active_repos.py --owner chodeus [--format csv|lines]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

# Forks kept only to track upstream — nothing chodeus-specific to sweep or
# digest. Update this set, not the calling workflows, when that changes.
SKIP_REPOS = {
    "sbam",
    "hass-dyson",
    "libdyson-rest",
    "webgui",
    "mousehole",
    "Sonarr",
    "folder.view.custom",
    "folder.view.custom.css",
    "Dump",
}


def list_repos(owner: str) -> list[str]:
    r = subprocess.run(
        ["gh", "repo", "list", owner, "--no-archived", "--limit", "200", "--json", "name"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if r.returncode != 0:
        sys.stderr.write(f"error: `gh repo list {owner}` failed: {r.stderr}\n")
        sys.exit(1)
    try:
        repos = json.loads(r.stdout or "[]")
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"error: could not parse `gh repo list` output: {exc}\n")
        sys.exit(1)
    names = [repo["name"] for repo in repos]
    return sorted(n for n in names if n not in SKIP_REPOS)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--owner", default="chodeus")
    ap.add_argument("--format", choices=["csv", "lines"], default="csv")
    args = ap.parse_args()

    repos = list_repos(args.owner)
    if not repos:
        # A failed/empty read must not look like "0 active repos" to a caller
        # that then reports a clean sweep across nothing. Fail loudly instead.
        sys.stderr.write("error: resolved repo list is empty — refusing to silently sweep/digest zero repos\n")
        return 1

    if args.format == "lines":
        print("\n".join(repos))
    else:
        print(",".join(repos))
    return 0


if __name__ == "__main__":
    sys.exit(main())
