#!/usr/bin/env python3
"""Cross-repo consistency audit.

Walks local clones of managed repos and reports pattern drift:
  - Dockerfile base images
  - PUID/PGID defaults
  - Presence of standard workflows (repo-events, codeql, renovate.json)
  - .github/workflows that still implement what chodeus-ops now owns

Usage:
  cross_repo_audit.py --root /path/to/github --repos BeatsCheck,chub,orpheusmorebetter [--format markdown|json]

Assumes each repo is cloned as <root>/<repo>/.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

EXPECTED_FILES = {
    "repo-events workflow": ".github/workflows/repo-events.yml",
    "renovate config": "renovate.json",
    "codeql workflow": ".github/workflows/codeql.yml",
}

LEGACY_WORKFLOW_MARKERS = {
    "inline docker-publish (should call chodeus-ops)": re.compile(
        r"docker/build-push-action|buildx", re.I
    ),
    "inline discord-notify (should call chodeus-ops)": re.compile(
        r"discord.*webhook", re.I
    ),
}


def audit_repo(repo_dir: Path) -> dict[str, Any]:
    name = repo_dir.name
    out: dict[str, Any] = {"repo": name, "findings": []}

    # Expected files
    for label, rel in EXPECTED_FILES.items():
        if not (repo_dir / rel).exists():
            out["findings"].append({"severity": "hygiene", "msg": f"missing: {label} ({rel})"})

    # Legacy workflows that duplicate chodeus-ops reusables
    wf_dir = repo_dir / ".github" / "workflows"
    if wf_dir.exists():
        for wf in wf_dir.glob("*.yml"):
            text = wf.read_text(errors="replace")
            # Skip caller workflows — they reference chodeus-ops
            if "chodeus/chodeus-ops" in text:
                continue
            for label, pat in LEGACY_WORKFLOW_MARKERS.items():
                if pat.search(text):
                    out["findings"].append(
                        {"severity": "regression-risk", "msg": f"{wf.name}: {label}"}
                    )

    # Dockerfile base image (informational)
    for df in list(repo_dir.rglob("Dockerfile"))[:5]:
        for line in df.read_text(errors="replace").splitlines():
            if line.strip().upper().startswith("FROM "):
                out["findings"].append(
                    {"severity": "nit", "msg": f"{df.relative_to(repo_dir)} base: {line.strip()}"}
                )
                break

    # PUID/PGID drift
    for path in list(repo_dir.rglob("Dockerfile"))[:5] + list(repo_dir.rglob("compose*.yaml"))[:5] + list(repo_dir.rglob("docker-compose*.yml"))[:5]:
        text = path.read_text(errors="replace")
        for var, expected in (("PUID", "99"), ("PGID", "100")):
            m = re.search(rf"{var}\s*[:=]\s*['\"]?(\d+)", text)
            if m and m.group(1) != expected:
                out["findings"].append(
                    {
                        "severity": "regression-risk",
                        "msg": f"{path.relative_to(repo_dir)}: {var}={m.group(1)} (expected {expected})",
                    }
                )

    return out


def render_markdown(results: list[dict[str, Any]]) -> str:
    lines = ["# Cross-repo audit\n"]
    for r in results:
        lines.append(f"## {r['repo']}")
        if not r["findings"]:
            lines.append("_clean_\n")
            continue
        by_sev: dict[str, list[str]] = {}
        for f in r["findings"]:
            by_sev.setdefault(f["severity"], []).append(f["msg"])
        for sev in ("blocker", "regression-risk", "hygiene", "nit"):
            if sev in by_sev:
                lines.append(f"### {sev}")
                for m in by_sev[sev]:
                    lines.append(f"- {m}")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, help="Parent directory containing cloned repos")
    ap.add_argument("--repos", required=True, help="Comma-separated repo folder names")
    ap.add_argument("--format", choices=["markdown", "json"], default="markdown")
    args = ap.parse_args()

    root = Path(args.root).expanduser()
    if not root.is_dir():
        sys.stderr.write(f"error: {root} is not a directory\n")
        return 2

    results = []
    for name in [r.strip() for r in args.repos.split(",") if r.strip()]:
        repo = root / name
        if not repo.is_dir():
            sys.stderr.write(f"warn: {repo} not found, skipping\n")
            continue
        results.append(audit_repo(repo))

    if args.format == "json":
        print(json.dumps(results, indent=2))
    else:
        print(render_markdown(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
