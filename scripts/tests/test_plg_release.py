import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import plg_release as pr  # noqa: E402

FV3_PLG = """<?xml version="1.0" standalone="yes"?>
<!DOCTYPE PLUGIN [
<!ENTITY name "folder.view3">
<!ENTITY author "chodeus">
<!ENTITY github "&author;/&name;">
<!ENTITY pluginURL "https://raw.githubusercontent.com/&github;/beta/&name;.plg">
<!ENTITY version "2026.08.28">
<!ENTITY md5 "8c16fcd81c312f8d6231c167dc398433">
<!ENTITY pkgname "&name;-&version;-x86_64-1">
]>

<PLUGIN name="&name;" version="&version;" pluginURL="&pluginURL;">
    <CHANGES>

After updating, hard-refresh your browser &amp; clear cache &lt;now&gt;.

###2026.08.28
- Containers assigned by a label no longer override a folder you picked
- Corrupt config files now fail safe

###2026.08.14 - Titled release
- Uninstalling now removes the plugin cleanly

###2026.08.01.2 and 2026.08.01.1
- beta-era hotfix

###2026.08.01
- Initial Release

###2026.08.01
- Duplicate heading from the early days
    </CHANGES>

    <FILE Name="/boot/config/plugins/&name;/&pkgname;.txz" Run="upgradepkg --install-new --reinstall">
        <URL>https://github.com/&github;/releases/download/v&version;/&pkgname;.txz</URL>
        <MD5>&md5;</MD5>
    </FILE>
</PLUGIN>
"""

ACNG_PLG = """<?xml version='1.0' standalone='yes'?>
<!DOCTYPE PLUGIN [
<!ENTITY name      "appdata.cleanup.ng">
<!ENTITY version   "2026.07.09">
<!ENTITY md5       "4cccb8e5ba9fe6e0fdf5568952a39ee8">
<!ENTITY github    "chodeus/appdata.cleanup.ng">
<!ENTITY pluginURL "https://raw.githubusercontent.com/&github;/main/plugins/&name;.plg">
]>
<PLUGIN name="&name;" version="&version;">
<CHANGES>
###ALWAYS VERIFY THE FOLDERS THE PLUGIN OFFERS BEFORE DELETING

###2026.07.09
- In use badge

###2026.07.02
- ZFS destroy re-checks
</CHANGES>
<FILE Name="/boot/config/plugins/&name;/&name;-&version;-x86_64-1.txz" Run="upgradepkg --install-new">
<URL>https://raw.githubusercontent.com/&github;/main/archive/&name;-&version;-x86_64-1.txz</URL>
<MD5>&md5;</MD5>
</FILE>
</PLUGIN>
"""


def run(*argv):
    return pr.main([str(a) for a in argv])


@pytest.fixture
def fv3(tmp_path):
    plg = tmp_path / "folder.view3.plg"
    plg.write_text(FV3_PLG)
    return plg, tmp_path / "CHANGELOG.md"


def test_migrate_then_render_is_byte_identical(fv3):
    plg, changelog = fv3
    assert run("migrate", "--plg", plg, "--changelog", changelog) == 0
    assert run("render", "--plg", plg, "--changelog", changelog, "--channel", "beta") == 0
    assert plg.read_text() == FV3_PLG
    assert run("render", "--plg", plg, "--changelog", changelog, "--channel", "beta", "--check") == 0


def test_migrate_unescapes_and_keeps_heading_suffixes(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    text = changelog.read_text()
    assert "browser & clear cache <now>." in text
    assert "## 2026.08.14 - Titled release\n" in text
    assert "## 2026.08.01.2 and 2026.08.01.1\n" in text
    assert pr.load_changelog(changelog).find("2026.08.01.2").rest == " and 2026.08.01.1"


def test_stable_channel_hides_beta_sections(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    text = changelog.read_text().replace("## 2026.08.01.2 and", "## 2026.08.01.2 (beta) and")
    changelog.write_text(text)
    run("render", "--plg", plg, "--changelog", changelog, "--channel", "stable")
    assert "###2026.08.01.2" not in plg.read_text()
    run("render", "--plg", plg, "--changelog", changelog, "--channel", "beta")
    assert "###2026.08.01.2 and 2026.08.01.1\n- beta-era hotfix" in plg.read_text()


def test_acng_phantom_heading_becomes_preamble(tmp_path):
    plg = tmp_path / "acng.plg"
    plg.write_text(ACNG_PLG)
    changelog = tmp_path / "CHANGELOG.md"
    run("migrate", "--plg", plg, "--changelog", changelog)
    log = pr.load_changelog(changelog)
    assert log.preamble == ["ALWAYS VERIFY THE FOLDERS THE PLUGIN OFFERS BEFORE DELETING"]
    assert [s.version for s in log.sections] == ["2026.07.09", "2026.07.02"]
    run("render", "--plg", plg, "--changelog", changelog, "--channel", "stable")
    body = plg.read_text()
    assert "<CHANGES>\n\nALWAYS VERIFY THE FOLDERS THE PLUGIN OFFERS BEFORE DELETING\n\n###2026.07.09\n" in body
    assert "###ALWAYS" not in body


def test_body_line_that_looks_like_heading_is_rejected(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    changelog.write_text(changelog.read_text().replace("- Initial Release", "- Initial Release\n#### oops"))
    with pytest.raises(pr.ChangelogError):
        pr.load_changelog(changelog)


def test_duplicate_released_heading_tolerated_but_unreleased_unique(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    assert [s.version for s in pr.load_changelog(changelog).sections].count("2026.08.01") == 2
    changelog.write_text(changelog.read_text() + "\n## Unreleased\n\n- a\n\n## Unreleased\n\n- b\n")
    with pytest.raises(pr.ChangelogError):
        pr.load_changelog(changelog)


def test_stamp_notes_and_check_flags(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    text = changelog.read_text().replace("\n## 2026.08.28\n", "\n## Unreleased\n\n- fix(ui): raw seed (#9)\n\n## 2026.08.28\n", 1)
    changelog.write_text(text)
    assert run("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta") == 0
    assert run("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "main") == 1
    assert run("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta", "--require-edited") == 1
    changelog.write_text(changelog.read_text().replace("- fix(ui): raw seed (#9)", "- Raw seed rewritten as prose (#9)"))
    assert run("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta", "--require-edited") == 0
    assert run("stamp", "--changelog", changelog, "--version", "2026.09.05.1", "--beta") == 0
    assert "## 2026.09.05.1 (beta)\n\n- Raw seed rewritten as prose (#9)" in changelog.read_text()
    assert run("stamp", "--changelog", changelog, "--version", "2026.09.05.2", "--beta") == 2
    assert run("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta", "--require-nonempty") == 1
    run("render", "--plg", plg, "--changelog", changelog, "--channel", "beta")
    assert "###2026.09.05.1\n- Raw seed rewritten as prose (#9)\n\n###2026.08.28" in plg.read_text()
    assert run("notes", "--changelog", changelog, "--version", "2026.09.05.1", "--footer", "Install: x") == 0


SCRIPTS = Path(__file__).resolve().parents[1]


def test_release_token_is_absent_from_git_config_during_the_build(tmp_path):
    """The build command must not be able to read the token out of .git/config."""
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "remote", "add", "origin",
                    "https://github.com/chodeus/plugin.git"], check=True)
    probe = tmp_path / "seen.txt"
    script = """
set -euo pipefail
cd "$1"
. "$2/plg_release_git.sh"
plg_git_setup
grep -c 'SEKRIT' .git/config >> "$3" || true   # before: token attached
plg_git_deauth
grep -c 'SEKRIT' .git/config >> "$3" || true   # during the build: must be 0
plg_git_auth
grep -c 'SEKRIT' .git/config >> "$3" || true   # after: reattached for the push
"""
    env = {**os.environ, "GH_TOKEN": "SEKRIT", "GITHUB_REPOSITORY": "chodeus/plugin",
           "GIT_USER": "chodeus", "GIT_EMAIL": "c@example.com"}
    subprocess.run(["bash", "-c", script, "sh", str(tmp_path), str(SCRIPTS), str(probe)],
                   check=True, env=env, capture_output=True)
    before, during, after = probe.read_text().split()
    assert (before, during, after) == ("1", "0", "1"), probe.read_text()


USES = "chodeus/chodeus-ops/.github/workflows/unraid-plugin-release.yml"


def _resolve_ops_ref(tmp_path, caller_yaml, later=None):
    """Run the workflow's own 'Resolve release-scripts ref' script against the caller file at the run's commit."""
    import yaml

    wf = yaml.safe_load((SCRIPTS.parent / ".github/workflows/unraid-plugin-release.yml").read_text())
    step = next(s for s in wf["jobs"]["release"]["steps"] if s.get("id") == "opsref")
    repo = tmp_path / "repo"
    caller = repo / ".github/workflows/release.yml"
    caller.parent.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    shas = []
    for text in [caller_yaml] + ([later] if later else []):
        caller.write_text(text)
        _git(repo, "add", "-A")
        _git(repo, "-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "caller")
        shas.append(_git(repo, "rev-parse", "HEAD"))
    out = tmp_path / "out.txt"
    out.touch()
    env = {**os.environ, "GITHUB_WORKFLOW_REF": "chodeus/plugin/.github/workflows/release.yml@refs/heads/main",
           "GITHUB_OUTPUT": str(out), "GITHUB_SHA": shas[0]}
    subprocess.run(["bash", "-c", step["run"]], cwd=tmp_path, env=env, check=True, capture_output=True)
    return re.search(r"ref=(.*)", out.read_text()).group(1)


@pytest.mark.parametrize("value,want", [
    (f"uses: {USES}@main", "main"),
    (f'uses: "{USES}@abc1234"', "abc1234"),
    (f"uses: '{USES}@v1.2'", "v1.2"),
    (f"uses: {USES}@abc1234 # v1.2", "abc1234"),
    (f"uses: {USES}@release/v1+hotfix", "release/v1+hotfix"),
    (f"uses: {USES}@abc1234\r", "abc1234"),
    (f"uses: {USES}@abc1234\t", "abc1234"),
])
def test_ops_ref_parsing(tmp_path, value, want):
    assert _resolve_ops_ref(tmp_path, f"jobs:\n  release:\n    {value}\n") == want


def test_ops_ref_fails_rather_than_defaulting_to_main(tmp_path):
    """Guessing a ref would run unreviewed upstream scripts with RELEASE_TOKEN."""
    with pytest.raises(subprocess.CalledProcessError):
        _resolve_ops_ref(tmp_path, "jobs:\n  release:\n    uses: some/other/workflow.yml@x\n")


def test_ops_ref_ignores_a_commented_out_uses_line(tmp_path):
    caller = f"jobs:\n  release:\n    # uses: {USES}@stale000\n    uses: {USES}@live111\n"
    assert _resolve_ops_ref(tmp_path, caller) == "live111"


def test_ops_ref_is_read_where_the_run_started_not_at_the_branch_tip(tmp_path):
    """GitHub ran the workflow as pinned at the event commit; a pin bump on the branch since must not swap its scripts."""
    started = f"jobs:\n  release:\n    uses: {USES}@old1111\n"
    assert _resolve_ops_ref(tmp_path, started, later=started.replace("old1111", "new2222")) == "old1111"


def _release_steps():
    import yaml

    wf = yaml.safe_load((SCRIPTS.parent / ".github/workflows/unraid-plugin-release.yml").read_text())
    return {s.get("name"): s for s in wf["jobs"]["release"]["steps"]}


def test_the_release_job_works_from_the_branch_tip():
    """A queued or re-run job's event commit can be behind its branch, and a cut from it cannot push."""
    assert _release_steps()["Checkout plugin repo"]["with"]["ref"] == "${{ github.ref }}"


def test_cross_channel_refreshes_wait_for_the_other_channels_merged_release():
    """A refresh while that channel's merged release awaits its cut would reopen the released notes as a new PR."""
    steps = _release_steps()
    for name in ("Refresh stable release PR after a beta", "Refresh beta release PR after a stable"):
        assert "steps.decide.outputs.other_pr == ''" in steps[name]["if"], name


def _run_decide(tmp_path, mode, ref_name="main"):
    """Run the workflow's 'Decide channel and mode' step with the given inputs; no release PR is merged."""
    import yaml

    wf = yaml.safe_load((SCRIPTS.parent / ".github/workflows/unraid-plugin-release.yml").read_text())
    step = next(s for s in wf["jobs"]["release"]["steps"] if s.get("id") == "decide")
    (tmp_path / "gh").write_text("#!/bin/bash\necho ' '\n")
    (tmp_path / "gh").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "STABLE": "main", "BETA": "beta", "MODE_IN": mode,
           "GITHUB_REF_NAME": ref_name, "GITHUB_OUTPUT": "/dev/null",
           "GITHUB_REPOSITORY": "chodeus/plugin", "GITHUB_SHA": "deadbeef"}
    return subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True)


@pytest.mark.parametrize("mode", ["relase", "", "RELEASE", "release; rm -rf /"])
def test_decide_rejects_an_unsupported_mode(tmp_path, mode):
    r = _run_decide(tmp_path, mode)
    assert r.returncode == 1, r.stdout
    assert "mode must be auto, pr or release" in r.stdout + r.stderr


@pytest.mark.parametrize("mode", ["pr", "release"])
def test_decide_accepts_the_supported_modes(tmp_path, mode):
    assert _run_decide(tmp_path, mode).returncode == 0


def test_stamp_rejects_a_version_the_parser_would_not_accept(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## Unreleased\n\n- something\n")
    for bad in ("1", "v2026.09.05", "2026.9.5", "latest"):
        assert run("stamp", "--changelog", changelog, "--version", bad) == 2, bad
    assert "## Unreleased" in changelog.read_text(), "a rejected stamp must not mutate the file"
    assert run("stamp", "--changelog", changelog, "--version", "2026.09.05") == 0


def test_backmerge_tolerates_an_already_merged_base():
    """A dry-run cut leaves the base already merged, so the back-merge stages nothing."""
    body = (SCRIPTS / "plg_release_backmerge.sh").read_text()
    assert "git commit -q --allow-empty" in body


def test_cut_names_the_recovery_command_when_publishing_fails():
    """No auto-rollback: a failure between tagging and the base push must say how to undo it."""
    body = (SCRIPTS / "plg_release_cut.sh").read_text()
    trap, tag, push, disarm = (body.index(t) for t in
                               ("trap 'echo", 'git tag "v$version"', 'git push -q origin "HEAD:$BASE"', "trap - ERR"))
    assert trap < tag < push < disarm, "the guidance must be armed before tagging and cleared after the push"
    assert "gh release delete v$version --cleanup-tag" in body
    assert "git push --delete origin v$version" in body, "the tag-only failure needs its own remedy"
    assert "gh release delete" not in body.split("trap - ERR")[1], "nothing may delete a release automatically"


def test_every_ampersand_is_escaped_so_changes_is_always_valid_xml():
    """Entity-like prose must not survive: &copy; is undeclared and would break the manifest."""
    assert pr._xml_escape("Tom & Jerry, &copy; 2026") == "Tom &amp; Jerry, &amp;copy; 2026"


def test_require_edited_catches_sha_suffixed_seed_bullets(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    args = ("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta", "--require-edited")

    def with_unreleased(bullet):
        base = pr.load_changelog(changelog)
        base.sections = [s for s in base.sections if s.released]
        base.sections.insert(0, pr.Section(pr.UNRELEASED, body=[bullet]))
        pr.save_changelog(changelog, base)

    with_unreleased("- Enhance icon caching (a1b2c3d)")  # seed shape: subject + short sha
    assert run(*args) == 1
    with_unreleased("- fix(ui): keep folder order")  # seed shape: conventional commit
    assert run(*args) == 1
    with_unreleased("- Folders keep their order when labels disagree (#63)")  # edited: cites a PR
    assert run(*args) == 0


def test_git_failure_surfaces_stderr(tmp_path, capsys):
    changelog = tmp_path / "c.md"
    changelog.write_text("# Changelog\n\n## 2026.09.05\n\n- x\n")
    assert run("seed", "--changelog", changelog, "--channel", "beta", "--since", "v-does-not-exist", "--repo", tmp_path) == 2
    err = capsys.readouterr().err
    assert "not a git repository" in err.lower() or "unknown revision" in err.lower()


def test_execution_errors_exit_2_not_1(tmp_path):
    missing = tmp_path / "nope.md"
    plg = tmp_path / "nope.plg"
    assert run("check", "--changelog", missing, "--plg", plg, "--channel", "stable", "--branch", "main") == 2
    changelog = tmp_path / "c.md"
    changelog.write_text("# Changelog\n\n## 2026.09.05\n\n- x\n")
    assert run("next-version", "--channel", "stable", "--tz", "Mars/Olympus", "--repo", tmp_path) == 2


def test_check_reports_unsynced_plg(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    plg.write_text(plg.read_text().replace("- Corrupt config files now fail safe\n", ""))
    assert run("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta") == 1


def test_verify_asset_compares_md5_and_reports_download_failure(fv3, monkeypatch):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    args = ("check", "--changelog", changelog, "--plg", plg, "--channel", "beta", "--branch", "beta", "--verify-asset")
    monkeypatch.setattr(pr, "fetch_md5", lambda url, attempts=3: "8c16fcd81c312f8d6231c167dc398433")
    assert run(*args) == 0
    monkeypatch.setattr(pr, "fetch_md5", lambda url, attempts=3: "0" * 32)
    assert run(*args) == 1
    monkeypatch.setattr(pr, "fetch_md5", lambda url, attempts=3: (_ for _ in ()).throw(OSError("truncated")))
    assert run(*args) == 1


def test_fetch_md5_retries_then_raises(monkeypatch):
    calls = []

    def boom(url, timeout):
        calls.append(url)
        raise pr.http.client.IncompleteRead(b"x")

    monkeypatch.setattr(pr.urllib.request, "urlopen", boom)
    monkeypatch.setattr(pr.time, "sleep", lambda s: None)
    with pytest.raises(OSError):
        pr.fetch_md5("https://example.invalid/x.txz", attempts=3)
    assert len(calls) == 3


def test_package_url_resolves_nested_entities(fv3):
    plg, _ = fv3
    assert pr.package_url(plg.read_text()) == (
        "https://github.com/chodeus/folder.view3/releases/download/v2026.08.28/folder.view3-2026.08.28-x86_64-1.txz"
    )


@pytest.fixture
def repo(tmp_path):
    def sh(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    sh("init", "-q", "-b", "main")
    sh("config", "user.email", "t@example.com")
    sh("config", "user.name", "t")
    (tmp_path / "f").write_text("0")
    sh("add", "f")
    sh("commit", "-qm", "chore: initial")
    sh("tag", "v2026.09.04")
    for msg in ["fix(ui): keep folder order (#63)", "chore(deps): update actions", "Merge branch x",
                "feat: new thing", "ci: tweak workflow", "Build beta 2026.09.05.1", "chore: tune CodeRabbit config",
                "docs: readme", "Plain subject"]:
        (tmp_path / "f").write_text(msg)
        sh("commit", "-qam", msg)
    return tmp_path


def test_commit_bullets_filters_noise(repo):
    bullets = pr.commit_bullets(repo, "v2026.09.04", "HEAD")
    assert [b.split(" (")[0] for b in bullets] == ["- Plain subject", "- feat: new thing", "- fix(ui): keep folder order"]


def test_seed_is_append_only_and_carries_edits(repo):
    changelog = repo / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2026.09.04\n\n- Old\n")
    old = repo / "old.md"
    old.write_text("# Changelog\n\n## Unreleased\n\n- Folder order is kept when labels disagree (#63)\n\n## 2026.09.04\n\n- Old\n")
    run("seed", "--changelog", changelog, "--channel", "beta", "--carry-from", old, "--since", "v2026.09.04", "--repo", repo)
    body = pr.load_changelog(changelog).unreleased().bullets()
    assert body[0] == "- Folder order is kept when labels disagree (#63)"
    assert "- feat: new thing" in body and "- fix(ui): keep folder order (#63)" in body
    run("seed", "--changelog", changelog, "--channel", "beta", "--since", "v2026.09.04", "--repo", repo)
    assert pr.load_changelog(changelog).unreleased().bullets() == body


def test_seed_from_beta_sections_stops_at_last_stable(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## 2026.09.06.2 (beta)\n\n- Newer beta\n\n## 2026.09.06.1 (beta)\n\n- Older beta\n- Shared\n\n"
        "## 2026.09.01\n\n- Stable\n\n## 2026.08.30.1 (beta)\n\n- Ancient beta\n"
    )
    run("seed", "--changelog", changelog, "--channel", "stable", "--beta-sections")
    assert pr.load_changelog(changelog).unreleased().bullets() == ["- Older beta", "- Shared", "- Newer beta"]


def test_next_version_stable_is_the_bare_date_unless_taken(repo):
    """A same-day beta must not push the stable to .N; only a second stable that day takes the shared counter."""
    sh = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    assert pr.next_version(repo, "stable", "2026.09.05") == "2026.09.05"
    assert pr.next_version(repo, "beta", "2026.09.05") == "2026.09.05.1"
    sh("tag", "v2026.09.05.1")
    assert pr.next_version(repo, "beta", "2026.09.05") == "2026.09.05.2"
    assert pr.next_version(repo, "stable", "2026.09.05") == "2026.09.05"
    sh("tag", "v2026.09.05.2")
    sh("tag", "v2026.09.05")
    assert pr.next_version(repo, "stable", "2026.09.05") == "2026.09.05.3"
    assert pr.next_version(repo, "beta", "2026.09.05") == "2026.09.05.3"
    assert pr.next_version(repo, "stable", "2026.09.04") == "2026.09.04.1"


def test_seed_does_not_carry_bullets_that_already_shipped(repo):
    """release/<channel> outlives its cut, so its Unreleased may hold bullets a released section now carries."""
    changelog = repo / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2026.09.05\n\n- Shipped in the cut\n\n## 2026.09.04\n\n- Old\n")
    old = repo / "old.md"
    old.write_text("# Changelog\n\n## Unreleased\n\n- Shipped in the cut\n- Still pending\n\n## 2026.09.04\n\n- Old\n")
    run("seed", "--changelog", changelog, "--channel", "stable", "--carry-from", old, "--since", "HEAD", "--repo", repo)
    assert pr.load_changelog(changelog).unreleased().bullets() == ["- Still pending"]


def test_cut_retires_the_release_branch_only_after_the_base_push():
    """A surviving release/<channel> would carry its released bullets into the next release PR."""
    body = (SCRIPTS / "plg_release_cut.sh").read_text()
    delete = 'git push -q --force-with-lease="refs/heads/release/$CHANNEL:$merged_head" origin ":refs/heads/release/$CHANNEL"'
    push, disarm, gone = (body.index(t) for t in ('git push -q origin "HEAD:$BASE"', "trap - ERR", delete))
    assert push < disarm < gone, "the branch goes only once the release is fully published"


def test_merge_changelog_inserts_missing_sections_and_keeps_our_order(tmp_path):
    ours = tmp_path / "ours.md"
    theirs = tmp_path / "theirs.md"
    out = tmp_path / "out.md"
    ours.write_text("# Changelog\n\nPreamble\n\n## Unreleased\n\n- Draft\n\n## 2026.09.06\n\n- Stable six\n\n"
                    "## 2026.09.01\n\n- One\n\n## 2026.09.03\n\n- Historic out-of-order block\n")
    theirs.write_text("# Changelog\n\n## 2026.09.06.1 (beta)\n\n- Beta six one\n\n## 2026.09.02 (beta)\n\n- Two\n\n"
                      "## 2026.09.01\n\n- One (beta copy)\n")
    run("merge-changelog", "--ours", ours, "--theirs", theirs, "--out", out)
    log = pr.load_changelog(out)
    assert [s.version for s in log.sections] == [
        "Unreleased", "2026.09.06.1", "2026.09.06", "2026.09.02", "2026.09.01", "2026.09.03"]
    assert log.find("2026.09.01").body == ["- One"]
    assert log.find("2026.09.06.1").beta is True
    assert log.preamble == ["Preamble"]


def test_last_version_and_entity(fv3):
    plg, changelog = fv3
    run("migrate", "--plg", plg, "--changelog", changelog)
    changelog.write_text(changelog.read_text().replace("## 2026.08.28\n", "## 2026.08.28 (beta)\n"))
    assert pr.load_changelog(changelog).released("stable")[0].version == "2026.08.14"
    assert pr.load_changelog(changelog).released("beta")[0].version == "2026.08.28"
    assert run("last-version", "--changelog", changelog, "--channel", "stable") == 0
    assert run("entity", "--plg", plg, "--name", "pluginURL") == 0
    assert pr.plg_entities(plg.read_text())["pluginURL"] == "https://raw.githubusercontent.com/chodeus/folder.view3/beta/folder.view3.plg"
    assert run("entity", "--plg", plg, "--name", "nope") == 2


RELEASES = ('[{"tag_name":"v2026.09.21","prerelease":false,"draft":true},'
            '{"tag_name":"pr-12-2026.09.20.0260920101010","prerelease":true,"draft":false},'
            '{"tag_name":"test-2026.09.19.9101010","prerelease":true,"draft":false},'
            '{"tag_name":"v2026.09.20.2","prerelease":true,"draft":false},'
            '{"tag_name":"v2026.09.19","prerelease":false,"draft":false},'
            '{"tag_name":"v2026.09.18.1","prerelease":true,"draft":false},'
            '{"tag_name":"v2026.09.07","prerelease":false,"draft":false}]')


def _rollback_footer(tmp_path, gh_body, channel="stable", plgr_body="echo verified", call="rollback_footer",
                     plg="plugin.plg"):
    """Run rollback_footer from plg_release_cut.sh against a stub gh and a stub release helper."""
    body = (SCRIPTS / "plg_release_cut.sh").read_text()
    fn = re.search(r"^rollback_footer\(\) \{.*?^\}", body, re.M | re.S).group(0)
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    for name, text in (("gh", gh_body), ("plgr", plgr_body)):
        (bindir / name).write_text("#!/bin/bash\n" + text + "\n")
        (bindir / name).chmod(0o755)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "GITHUB_REPOSITORY": "chodeus/plugin",
           "PLG": plg, "CHANNEL": channel, "PLGR": str(bindir / "plgr"),
           "STABLE_PLUGIN_URL": "https://raw.githubusercontent.com/chodeus/plugin/main/plugin.plg"}
    return subprocess.run(["bash", "-c", f"set -euo pipefail\n{fn}\n{call}"], cwd=tmp_path, env=env,
                          capture_output=True, text=True)


def _gh_listing(*pages):
    """A gh stub that applies the script's own --jq filter to each page of a canned release listing."""
    body = 'all=""\nwhile [ $# -gt 0 ]; do case "$1" in --jq) f="$2"; shift ;; --paginate) all=1 ;; esac; shift; done\n'
    for n, page in enumerate(pages or (RELEASES,)):
        body += ('[ -n "$all" ] || exit 0\n' if n else "") + f"jq -r \"$f\" <<'JSON'\n{page}\nJSON\n"
    return body


def test_rollback_note_pins_the_previous_release(tmp_path):
    """The rollback command installs the manifest as it was at the last stable tag."""
    r = _rollback_footer(tmp_path, _gh_listing())
    assert r.returncode == 0, r.stderr
    assert "plugin install https://raw.githubusercontent.com/chodeus/plugin/v2026.09.19/plugin.plg forced" in r.stdout.splitlines()
    assert "Removing the plugin first would delete its settings." in r.stdout


def test_beta_rollback_pins_the_previous_beta_and_offers_the_way_back_to_stable(tmp_path):
    r = _rollback_footer(tmp_path, _gh_listing(), channel="beta")
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert "plugin install https://raw.githubusercontent.com/chodeus/plugin/v2026.09.20.2/plugin.plg forced" in lines
    assert "plugin install https://raw.githubusercontent.com/chodeus/plugin/main/plugin.plg forced" in lines
    assert "pr-12" not in r.stdout and "test-" not in r.stdout, "test builds are pre-releases but never betas"


def test_first_beta_offers_only_the_way_back_to_stable(tmp_path):
    r = _rollback_footer(tmp_path, _gh_listing('[{"tag_name":"v2026.09.19","prerelease":false,"draft":false}]'),
                         channel="beta")
    assert r.returncode == 0, r.stderr
    assert "Rollback to" not in r.stdout and "go back to the stable release" in r.stdout


def test_rollback_note_finds_the_previous_release_past_the_first_page(tmp_path):
    """A run of betas can push the last stable release off the first page of the listing."""
    betas = json.dumps([{"tag_name": f"v2026.09.20.{n}", "prerelease": True, "draft": False} for n in range(100, 0, -1)])
    r = _rollback_footer(tmp_path, _gh_listing(betas, RELEASES))
    assert r.returncode == 0, r.stderr
    assert "plugin install https://raw.githubusercontent.com/chodeus/plugin/v2026.09.19/plugin.plg forced" in r.stdout.splitlines()


def test_rollback_note_percent_encodes_the_manifest_path(tmp_path):
    """A # or ? in the manifest path stays part of the path instead of starting a fragment or a query."""
    r = _rollback_footer(tmp_path, _gh_listing(), plg="plugins/my #1?.plg", plgr_body='echo "$*" >> verified; echo ok')
    assert r.returncode == 0, r.stderr
    url = "https://raw.githubusercontent.com/chodeus/plugin/v2026.09.19/plugins/my%20%231%3F.plg"
    assert f"plugin install {url} forced" in r.stdout.splitlines()
    assert f"--url {url}" in (tmp_path / "verified").read_text()


def test_no_rollback_note_before_the_first_stable_release(tmp_path):
    """Without a stable release there is nothing to go back to."""
    r = _rollback_footer(tmp_path, _gh_listing("[]"))
    assert (r.returncode, r.stdout) == (0, "")


def test_an_unexpected_tag_never_reaches_the_pasted_command(tmp_path):
    """The stub bypasses the listing filter; the script's own shape check must still refuse the tag."""
    r = _rollback_footer(tmp_path, "echo 'v1;touch INJECTED;#'", call='rollback=$(rollback_footer); echo reached')
    assert r.returncode != 0 and "reached" not in r.stdout
    assert not (tmp_path / "INJECTED").exists()


@pytest.mark.parametrize("channel,gh_body,plgr_body", [
    ("stable", "echo 'HTTP 502' >&2; exit 1", "echo verified"),  # the release lookup failed
    ("stable", _gh_listing(), "echo 'HTTP Error 404' >&2; exit 2"),  # the rollback target no longer installs
    ("beta", _gh_listing(), 'case "$*" in *"/main/"*) echo "HTTP Error 404" >&2; exit 2 ;; esac; echo verified'),
])
def test_a_rollback_note_that_cannot_be_made_right_stops_the_release(tmp_path, channel, gh_body, plgr_body):
    r = _rollback_footer(tmp_path, gh_body, channel=channel, plgr_body=plgr_body,
                         call='rollback=$(rollback_footer); echo reached')
    assert r.returncode != 0 and "reached" not in r.stdout


def _manifest(branch="main", version="2026.09.19", md5="0" * 32, install="chmod 644 /usr/local/sbin/x"):
    return (
        '<?xml version="1.0" standalone="yes"?>\n<!DOCTYPE PLUGIN [\n'
        '<!ENTITY name "p">\n<!ENTITY author "chodeus">\n<!ENTITY github "&author;/&name;">\n'
        f'<!ENTITY pluginURL "https://raw.githubusercontent.com/&github;/{branch}/&name;.plg">\n'
        f'<!ENTITY version "{version}">\n<!ENTITY md5 "{md5}">\n'
        ']>\n<PLUGIN name="&name;" version="&version;" pluginURL="&pluginURL;">\n'
        f"<CHANGES>\n\n###{version}\n- Notes for {version}\n</CHANGES>\n"
        f'<FILE Run="/bin/bash">\n<INLINE>\n{install}\necho step one\necho step two\necho installed\n</INLINE>\n</FILE>\n</PLUGIN>\n'
    )


def test_merge_manifest_takes_the_other_branch_installer_and_keeps_our_release_fields():
    merged = pr.merge_manifest(_manifest(), _manifest(version="2026.09.20", md5="a" * 32),
                               _manifest("beta", "2026.09.21.1", "b" * 32, "chmod 755 /usr/local/sbin/x"))
    assert "chmod 755 /usr/local/sbin/x" in merged
    ents = pr.plg_entities(merged)
    assert (ents["version"], ents["md5"]) == ("2026.09.20", "a" * 32) and "/main/" in ents["pluginURL"]
    assert pr.split_plg(merged)[1] == [], "CHANGES is left for render"


def test_merge_manifest_keeps_an_installer_fix_made_on_our_side():
    merged = pr.merge_manifest(_manifest("beta", "2026.09.21.1"),
                               _manifest("beta", "2026.09.22.1", install="chmod 755 /usr/local/sbin/x"),
                               _manifest(version="2026.09.21", md5="c" * 32))
    assert "chmod 755 /usr/local/sbin/x" in merged
    assert pr.plg_entities(merged)["version"] == "2026.09.22.1" and "/beta/" in pr.plg_entities(merged)["pluginURL"]


def test_merge_manifest_refuses_both_branches_changing_the_same_installer_line(tmp_path):
    base, ours = _manifest(), _manifest(install="chmod 700 /usr/local/sbin/x")
    theirs = _manifest("beta", install="chmod 755 /usr/local/sbin/x")
    with pytest.raises(pr.ChangelogError):
        pr.merge_manifest(base, ours, theirs)
    files = []
    for name, text in (("base", base), ("ours", ours), ("theirs", theirs)):
        (tmp_path / name).write_text(text)
        files += [f"--{name}", tmp_path / name]
    assert run("merge-manifest", *files, "--out", tmp_path / "out") == 2


def test_last_beta_is_the_newest_beta_release(tmp_path, capsys):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2026.09.21\n\n- s\n\n## 2026.09.20.2 (beta)\n\n- b\n\n"
                         "## 2026.09.22.1 (beta)\n\n- newest\n\n## 2026.09.20.1 (beta)\n\n- a\n")
    assert run("last-beta", "--changelog", changelog) == 0
    assert capsys.readouterr().out.strip() == "2026.09.22.1"
    changelog.write_text("# Changelog\n\n## 2026.09.21\n\n- s\n")
    assert run("last-beta", "--changelog", changelog) == 0
    assert capsys.readouterr().out == ""


def test_stable_refresh_keeps_edits_and_adds_only_newer_betas(tmp_path):
    """Released beta notes are not shipped on the stable channel, so the stable PR's edits and deletions stand."""
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2026.09.22.1 (beta)\n\n- Four\n\n"
                         "## 2026.09.21.1 (beta)\n\n- One\n- Two\n- Three\n\n## 2026.09.19\n\n- Old\n")
    old = tmp_path / "old.md"
    old.write_text("# Changelog\n\n## Unreleased\n\n- One, reworded\n- Three\n\n## 2026.09.19\n\n- Old\n")
    run("seed", "--changelog", changelog, "--channel", "stable", "--carry-from", old,
        "--beta-sections", "--beta-after", "2026.09.21.1")
    assert pr.load_changelog(changelog).unreleased().bullets() == ["- One, reworded", "- Three", "- Four"]


def test_seed_skips_commits_that_only_edit_the_changelog(repo):
    sh = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    changelog = repo / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2026.09.04\n\n- Old\n")
    sh("add", "CHANGELOG.md")
    sh("commit", "-qm", "Update CHANGELOG.md")
    changelog.write_text(changelog.read_text() + "\n")
    (repo / "f").write_text("both")
    sh("commit", "-qam", "Plain change with notes")
    subjects = [b.split(" (")[0] for b in pr.commit_bullets(repo, "v2026.09.04", "HEAD", changelog)]
    assert "- Update CHANGELOG.md" not in subjects and "- Plain change with notes" in subjects


def test_heading_suffix_is_escaped_in_the_manifest(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\n## 2026.09.26 - Fixes & <tweaks>\n\n- x\n")
    content = pr.render_changes(pr.load_changelog(changelog), "stable")
    heading = next(ln for ln in content if ln.startswith("###"))
    assert heading == "###2026.09.26 - Fixes &amp; &lt;tweaks&gt;"
    assert pr.parse_changes(content).sections[0].rest == " - Fixes & <tweaks>"


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def decide_repo(tmp_path):
    """main: base commit, a merged release PR's merge commit, then one more push."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    shas = []
    for msg in ("base", "Merge pull request #7 from chodeus/release/stable", "later push"):
        (repo / "f").write_text(msg)
        _git(repo, "add", "f")
        _git(repo, "commit", "-qm", msg)
        shas.append(_git(repo, "rev-parse", "HEAD"))
    return repo, shas[1]


def _run_decide_state(tmp_path, repo, gh_body, mode="auto", ref_name="main", event=None, beta="beta", dry_run="false"):
    """Run the reusable's 'Decide channel and mode' step in a plugin checkout against a stub gh."""
    import yaml

    wf = yaml.safe_load((SCRIPTS.parent / ".github/workflows/unraid-plugin-release.yml").read_text())
    step = next(s for s in wf["jobs"]["release"]["steps"] if s.get("id") == "decide")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    (bindir / "gh").write_text("#!/bin/bash\n" + gh_body + "\n")
    (bindir / "gh").chmod(0o755)
    out = tmp_path / "out.txt"
    out.write_text("")
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "STABLE": "main", "BETA": beta, "MODE_IN": mode,
           "GITHUB_REF_NAME": ref_name, "GITHUB_OUTPUT": str(out), "GITHUB_REPOSITORY": "chodeus/plugin",
           "GITHUB_WORKFLOW_REF": "chodeus/plugin/.github/workflows/release.yml@refs/heads/main",
           "DISPATCH_TOKEN": "dispatch-token", "DRY_RUN": dry_run}
    env.pop("GITHUB_EVENT_NAME", None)
    if event:
        env["GITHUB_EVENT_NAME"] = event
    r = subprocess.run(["bash", "-c", step["run"]], cwd=repo, env=env, capture_output=True, text=True)
    return r, dict(line.split("=", 1) for line in out.read_text().splitlines())


def test_decide_releases_a_merged_release_pr_until_a_release_tag_contains_it(tmp_path, decide_repo):
    """A cancelled run must not lose the release: the next run sees the merge still has no release tag."""
    repo, merged = decide_repo
    gh = f"echo '2026-09-25T00:00:00Z 7 {merged}'"
    r, out = _run_decide_state(tmp_path, repo, gh)
    assert r.returncode == 0, r.stderr
    assert (out["channel"], out["mode"], out["pr_number"]) == ("stable", "release", "7")
    _git(repo, "tag", "test-2026.09.19.9101010", "HEAD")
    _git(repo, "tag", "pr-12-2026.09.19.0260925010101", "HEAD")
    assert _run_decide_state(tmp_path, repo, gh)[1]["mode"] == "release", "a test build's tag is not a release"
    _git(repo, "tag", "v2026.09.26", "HEAD")
    assert _run_decide_state(tmp_path, repo, gh)[1]["mode"] == "pr"


def test_decide_stops_on_a_release_tag_the_branch_does_not_have(tmp_path, decide_repo):
    """The cut moves the branch last: a tag the branch lacks is a cut that stopped part way, not a release."""
    repo, merged = decide_repo
    gh = f"echo '2026-09-25T00:00:00Z 7 {merged}'"

    def tag_off_the_branch(tag):
        _git(repo, "switch", "-q", "--detach", "main")
        _git(repo, "commit", "-q", "--allow-empty", "-m", f"chore(release): {tag}")
        _git(repo, "tag", tag)
        _git(repo, "switch", "-q", "main")

    tag_off_the_branch("v2026.09.26")
    r, out = _run_decide_state(tmp_path, repo, gh)
    assert r.returncode != 0 and "mode" not in out
    assert "gh release delete v2026.09.26 --cleanup-tag --yes" in r.stderr and "stopped part way" in r.stderr
    _git(repo, "merge", "-q", "--ff-only", "v2026.09.26")
    tag_off_the_branch("v2026.09.27")
    assert _run_decide_state(tmp_path, repo, gh)[1]["mode"] == "pr", "one release tag on the branch is enough"


def test_decide_releases_when_only_the_other_channels_tag_contains_the_merge(tmp_path, decide_repo):
    """main merged into beta before the stable cut ran: the beta release's tag contains the stable merge."""
    repo, merged = decide_repo
    _git(repo, "switch", "-q", "-c", "beta", "HEAD~2")
    _git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'main' into beta", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "Merge pull request #9 from chodeus/release/beta")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "chore(release): v2026.09.26 [skip ci]")
    _git(repo, "tag", "v2026.09.26")
    _git(repo, "switch", "-q", "main")
    r, out = _run_decide_state(tmp_path, repo, f"echo '2026-09-25T00:00:00Z 7 {merged}'")
    assert r.returncode == 0, r.stderr
    assert (out["mode"], out["pr_number"]) == ("release", "7")


def test_decide_refreshes_the_pr_when_no_release_pr_is_due(tmp_path, decide_repo):
    repo, _ = decide_repo
    assert _run_decide_state(tmp_path, repo, "echo ' '")[1]["mode"] == "pr"
    assert _run_decide_state(tmp_path, repo, "echo '2026-09-25T00:00:00Z 7 " + "f" * 40 + "'")[1]["mode"] == "pr"


def test_decide_stops_when_the_pr_lookup_fails(tmp_path, decide_repo):
    """Guessing "refresh" on an API error would silently skip a due release."""
    r, out = _run_decide_state(tmp_path, decide_repo[0], "echo 'HTTP 502' >&2; exit 1")
    assert r.returncode != 0 and "mode" not in out


def test_decide_restarts_the_other_channels_waiting_release(tmp_path, decide_repo):
    """A push to one branch can cancel the other branch's queued release run; the push's run starts it again."""
    repo, merged = decide_repo
    _git(repo, "tag", "v2026.09.26", "HEAD")
    _git(repo, "switch", "-q", "-c", "beta", "HEAD~2")
    (repo / "f").write_text("beta release PR merge")
    _git(repo, "commit", "-qam", "Merge pull request #9 from chodeus/release/beta")
    beta_merged = _git(repo, "rev-parse", "HEAD")
    _git(repo, "update-ref", "refs/remotes/origin/beta", beta_merged)
    _git(repo, "switch", "-q", "main")
    gh = ('case "$*" in\n  *"release/stable"*) echo "2026-09-25T00:00:00Z 7 ' + merged + '" ;;\n  *"release/beta"*) echo "2026-09-25T00:00:00Z 9 ' + beta_merged + '" ;;\n'
          '  *"workflow run"*) echo "$GH_TOKEN $*" >> dispatched ;;\nesac')
    r, out = _run_decide_state(tmp_path, repo, gh, event="push")
    assert r.returncode == 0, r.stderr
    assert (out["mode"], out["other_pr"]) == ("pr", "9")
    dispatched = (repo / "dispatched").read_text()
    assert dispatched.startswith("dispatch-token workflow run release.yml") and "--ref beta" in dispatched
    (repo / "dispatched").unlink()
    out = _run_decide_state(tmp_path, repo, gh, mode="release", event="workflow_dispatch")[1]
    assert out["other_pr"] == "9", "a release run still learns the other channel is waiting"
    assert not (repo / "dispatched").exists(), "a started run never starts another"
    assert _run_decide_state(tmp_path, repo, gh, event="push", beta="")[1]["other_pr"] == ""
    assert not (repo / "dispatched").exists(), "a single-channel plugin has no other channel"
    _run_decide_state(tmp_path, repo, gh, event="push", dry_run="true")
    assert not (repo / "dispatched").exists(), "a dry run never starts a release"


def test_decide_stops_when_the_other_channels_lookup_fails(tmp_path, decide_repo):
    repo, _ = decide_repo
    gh = 'case "$*" in\n  *"release/stable"*) echo " " ;;\n  *) echo "HTTP 502" >&2; exit 1 ;;\nesac'
    r, out = _run_decide_state(tmp_path, repo, gh, event="push")
    assert r.returncode != 0 and "mode" not in out


class Channels:
    """A bare origin with main and beta, driven through the real release scripts."""

    PLG = "p.plg"

    def __init__(self, tmp):
        self.tmp, self.origin, self.work, self.user = tmp, tmp / "origin.git", tmp / "work", tmp / "user"
        bindir = tmp / "bin"
        bindir.mkdir()
        (bindir / "gh").write_text('#!/bin/bash\necho "gh $*" >> "' + str(tmp / "gh.log") + '"\n')
        (bindir / "gh").chmod(0o755)
        ident = {f"GIT_{who}_{what}": val for who in ("AUTHOR", "COMMITTER") for what, val in (("NAME", "t"), ("EMAIL", "t@example.com"))}
        self.env = {**os.environ, **ident, "PATH": f"{bindir}:{os.environ['PATH']}", "OPS": str(SCRIPTS),
                    "PLG": self.PLG, "CHANGELOG": "CHANGELOG.md", "GIT_USER": "t", "GIT_EMAIL": "t@example.com",
                    "BETA_BRANCH": "beta", "DRY_RUN": "false"}
        for key in ("GH_TOKEN", "GITHUB_REPOSITORY"):
            self.env.pop(key, None)
        self.sh(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.sh(tmp, "clone", "-q", str(self.origin), str(self.user))
        self.sh(self.user, "switch", "-q", "-c", "main")
        (self.user / "CHANGELOG.md").write_text("# Changelog\n\n## 2026.09.19\n\n- Old stable\n")
        self.write_manifest("main", "stable")
        (self.user / "f").write_text("0")
        self.sh(self.user, "add", "-A")
        self.sh(self.user, "commit", "-qm", "chore: initial")
        self.sh(self.user, "tag", "v2026.09.19")
        self.sh(self.user, "push", "-q", "origin", "main", "--tags")
        self.sh(self.user, "switch", "-q", "-c", "beta")
        self.write_manifest("beta", "beta")
        self.sh(self.user, "commit", "-qam", "chore(beta): beta manifest")
        self.sh(self.user, "push", "-q", "origin", "beta")
        self.sh(tmp, "clone", "-q", str(self.origin), str(self.work))

    def sh(self, cwd, *args):
        return subprocess.run(["git", *args], cwd=cwd, env=self.env, check=True, capture_output=True, text=True).stdout

    def write_manifest(self, branch, channel):
        plg = self.user / self.PLG
        plg.write_text(_manifest(branch, "2026.09.19", "0123456789abcdef0123456789abcdef"))
        assert run("render", "--plg", plg, "--changelog", self.user / "CHANGELOG.md", "--channel", channel) == 0

    def on(self, branch):
        self.sh(self.user, "fetch", "-q", "origin", "--tags")
        self.sh(self.user, "switch", "-q", "-C", branch, f"origin/{branch}")

    def commit(self, branch, subject, path=None, edit=None):
        self.on(branch)
        target = self.user / (path or "src" / Path(re.sub(r"\W+", "-", subject)))
        target.parent.mkdir(exist_ok=True)
        target.write_text(edit(target.read_text()) if edit else subject)
        self.sh(self.user, "add", "-A")
        self.sh(self.user, "commit", "-qm", subject)
        self.sh(self.user, "push", "-q", "origin", branch)

    def release(self, branch, version, bullets, channel):
        """A release as the cut leaves it: a stamped section, CHANGES rendered, version set, tagged."""
        self.on(branch)
        cl = self.user / "CHANGELOG.md"
        head, rest = cl.read_text().split("\n## ", 1)
        marker = " (beta)" if channel == "beta" else ""
        cl.write_text(f"{head}\n## {version}{marker}\n\n{bullets}\n\n## {rest}")
        self.stamp_manifest(version, channel)

    def merge_pr(self, head, base):
        self.on(base)
        self.sh(self.user, "merge", "-q", "--no-ff", "-m", f"Merge pull request from {head}", f"origin/{head}")
        self.sh(self.user, "push", "-q", "origin", base)

    def cut(self, branch, version, channel):
        """What the release job does to the branch after a release PR merges, short of building and publishing."""
        self.on(branch)
        assert run("stamp", "--changelog", self.user / "CHANGELOG.md", "--version", version,
                   *(["--beta"] if channel == "beta" else [])) == 0
        self.stamp_manifest(version, channel)

    def stamp_manifest(self, version, channel):
        cl, plg = self.user / "CHANGELOG.md", self.user / self.PLG
        plg.write_text(re.sub(r'<!ENTITY version "[^"]*"', f'<!ENTITY version "{version}"', plg.read_text()))
        assert run("render", "--plg", plg, "--changelog", cl, "--channel", channel) == 0
        self.sh(self.user, "commit", "-qam", f"chore(release): v{version} [skip ci]")
        self.sh(self.user, "tag", f"v{version}")
        self.sh(self.user, "push", "-q", "origin", "HEAD", "--tags")

    def run(self, script, ok=True, **env):
        self.sh(self.work, "fetch", "-q", "origin", "--tags")
        self.sh(self.work, "reset", "-q", "--hard")
        self.sh(self.work, "checkout", "-q", "--detach", "origin/main")
        r = subprocess.run(["bash", str(SCRIPTS / script)], cwd=self.work, env={**self.env, **env},
                           capture_output=True, text=True)
        if not ok:
            return r
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    def show(self, ref, path):
        self.sh(self.user, "fetch", "-q", "origin")
        return self.sh(self.user, "show", f"origin/{ref}:{path}")

    def check(self, ref, channel, branch):
        """The release check against a branch as origin has it."""
        plg, cl = self.tmp / f"{channel}.plg", self.tmp / f"{channel}.md"
        plg.write_text(self.show(ref, self.PLG))
        cl.write_text(self.show(ref, "CHANGELOG.md"))
        return run("check", "--changelog", cl, "--plg", plg, "--channel", channel, "--branch", branch)

    def unreleased(self, ref):
        return pr.parse_changelog(self.show(ref, "CHANGELOG.md")).unreleased().bullets()

    def log(self, rng):
        return self.sh(self.user, "log", "--no-merges", "--format=%s", rng).splitlines()


@pytest.fixture
def channels(tmp_path):
    return Channels(tmp_path)


def test_stable_pr_carries_an_urgent_main_fix_after_a_stable_release(channels):
    """Once a beta release is in stable and merged back, beta is always ahead; that alone is no promotion."""
    channels.commit("beta", "fix: beta fix")
    channels.release("beta", "2026.09.21.1", "- Beta fix", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    channels.merge_pr("release/stable", "main")
    channels.cut("main", "2026.09.21", "stable")
    channels.run("plg_release_backmerge.sh", BASE="main", VERSION="2026.09.21")
    channels.commit("beta", "feat: unreleased beta work")
    channels.commit("main", "fix: urgent data-loss fix")
    out = channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    assert "nothing to release" not in out
    assert channels.unreleased("release/stable") == ["- fix: urgent data-loss fix"]
    assert "feat: unreleased beta work" not in channels.log("origin/main..origin/release/stable")
    assert channels.check("release/stable", "stable", "main") == 0


def test_stable_pr_promotes_the_beta_release_not_unreleased_beta_work(channels):
    channels.commit("beta", "fix: beta fix one")
    channels.release("beta", "2026.09.21.1", "- Beta fix one, reworded", "beta")
    channels.commit("beta", "feat: unreleased, untested beta work")
    channels.commit("main", "fix: urgent data-loss fix")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    assert channels.unreleased("release/stable") == ["- Beta fix one, reworded", "- fix: urgent data-loss fix"]
    brought = channels.log("origin/main..origin/release/stable")
    assert "fix: beta fix one" in brought and "feat: unreleased, untested beta work" not in brought


def test_stable_pr_lists_main_commits_already_in_the_beta_only_once(channels):
    """main merged into beta mid-cycle: the beta's notes describe those commits, so they are not seeded again."""
    channels.commit("main", "fix: fixed on main first")
    channels.on("beta")
    channels.sh(channels.user, "merge", "-q", "--no-ff", "-m", "Merge main into beta", "origin/main", "-X", "ours")
    channels.sh(channels.user, "push", "-q", "origin", "beta")
    channels.release("beta", "2026.09.21.1", "- The fix, described for users", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    assert channels.unreleased("release/stable") == ["- The fix, described for users"]


def test_stable_pr_edits_survive_the_next_beta_release(channels):
    channels.commit("beta", "fix: beta code change")
    channels.release("beta", "2026.09.21.1", "- One\n- Two\n- Three", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    channels.commit("release/stable", "Update CHANGELOG.md", "CHANGELOG.md",
                    lambda t: t.replace("- One\n", "- One, reworded\n", 1).replace("- Two\n", "", 1))
    channels.release("beta", "2026.09.22.1", "- Four", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    assert channels.unreleased("release/stable") == ["- One, reworded", "- Three", "- Four"]


def test_installer_changes_cross_between_the_channels(channels):
    channels.commit("beta", "fix: keep x executable after install", Channels.PLG,
                    lambda t: t.replace("chmod 644 /usr/local/sbin/x", "chmod 755 /usr/local/sbin/x"))
    channels.release("beta", "2026.09.21.1", "- x stays executable", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    stable = channels.show("release/stable", Channels.PLG)
    assert "chmod 755 /usr/local/sbin/x" in stable
    assert pr.plg_entities(stable)["version"] == "2026.09.19" and "/main/" in pr.plg_entities(stable)["pluginURL"]
    assert channels.check("release/stable", "stable", "main") == 0
    channels.commit("main", "fix: log the install", Channels.PLG, lambda t: t.replace("echo installed", "echo installed ok"))
    channels.release("main", "2026.09.22", "- Install logs", "stable")
    channels.run("plg_release_backmerge.sh", BASE="main", VERSION="2026.09.22")
    beta = channels.show("beta", Channels.PLG)
    assert "echo installed ok" in beta and "chmod 755 /usr/local/sbin/x" in beta
    assert pr.plg_entities(beta)["version"] == "2026.09.21.1" and "/beta/" in pr.plg_entities(beta)["pluginURL"]
    assert channels.check("beta", "beta", "beta") == 0


def test_refresh_guard_leaves_out_beta_work_a_stable_pr_has_no_record_of(channels):
    """A stable PR from the older refresh merged beta whole, with no Release-Synced-Beta trailer to say so."""
    channels.commit("beta", "feat: beta work")
    channels.on("main")
    channels.sh(channels.user, "switch", "-q", "-c", "release/stable")
    channels.sh(channels.user, "merge", "-q", "--no-ff", "-m", "Merge beta into release/stable", "origin/beta")
    channels.sh(channels.user, "push", "-q", "origin", "release/stable")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    channels.commit("release/stable", "fix: pushed to the release PR")
    r = channels.run("plg_release_pr.sh", ok=False, CHANNEL="stable", BASE="main")
    assert r.returncode == 1 and "src/fix-pushed-to-the-release-PR" in r.stdout, "a real edit still stops it"


def test_refresh_guard_leaves_out_the_promoted_beta_after_beta_is_rewritten(channels):
    """The promoted tag still marks its commits as beta work once beta itself no longer has them."""
    channels.release("beta", "2026.09.21.1", "- Beta notes", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")
    channels.sh(channels.user, "push", "-q", "--force", "origin", "origin/beta~1:refs/heads/beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main")


def test_release_scripts_leave_no_temporary_files(channels):
    """Each script keeps its files in one scratch directory and removes it when it exits."""
    scratch = channels.tmp / "scratch"
    scratch.mkdir()
    channels.release("beta", "2026.09.21.1", "- Beta notes", "beta")
    channels.run("plg_release_pr.sh", CHANNEL="stable", BASE="main", TMPDIR=str(scratch))
    channels.sh(channels.user, "fetch", "-q", "origin")
    assert "merge v2026.09.21.1 into main" in channels.sh(channels.user, "log", "--format=%s", "origin/main..origin/release/stable")
    channels.release("main", "2026.09.22", "- Stable notes", "stable")
    channels.run("plg_release_backmerge.sh", BASE="main", VERSION="2026.09.22", TMPDIR=str(scratch))
    assert list(scratch.iterdir()) == []


@pytest.mark.parametrize("script", ["plg_release_pr.sh", "plg_release_cut.sh", "plg_release_backmerge.sh"])
def test_every_release_script_removes_its_scratch_directory(script):
    body = (SCRIPTS / script).read_text()
    assert "SCRATCH=$(mktemp -d)\ntrap 'rm -rf \"$SCRATCH\"' EXIT\n" in body
    assert body.count("mktemp") == 1, "temporary files go in $SCRATCH"


def test_beta_pr_edits_survive_a_refresh(channels):
    channels.commit("beta", "fix: A thing")
    channels.commit("beta", "Plain subject B")
    channels.run("plg_release_pr.sh", CHANNEL="beta", BASE="beta")
    channels.commit("release/beta", "Update CHANGELOG.md", "CHANGELOG.md",
                    lambda t: re.sub(r"- Plain subject B \([0-9a-f]+\)\n", "",
                                     t.replace("- fix: A thing\n", "- A thing is fixed for every folder\n")))
    channels.commit("beta", "feat: C")
    channels.run("plg_release_pr.sh", CHANNEL="beta", BASE="beta")
    assert channels.unreleased("release/beta") == ["- A thing is fixed for every folder", "- feat: C"]


def test_a_notes_only_edit_is_not_a_bullet_after_the_back_merge(channels):
    channels.commit("main", "Update CHANGELOG.md", "CHANGELOG.md", lambda t: t + "\n")
    channels.release("main", "2026.09.20", "- Stable twenty", "stable")
    channels.run("plg_release_backmerge.sh", BASE="main", VERSION="2026.09.20")
    channels.commit("beta", "fix: beta work")
    channels.run("plg_release_pr.sh", CHANNEL="beta", BASE="beta")
    assert channels.unreleased("release/beta") == ["- fix: beta work"]


def test_refresh_stops_rather_than_drop_a_code_change_on_the_release_pr(channels):
    """The rebuild carries over only the notes; anything else pushed to the release PR must not vanish silently."""
    channels.commit("beta", "fix: A thing")
    channels.run("plg_release_pr.sh", CHANNEL="beta", BASE="beta")
    channels.commit("release/beta", "Update p.plg", Channels.PLG, lambda t: t.replace("echo installed", "echo installed fine"))
    channels.commit("beta", "feat: C")
    r = channels.run("plg_release_pr.sh", ok=False, CHANNEL="beta", BASE="beta")
    assert r.returncode != 0 and Channels.PLG in r.stdout + r.stderr
    assert "echo installed fine" in channels.show("release/beta", Channels.PLG), "the edit is still on the PR"


def test_merge_manifest_reports_a_failed_merge_file_as_a_failure():
    """merge-file exits 255 when it cannot run (binary input); that is not a clash between the branches."""
    ours = _manifest(install="chmod 644 /usr/local/sbin/x\0")
    with pytest.raises(pr.ChangelogError, match="git merge-file failed: .*binary"):
        pr.merge_manifest(_manifest(), ours, _manifest("beta"))


def test_merge_stops_when_git_refuses_to_merge(tmp_path):
    """An untracked file in the way makes git refuse without a conflict; nothing may be committed as if merged."""
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "p.plg").write_text(_manifest())
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 2026.09.19\n\n- Notes for 2026.09.19\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "switch", "-q", "-c", "other")
    (repo / "x").write_text("from other")
    _git(repo, "add", "x")
    _git(repo, "commit", "-qm", "add x")
    _git(repo, "switch", "-q", "main")
    (repo / "x").write_text("untracked, in the way")
    script = f'''set -euo pipefail
PLG=p.plg CHANGELOG=CHANGELOG.md PLGR="python3 {SCRIPTS}/plg_release.py" SCRATCH="{tmp_path}"
. "{SCRIPTS}/plg_release_merge.sh"
plg_merge other stable
'''
    r = subprocess.run(["bash", "-c", script], cwd=repo, capture_output=True, text=True)
    assert r.returncode != 0 and "could not merge other" in r.stdout + r.stderr


def test_decide_takes_the_newest_merged_release_pr_of_this_repository_from_every_page(tmp_path, decide_repo):
    """A fork's release/<channel> PR is somebody's PR; forks are filtered on the server, and every page is read."""
    repo, merged = decide_repo
    pages = [[{"number": 5, "merged_at": None, "merge_commit_sha": "0" * 40}],
             [{"number": 7, "merged_at": "2026-09-25T00:00:00Z", "merge_commit_sha": merged},
              {"number": 3, "merged_at": "2026-09-01T00:00:00Z", "merge_commit_sha": "1" * 40}]]
    gh = ('echo "$*" >> gh-args\nall=""\n'
          'while [ $# -gt 0 ]; do case "$1" in --jq) f="$2"; shift ;; --paginate) all=1 ;; esac; shift; done\n')
    for n, page in enumerate(pages):
        gh += ('[ -n "$all" ] || exit 0\n' if n else "") + f"jq -r \"$f\" <<'JSON'\n{json.dumps(page)}\nJSON\n"
    r, out = _run_decide_state(tmp_path, repo, gh)
    assert r.returncode == 0, r.stderr
    assert (out["mode"], out["pr_number"]) == ("release", "7")
    assert "head=chodeus:release/stable" in (repo / "gh-args").read_text()
