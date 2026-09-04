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

After updating, hard-refresh your browser &amp; clear cache &lt;now&gt;. See &name; docs.

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
    assert "browser & clear cache <now>. See &name; docs." in text
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
    script = f"""
set -euo pipefail
cd {tmp_path}
. {SCRIPTS}/plg_release_git.sh
plg_git_setup
grep -c 'SEKRIT' .git/config >> {probe} || true   # before: token attached
plg_git_deauth
grep -c 'SEKRIT' .git/config >> {probe} || true   # during the build: must be 0
plg_git_auth
grep -c 'SEKRIT' .git/config >> {probe} || true   # after: reattached for the push
"""
    env = {**os.environ, "GH_TOKEN": "SEKRIT", "GITHUB_REPOSITORY": "chodeus/plugin",
           "GIT_USER": "chodeus", "GIT_EMAIL": "c@example.com"}
    subprocess.run(["bash", "-c", script], check=True, env=env, capture_output=True)
    before, during, after = probe.read_text().split()
    assert (before, during, after) == ("1", "0", "1"), probe.read_text()


def test_ops_ref_parsing_handles_quoted_and_commented_callers():
    """The ref the reusable checks out comes from the caller's own `uses:` line."""
    workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/unraid-plugin-release.yml").read_text()
    m = re.search(r"grep -oE '(?P<pat>chodeus/chodeus-ops[^']+)'", workflow)
    assert m, "ops-ref grep pattern not found — the test is pinned to the workflow"
    pattern = re.compile(m.group("pat").replace("\\.", r"\."))
    stem = "uses: %schodeus/chodeus-ops/.github/workflows/unraid-plugin-release.yml@%s%s"
    cases = {
        stem % ("", "main", ""): "main",
        stem % ('"', "abc1234", '"'): "abc1234",
        stem % ("'", "v1.2", "'"): "v1.2",
        stem % ("", "abc1234", " # v1.2"): "abc1234",
        stem % ("", "release/2026", "\r"): "release/2026",
    }
    for line, want in cases.items():
        found = pattern.search(line)
        assert found, line
        assert found.group(0).split("@", 1)[1] == want, line


def test_cut_script_brackets_the_build_with_deauth_and_auth():
    """Ordering guard: a refactor must not reopen the window the test above closes."""
    body = (SCRIPTS / "plg_release_cut.sh").read_text()
    deauth, build, auth = (body.index(t) for t in ("plg_git_deauth", '"${BUILD[@]}"', "plg_git_auth\n"))
    assert deauth < build < auth


def test_declared_entity_survives_but_bare_ampersand_is_escaped():
    assert pr._xml_escape("See &name; and &#124; and Tom & Jerry") == "See &name; and &#124; and Tom &amp; Jerry"
    assert pr._xml_escape("a & b") == "a &amp; b"


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
    assert run("seed", "--changelog", changelog, "--since", "v-does-not-exist", "--repo", tmp_path) == 2
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
    run("seed", "--changelog", changelog, "--carry-from", old, "--since", "v2026.09.04", "--repo", repo)
    body = pr.load_changelog(changelog).unreleased().bullets()
    assert body[0] == "- Folder order is kept when labels disagree (#63)"
    assert "- feat: new thing" in body and "- fix(ui): keep folder order (#63)" in body
    run("seed", "--changelog", changelog, "--since", "v2026.09.04", "--repo", repo)
    assert pr.load_changelog(changelog).unreleased().bullets() == body


def test_seed_from_beta_sections_stops_at_last_stable(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## 2026.09.06.2 (beta)\n\n- Newer beta\n\n## 2026.09.06.1 (beta)\n\n- Older beta\n- Shared\n\n"
        "## 2026.09.01\n\n- Stable\n\n## 2026.08.30.1 (beta)\n\n- Ancient beta\n"
    )
    run("seed", "--changelog", changelog, "--beta-sections")
    assert pr.load_changelog(changelog).unreleased().bullets() == ["- Older beta", "- Shared", "- Newer beta"]


def test_next_version_shares_counter_across_channels(repo):
    sh = lambda *a: subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)  # noqa: E731
    assert pr.next_version(repo, "stable", "2026.09.05") == "2026.09.05"
    assert pr.next_version(repo, "beta", "2026.09.05") == "2026.09.05.1"
    sh("tag", "v2026.09.05.1")
    assert pr.next_version(repo, "beta", "2026.09.05") == "2026.09.05.2"
    assert pr.next_version(repo, "stable", "2026.09.05") == "2026.09.05.2"
    sh("tag", "v2026.09.05.2")
    sh("tag", "v2026.09.05")
    assert pr.next_version(repo, "stable", "2026.09.05") == "2026.09.05.3"
    assert pr.next_version(repo, "stable", "2026.09.04") == "2026.09.04.1"


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
