"""The test-build workflow's own step scripts, run in bash against the manifest shapes plugin repos use."""
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github/workflows/unraid-plugin-test-build.yml"

MANIFEST = """<?xml version='1.0' standalone='yes'?>
<!DOCTYPE PLUGIN [
<!ENTITY name "zfs.master">
<!ENTITY author "chodeus">
<!ENTITY github "&author;/ZFS-Master-NG">
<!ENTITY pluginURL "{url}">
<!ENTITY version "2026.11.06.1">
]>
<PLUGIN name="&name;" version="&version;" pluginURL="&pluginURL;"></PLUGIN>
"""


def _steps():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["test-build"]["steps"]


def _step(name):
    return next(s for s in _steps() if s.get("name") == name)


def _update_branch(tmp_path, url, ref):
    plg = tmp_path / "plugin.plg"
    plg.write_text(MANIFEST.format(url=url))
    out = tmp_path / "out.txt"
    out.touch()
    env = {**os.environ, "PLG": str(plg), "GITHUB_REF_NAME": ref, "GITHUB_OUTPUT": str(out)}
    run = subprocess.run(["bash", "-c", _step("Pick the update branch")["run"]], env=env,
                         capture_output=True, text=True, check=True)
    return re.search(r"^branch=(.*)$", out.read_text(), re.M).group(1), run.stdout


@pytest.mark.parametrize("url,ref,want", [
    # A pull request branch is deleted on merge; its manifest names the branch it merges into.
    ("https://raw.githubusercontent.com/&github;/beta/&name;.plg", "fix/live-test-findings", "beta"),
    ("https://raw.githubusercontent.com/&github;/beta/&name;.plg", "beta", "beta"),
    ("https://raw.githubusercontent.com/&github;/main/&name;.plg", "main", "main"),
    ("https://raw.githubusercontent.com/chodeus/folder.view3/beta/folder.view3.plg", "feat/x", "beta"),
    # Any segment plg_release.py accepts and git allows as a branch name.
    ("https://raw.githubusercontent.com/&github;/release!/&name;.plg", "feat/x", "release!"),
])
def test_update_branch_comes_from_the_manifest(tmp_path, url, ref, want):
    assert _update_branch(tmp_path, url, ref)[0] == want


@pytest.mark.parametrize("url", [
    "https://example.com/zfs.master.plg",
    "https://raw.githubusercontent.com/&github;/bad..name/&name;.plg",
])
def test_an_unusable_plugin_url_keeps_the_built_branch_and_warns(tmp_path, url):
    branch, stdout = _update_branch(tmp_path, url, "feat/x")
    assert branch == "feat/x"
    assert "::warning::" in stdout and "--branch feat/x" in stdout


def test_the_build_gets_the_picked_branch(tmp_path):
    step = _step("Build the package")
    assert step["env"]["BRANCH"] == "${{ steps.branch.outputs.branch }}"
    args = tmp_path / "args.txt"
    build = tmp_path / "build.sh"
    build.write_text(f'printf "%s\\n" "$@" > "{args}"\nmkdir -p dist && touch dist/plugin.txz\n')
    env = {**os.environ, "BUILD_CMD": f"bash {build}", "VERSION": "2026.11.06.1.0261106010101",
           "BRANCH": "beta", "GITHUB_REF_NAME": "fix/live-test-findings"}
    subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, check=True, capture_output=True)
    assert args.read_text().split() == ["--version", "2026.11.06.1.0261106010101", "--branch", "beta"]


def test_the_release_notes_name_the_published_update_url(tmp_path):
    """The build script decides pluginURL, so the notes read it from the manifest being published."""
    published = "https://raw.githubusercontent.com/chodeus/ZFS-Master-NG/beta/zfs.master.plg"
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist/plugin.plg").write_text(MANIFEST.format(url=published))
    (tmp_path / "dist/plugin.txz").write_text("pkg")
    notes = tmp_path / "notes.txt"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # gh stand-in: no existing release or tag, and `release create` keeps its notes file.
    (bin_dir / "gh").write_text(
        '#!/bin/bash\n[ "$1 $2" = "release create" ] || exit 1\n'
        f'while [ $# -gt 0 ]; do [ "$1" = --notes-file ] && cp "$2" "{notes}"; shift; done\n')
    (bin_dir / "gh").chmod(0o755)
    out = tmp_path / "out.txt"
    out.touch()
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "PLG": "plugin.plg", "TAG": "test",
           "VERSION": "2026.11.06.1.0261106010101", "VERSION_IN": "", "GITHUB_REPOSITORY": "chodeus/ZFS-Master-NG",
           "GITHUB_SHA": "a" * 40, "GITHUB_REF_NAME": "fix/live-test-findings", "GITHUB_OUTPUT": str(out)}
    subprocess.run(["bash", "-c", _step("Publish the pre-release")["run"]], cwd=tmp_path, env=env,
                   check=True, capture_output=True)
    assert f"Check for Updates reads `{published}`" in notes.read_text()


def test_the_branch_is_picked_after_xmllint_and_before_the_build():
    names = [s.get("name") for s in _steps()]
    assert names.index("Install xmllint") < names.index("Pick the update branch") < names.index("Build the package")
