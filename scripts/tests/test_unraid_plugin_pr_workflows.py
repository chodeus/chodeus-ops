import base64
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github/workflows"
BASE_VERSION = "2026.09.10"
BUILD_VERSION = f"{BASE_VERSION}.0260911123456"
HEAD_SHA = "a" * 40
PLUGIN_URL = "https://raw.githubusercontent.com/chodeus/plugin/main/plugin.plg"
BASE_PLG = f'''<!DOCTYPE PLUGIN [
<!ENTITY version "{BASE_VERSION}">
]>
<PLUGIN version="&version;" pluginURL="{PLUGIN_URL}"/>
'''


def _workflow(name):
    return yaml.safe_load((WORKFLOWS / name).read_text())


def _step(workflow, job, name):
    return next(step for step in workflow["jobs"][job]["steps"] if step["name"] == name)


def _write_executable(path, body):
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def command_dir(tmp_path):
    """Portable stand-ins for commands supplied by a GitHub-hosted runner."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_executable(
        bindir / "gh",
        """#!/usr/bin/env python3
import json
import os
import sys

args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
joined = "\\0".join(args)
for rule in json.loads(os.environ.get("FAKE_GH_RULES", "[]")):
    if all(part in joined for part in rule.get("contains", [])):
        sys.stdout.write(rule.get("stdout", ""))
        sys.stderr.write(rule.get("stderr", ""))
        raise SystemExit(rule.get("status", 0))
sys.stderr.write("unexpected gh call: " + repr(args) + "\\n")
raise SystemExit(97)
""",
    )
    _write_executable(
        bindir / "xmllint",
        """#!/usr/bin/env python3
import sys
import xml.etree.ElementTree as ET

args = sys.argv[1:]
if args[0] == "--noout":
    ET.parse(args[1])
    raise SystemExit(0)
if args[0] != "--xpath":
    raise SystemExit("unsupported xmllint arguments")
expression, filename = args[1:]
root = ET.parse(filename).getroot()
values = {
    "string(/PLUGIN/@version)": root.get("version", ""),
    "string(/PLUGIN/@pluginURL)": root.get("pluginURL", ""),
    "count(/PLUGIN/FILE/URL)": str(len(root.findall("./FILE/URL"))),
    "string(/PLUGIN/FILE/URL)": root.findtext("./FILE/URL", ""),
    "string(/PLUGIN/FILE[URL]/MD5)": root.findtext("./FILE/MD5", ""),
}
if expression not in values:
    raise SystemExit("unsupported xpath: " + expression)
sys.stdout.write(values[expression])
""",
    )
    return bindir


def _rules(*rules):
    return json.dumps(rules)


def _run_step(tmp_path, command_dir, workflow, job, name, env=None):
    output = tmp_path / "github-output"
    output.write_text("")
    log = tmp_path / "gh.log"
    log.write_text("")
    run_env = {
        **os.environ,
        "PATH": f"{command_dir}:{os.environ['PATH']}",
        "GITHUB_OUTPUT": str(output),
        "GITHUB_REPOSITORY": "chodeus/plugin",
        "FAKE_GH": str(command_dir / "gh"),
        "FAKE_GH_LOG": str(log),
        "FAKE_GH_RULES": "[]",
    }
    run_env.update(env or {})
    result = subprocess.run(
        [
            "bash",
            "-c",
            'gh() { "$FAKE_GH" "$@"; }\n' + _step(_workflow(workflow), job, name)["run"],
        ],
        cwd=tmp_path,
        env=run_env,
        capture_output=True,
        text=True,
    )
    return result, output.read_text(), [json.loads(line) for line in log.read_text().splitlines()]


def _fake_date(command_dir, value="260911123456"):
    _write_executable(
        command_dir / "date",
        f'''#!/usr/bin/env bash
[[ "$*" == "-u +%y%m%d%H%M%S" ]] || exit 98
printf '%s\\n' '{value}'
''',
    )


def _encoded(text):
    return base64.b64encode(text.encode()).decode() + "\n"


def _built_manifest(version=BUILD_VERSION, plugin_url=PLUGIN_URL, package_url=None, md5=None, extra_url=False):
    package_url = package_url or (
        f"https://github.com/chodeus/plugin/releases/download/pr-42-{version}/plugin.txz"
    )
    md5 = md5 or hashlib.md5(b"package bytes").hexdigest()
    second = "<URL>https://example.invalid/extra.txz</URL>" if extra_url else ""
    return (
        f'<PLUGIN version="{version}" pluginURL="{plugin_url}">'
        f"<FILE><URL>{package_url}</URL>{second}<MD5>{md5}</MD5></FILE></PLUGIN>"
    )


def test_pr_build_versions_above_the_base_release(tmp_path, command_dir):
    _fake_date(command_dir)
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    result, output, _ = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-build.yml",
        "build",
        "Version the build just above the base branch's release",
        {
            "PR": "42",
            "BASE": "release/v1.2",
            "PLG": "plugin.plg",
            "HEAD_SHA": HEAD_SHA,
            "RUNNER_TEMP": str(runner_temp),
            "FAKE_GH_RULES": _rules(
                {"contains": ["contents/plugin.plg?ref=release/v1.2"], "stdout": _encoded(BASE_PLG)}
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    assert output == f"version={BUILD_VERSION}\n"
    assert f"pull request #42 at {HEAD_SHA[:7]}" in result.stdout


@pytest.mark.parametrize(
    "pr,base,message",
    [
        ("forty-two", "main", "pr_number must be a number"),
        ("42", "main; touch owned", "base_branch must be a plain branch name"),
    ],
)
def test_pr_build_rejects_untrusted_identifiers(tmp_path, command_dir, pr, base, message):
    result, output, calls = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-build.yml",
        "build",
        "Version the build just above the base branch's release",
        {"PR": pr, "BASE": base, "PLG": "plugin.plg", "HEAD_SHA": HEAD_SHA, "RUNNER_TEMP": str(tmp_path)},
    )
    assert result.returncode == 1
    assert message in result.stdout + result.stderr
    assert output == "" and calls == []


def test_pr_build_rejects_an_unexpected_base_version(tmp_path, command_dir):
    runner_temp = tmp_path / "runner"
    runner_temp.mkdir()
    bad_manifest = BASE_PLG.replace(BASE_VERSION, "v1;unsafe")
    result, _, _ = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-build.yml",
        "build",
        "Version the build just above the base branch's release",
        {
            "PR": "42",
            "BASE": "main",
            "PLG": "plugin.plg",
            "HEAD_SHA": HEAD_SHA,
            "RUNNER_TEMP": str(runner_temp),
            "FAKE_GH_RULES": _rules({"contains": ["contents/plugin.plg"], "stdout": _encoded(bad_manifest)}),
        },
    )
    assert result.returncode == 1
    assert "unexpected version 'v1;unsafe'" in result.stdout + result.stderr


def test_pr_build_passes_exact_build_arguments_and_requires_one_package(tmp_path, command_dir):
    build = tmp_path / "build.sh"
    _write_executable(
        build,
        """#!/usr/bin/env bash
printf '%s\\n' "$@" > args
mkdir -p dist
printf package > dist/plugin.txz
""",
    )
    env = {"BUILD_CMD": "bash build.sh --quiet", "VERSION": BUILD_VERSION, "BASE": "main"}
    result, _, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-build.yml", "build", "Build the package", env
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "args").read_text().splitlines() == [
        "--quiet", "--version", BUILD_VERSION, "--branch", "main"
    ]

    (tmp_path / "dist/extra.txz").write_text("extra")
    result, _, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-build.yml", "build", "Build the package", env
    )
    assert result.returncode == 1
    assert "expected exactly one dist/*.txz, found 2" in result.stdout + result.stderr


def test_pr_build_rewrites_manifest_and_records_artifact_identity(tmp_path, command_dir):
    (tmp_path / "dist").mkdir()
    (tmp_path / "plugin.plg").write_text(
        BASE_PLG.replace("/>", "><FILE><URL>https://github.com/chodeus/plugin/releases/download/v&version;/plugin.txz</URL></FILE></PLUGIN>")
    )
    env = {"PR": "42", "VERSION": BUILD_VERSION, "HEAD_SHA": HEAD_SHA, "BASE": "main", "PLG": "plugin.plg"}
    result, _, _ = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-build.yml",
        "build",
        "Point the manifest at the pull request release",
        env,
    )
    assert result.returncode == 0, result.stderr
    assert f"/releases/download/pr-42-{BUILD_VERSION}/" in (tmp_path / "dist/plugin.plg").read_text()
    assert (tmp_path / "dist/build.env").read_text() == (
        f"pr=42\nversion={BUILD_VERSION}\nhead_sha={HEAD_SHA}\nbase=main\n"
    )


def test_pr_build_fails_if_the_manifest_does_not_use_the_version_entity_url(tmp_path, command_dir):
    (tmp_path / "dist").mkdir()
    (tmp_path / "plugin.plg").write_text('<PLUGIN><FILE><URL>https://example.invalid/plugin.txz</URL></FILE></PLUGIN>')
    result, _, _ = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-build.yml",
        "build",
        "Point the manifest at the pull request release",
        {"PR": "42", "VERSION": BUILD_VERSION, "HEAD_SHA": HEAD_SHA, "BASE": "main", "PLG": "plugin.plg"},
    )
    assert result.returncode == 1
    assert "package URL was not rewritten" in result.stdout + result.stderr
    assert not (tmp_path / "dist/build.env").exists()


def test_publish_finds_special_character_branch_without_building_a_query(tmp_path, command_dir):
    result, output, calls = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-publish.yml",
        "publish",
        "Find the labelled pull request this build belongs to",
        {
            "RUN_ID": "123",
            "LABEL": "test-build",
            "HEAD": "alice:feature/a&b#+c",
            "HEAD_SHA": HEAD_SHA,
            "FAKE_GH_RULES": _rules(
                {"contains": ["-f", "head=alice:feature/a&b#+c"], "stdout": "42\n"},
                {"contains": ["pulls/42", ".labels[].name"], "stdout": "other\ntest-build\n"},
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    assert output == "number=42\n"
    assert "head=alice:feature/a&b#+c" in calls[0]


@pytest.mark.parametrize(
    "rules,message",
    [
        ((_rules({"contains": ["pulls"], "stdout": "null\n"})), "No open pull request"),
        (
            _rules(
                {"contains": ["-f", "head=alice:feature"], "stdout": "42\n"},
                {"contains": ["pulls/42", ".labels[].name"], "stdout": "needs-review\n"},
            ),
            "does not carry the test-build label",
        ),
    ],
)
def test_publish_stops_cleanly_without_an_authorized_pull_request(tmp_path, command_dir, rules, message):
    result, output, _ = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-publish.yml",
        "publish",
        "Find the labelled pull request this build belongs to",
        {"RUN_ID": "123", "LABEL": "test-build", "HEAD": "alice:feature", "HEAD_SHA": HEAD_SHA,
         "FAKE_GH_RULES": rules},
    )
    assert result.returncode == 0, result.stderr
    assert output == ""
    assert message in result.stdout


@pytest.mark.parametrize("key,value,message", [
    ("RUN_ID", "12;bad", "run_id must be a number"),
    ("LABEL", "test build", "label must be a plain name"),
])
def test_publish_rejects_untrusted_workflow_inputs(tmp_path, command_dir, key, value, message):
    env = {"RUN_ID": "123", "LABEL": "test-build", "HEAD": "alice:feature", "HEAD_SHA": HEAD_SHA}
    env[key] = value
    result, output, calls = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-publish.yml", "publish",
        "Find the labelled pull request this build belongs to", env
    )
    assert result.returncode == 1
    assert message in result.stdout + result.stderr
    assert output == "" and calls == []


def _run_metadata_check(tmp_path, command_dir, case="valid"):
    dist = tmp_path / "dist"
    dist.mkdir()
    package = b"package bytes"
    (dist / "plugin.txz").write_bytes(package)
    values = {"pr": "42", "version": BUILD_VERSION, "head_sha": HEAD_SHA, "base": "main"}
    manifest_args = {}
    if case in values:
        values[case] = {"pr": "41", "version": f"{BASE_VERSION}.0123", "head_sha": "b" * 40,
                        "base": "develop"}[case]
    elif case == "manifest-version":
        manifest_args["version"] = BUILD_VERSION + "1"
    elif case == "package-url":
        manifest_args["package_url"] = "https://example.invalid/plugin.txz"
    elif case == "extra-url":
        manifest_args["extra_url"] = True
    elif case == "md5":
        manifest_args["md5"] = "0" * 32
    elif case == "plugin-url":
        manifest_args["plugin_url"] = "https://attacker.invalid/plugin.plg"
    elif case == "extra-package":
        (dist / "extra.txz").write_bytes(b"extra")
    (dist / "build.env").write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    (dist / "plugin.plg").write_text(_built_manifest(**manifest_args))
    return _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-publish.yml",
        "publish",
        "Check the build against the pull request",
        {
            "PR": "42",
            "HEAD_SHA": HEAD_SHA,
            "PLG_PATH": "plugin.plg",
            "FAKE_GH_RULES": _rules(
                {"contains": ["pulls/42", ".base.ref"], "stdout": "main\n"},
                {"contains": ["contents/plugin.plg?ref=main"], "stdout": _encoded(BASE_PLG)},
            ),
        },
    )


def test_publish_accepts_an_artifact_bound_to_the_pull_request(tmp_path, command_dir):
    result, output, _ = _run_metadata_check(tmp_path, command_dir)
    assert result.returncode == 0, result.stderr
    assert f"version={BUILD_VERSION}" in output
    assert f"tag=pr-42-{BUILD_VERSION}" in output
    assert "plg=dist/plugin.plg" in output and "txz=dist/plugin.txz" in output
    assert f"md5={hashlib.md5(b'package bytes').hexdigest()}" in output


@pytest.mark.parametrize(
    "case,message",
    [
        ("version", "is not '2026.09.10.0<build time>'"),
        ("pr", "belongs to another pull request"),
        ("base", "made against another base branch"),
        ("head_sha", "build is not from"),
        ("manifest-version", "manifest's version is not"),
        ("package-url", "manifest must have one download"),
        ("extra-url", "manifest must have one download"),
        ("md5", "manifest's package MD5 is not"),
        ("plugin-url", "differs from the base branch"),
        ("extra-package", "expected one .plg and one .txz"),
    ],
)
def test_publish_rejects_tampered_or_stale_artifacts(tmp_path, command_dir, case, message):
    result, _, _ = _run_metadata_check(tmp_path, command_dir, case)
    assert result.returncode == 1
    assert message in result.stdout + result.stderr


@pytest.mark.parametrize("already_exists", [False, True])
def test_publish_creates_a_prerelease_once_and_keeps_reruns_idempotent(tmp_path, command_dir, already_exists):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "plugin.plg").write_text("manifest")
    (dist / "plugin.txz").write_text("package")
    tag = f"pr-42-{BUILD_VERSION}"
    rules = [
        {"contains": ["release", "view", tag], "status": 0 if already_exists else 1},
        {"contains": ["release", "create", tag]},
    ]
    result, output, calls = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-publish.yml", "publish", "Publish the pre-release",
        {
            "PR": "42", "TAG": tag, "VERSION": BUILD_VERSION, "PLG_FILE": "dist/plugin.plg",
            "TXZ_FILE": "dist/plugin.txz", "DEFAULT_BRANCH": "main", "HEAD_SHA": HEAD_SHA,
            "FAKE_GH_RULES": _rules(*rules),
        },
    )
    assert result.returncode == 0, result.stderr
    assert f"install_url=https://github.com/chodeus/plugin/releases/download/{tag}/plugin.plg" in output
    creates = [call for call in calls if call[:2] == ["release", "create"]]
    if already_exists:
        assert creates == []
        assert "created=true" not in output
        assert "verifying it instead" in result.stdout
    else:
        assert output.startswith("created=true\n")
        assert len(creates) == 1
        tag_index = creates[0].index(tag)
        assert creates[0][tag_index:tag_index + 4] == [
            tag, "dist/plugin.txz", "dist/plugin.plg", "--target"
        ]
        assert "main" in creates[0] and "--prerelease" in creates[0]


@pytest.mark.parametrize("served_package,expected_status", [(b"package bytes", 0), (b"tampered", 1)])
def test_publish_verifies_the_assets_a_server_fetches(tmp_path, command_dir, served_package, expected_status):
    built_manifest = tmp_path / "plugin.plg"
    built_package = tmp_path / "plugin.txz"
    served_manifest = tmp_path / "served.plg"
    served_txz = tmp_path / "served.txz"
    built_manifest.write_text("manifest")
    built_package.write_bytes(b"package bytes")
    served_manifest.write_text("manifest")
    served_txz.write_bytes(served_package)
    _write_executable(
        command_dir / "curl",
        """#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then out=$2; shift 2; continue; fi
  url=$1
  shift
done
case "$url" in
  *.plg) cp "$SERVED_PLG" "$out" ;;
  *.txz) cp "$SERVED_TXZ" "$out" ;;
  *) exit 2 ;;
esac
""",
    )
    want = hashlib.md5(b"package bytes").hexdigest()
    result, _, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-publish.yml", "publish", "Verify what a server will fetch",
        {
            "INSTALL_URL": "https://example.invalid/plugin.plg", "PLG_FILE": str(built_manifest),
            "TXZ_FILE": str(built_package), "WANT": want, "SERVED_PLG": str(served_manifest),
            "SERVED_TXZ": str(served_txz),
        },
    )
    assert result.returncode == expected_status
    if expected_status:
        assert "served package md5" in result.stdout + result.stderr
    else:
        assert "install URL" in result.stdout


def test_cleanup_removes_all_listed_builds_and_updates_the_existing_comment(tmp_path, command_dir):
    result, _, calls = _run_step(
        tmp_path,
        command_dir,
        "unraid-plugin-pr-cleanup.yml",
        "cleanup",
        "Remove the pull request's test builds and update the note",
        {
            "PR": "42",
            "MARKER": "<!-- pr-build -->",
            "FAKE_GH_RULES": _rules(
                {"contains": ["--paginate", "repos/chodeus/plugin/releases"], "stdout": "pr-42-one\npr-42-two\n"},
                {"contains": ["release", "delete", "pr-42-one"]},
                {"contains": ["release", "delete", "pr-42-two"], "stderr": "release not found\n", "status": 1},
                {"contains": ["issues/42/comments"], "stdout": "99\n"},
                {"contains": ["issues/comments/99", "PATCH"]},
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    deleted = [call for call in calls if call[:2] == ["release", "delete"]]
    assert [call[call.index("--repo") + 2] for call in deleted] == ["pr-42-one", "pr-42-two"]
    assert any(
        any("issues/comments/99" in arg for arg in call)
        and any("body=<!-- pr-build -->\nThe test build" in arg for arg in call)
        for call in calls
    )


def test_cleanup_updates_the_comment_even_when_no_builds_remain(tmp_path, command_dir):
    result, _, calls = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-cleanup.yml", "cleanup",
        "Remove the pull request's test builds and update the note",
        {
            "PR": "42", "MARKER": "<!-- pr-build -->", "FAKE_GH_RULES": _rules(
                {"contains": ["repos/chodeus/plugin/releases"], "stdout": ""},
                {"contains": ["issues/42/comments"], "stdout": "99\n"},
                {"contains": ["issues/comments/99", "PATCH"]},
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "No test build for #42" in result.stdout
    assert not any(call[:2] == ["release", "delete"] for call in calls)
    assert any(any("issues/comments/99" in arg for arg in call) for call in calls)


@pytest.mark.parametrize(
    "rules,message",
    [
        (_rules({"contains": ["repos/chodeus/plugin/releases"], "stderr": "API unavailable\n", "status": 1}),
         "API unavailable"),
        (_rules(
            {"contains": ["repos/chodeus/plugin/releases"], "stdout": "pr-42-one\n"},
            {"contains": ["release", "delete"], "stderr": "permission denied\n", "status": 1},
        ), "permission denied"),
    ],
)
def test_cleanup_surfaces_listing_and_deletion_failures(tmp_path, command_dir, rules, message):
    result, _, calls = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-cleanup.yml", "cleanup",
        "Remove the pull request's test builds and update the note",
        {"PR": "42", "MARKER": "<!-- pr-build -->", "FAKE_GH_RULES": rules},
    )
    assert result.returncode != 0
    assert message in result.stdout + result.stderr
    assert not any("comments" in " ".join(call) for call in calls)


def test_cleanup_rejects_a_non_numeric_pull_request_before_api_calls(tmp_path, command_dir):
    result, _, calls = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-cleanup.yml", "cleanup",
        "Remove the pull request's test builds and update the note",
        {"PR": "42;bad", "MARKER": "<!-- pr-build -->"},
    )
    assert result.returncode == 1
    assert "pr_number must be a number" in result.stdout + result.stderr
    assert calls == []


@pytest.mark.parametrize("stderr,status", [("release not found\n", 0), ("permission denied\n", 1)])
def test_unverified_release_removal_is_only_idempotent_for_not_found(tmp_path, command_dir, stderr, status):
    result, _, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-publish.yml", "publish", "Remove the unverified build",
        {"TAG": f"pr-42-{BUILD_VERSION}", "FAKE_GH_RULES": _rules(
            {"contains": ["release", "delete"], "stderr": stderr, "status": 1}
        )},
    )
    assert result.returncode == status


@pytest.mark.parametrize("closed,verified,phrase", [
    ("true", "true", "has been removed"),
    ("false", "true", "next real release supersedes it"),
    ("false", "false", "was not published"),
])
def test_publish_comment_reports_each_terminal_outcome(tmp_path, command_dir, closed, verified, phrase):
    result, _, calls = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-publish.yml", "publish", "Update the pull request comment",
        {
            "PR": "42", "VERSION": BUILD_VERSION, "INSTALL_URL": "https://example.invalid/plugin.plg",
            "VERIFIED": verified, "CLOSED": closed, "MARKER": "<!-- pr-build -->", "LABEL": "test-build",
            "HEAD_SHA": HEAD_SHA, "RUN_ID": "100", "GITHUB_RUN_ID": "101",
            "FAKE_GH_RULES": _rules(
                {"contains": ["issues/42/comments?per_page=100"], "stdout": "99\n"},
                {"contains": ["issues/comments/99", "PATCH"]},
            ),
        },
    )
    assert result.returncode == 0, result.stderr
    patch = next(call for call in calls if any("issues/comments/99" in arg for arg in call))
    assert phrase in next(arg for arg in patch if arg.startswith("body="))


@pytest.mark.parametrize("stderr,status", [("gh: Not Found (HTTP 404)\n", 0), ("gh: forbidden (HTTP 403)\n", 1)])
def test_label_removal_only_ignores_an_already_absent_label(tmp_path, command_dir, stderr, status):
    result, _, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-pr-publish.yml", "publish",
        "Take the label off so the next build needs a new go",
        {"PR": "42", "LABEL": "test-build", "FAKE_GH_RULES": _rules(
            {"contains": ["labels/test-build", "DELETE"], "stderr": stderr, "status": 1}
        )},
    )
    assert result.returncode == status
    if status == 0:
        assert "label was already off" in result.stdout


def test_manual_test_build_derives_version_from_the_manifest(tmp_path, command_dir):
    _fake_date(command_dir)
    (tmp_path / "plugin.plg").write_text(BASE_PLG)
    result, output, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-test-build.yml", "test-build", "Pick the version",
        {"VERSION_IN": "", "TAG": "test", "PLG": "plugin.plg"},
    )
    assert result.returncode == 0, result.stderr
    assert output == f"version={BUILD_VERSION}\n"


@pytest.mark.parametrize("version", ["1.2.3", "2026.9.10", "2026.09.10-rc1", "2026.09.10;bad"])
def test_manual_test_build_rejects_unsafe_or_malformed_versions(tmp_path, command_dir, version):
    result, output, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-test-build.yml", "test-build", "Pick the version",
        {"VERSION_IN": version, "TAG": "test", "PLG": "plugin.plg"},
    )
    assert result.returncode == 1
    assert "version must start with YYYY.MM.DD" in result.stdout + result.stderr
    assert output == ""


def test_manual_test_build_accepts_a_safe_explicit_version_and_rejects_an_unsafe_tag(tmp_path, command_dir):
    env = {"VERSION_IN": "2026.09.10beta.2", "TAG": "test_build-1", "PLG": "plugin.plg"}
    result, output, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-test-build.yml", "test-build", "Pick the version", env
    )
    assert result.returncode == 0 and output == "version=2026.09.10beta.2\n"
    env["TAG"] = "test/build"
    result, output, _ = _run_step(
        tmp_path, command_dir, "unraid-plugin-test-build.yml", "test-build", "Pick the version", env
    )
    assert result.returncode == 1
    assert "release_tag must be a plain tag name" in result.stdout + result.stderr
    assert output == ""


def test_privileged_publish_never_checks_out_pr_code_and_cleans_only_after_verification():
    publish = _workflow("unraid-plugin-pr-publish.yml")
    job = publish["jobs"]["publish"]
    assert job["permissions"] == {"contents": "write", "pull-requests": "write", "actions": "read"}
    assert not any("checkout" in step.get("uses", "") for step in job["steps"])
    names = [step["name"] for step in job["steps"]]
    assert names.index("Verify what a server will fetch") < names.index("Remove older builds of this pull request")
    removal = _step(publish, "publish", "Remove the unverified build")
    assert "steps.verify.outcome == 'failure'" in removal["if"]
    assert "steps.publish.outputs.created == 'true'" in removal["if"]
    cleanup = _step(publish, "publish", "Remove older builds of this pull request")["run"]
    assert ".prerelease" in cleanup and '.tag_name != \\"$TAG\\"' in cleanup
    assert 'startswith(\\"pr-$PR-\\")' in cleanup


def test_pr_build_and_callers_keep_untrusted_code_read_only_and_pin_reusables():
    build = _workflow("unraid-plugin-pr-build.yml")
    assert build["permissions"] == {"contents": "read"}
    assert build["jobs"]["build"]["permissions"] == {"contents": "read"}
    checkout = _step(build, "build", "Checkout the pull request head")
    assert checkout["with"]["persist-credentials"] is False

    expected_ref = "3e8986e0bbed0a74436a2862ec2a1ed206d4a3de"
    callers = {
        "caller-unraid-plugin-pr-build.yml": ("pull_request", ["labeled"]),
        "caller-unraid-plugin-pr-cleanup.yml": ("pull_request_target", ["closed"]),
        "caller-unraid-plugin-pr-publish.yml": ("workflow_run", ["completed"]),
    }
    for filename, (event, event_types) in callers.items():
        caller = yaml.safe_load((ROOT / "templates" / filename).read_text())
        trigger = caller.get("on", caller.get(True))[event]
        assert trigger["types"] == event_types
        called_job = next(iter(caller["jobs"].values()))
        assert called_job["uses"].endswith("@" + expected_ref)

    pr_caller = yaml.safe_load((ROOT / "templates/caller-unraid-plugin-pr-build.yml").read_text())
    assert pr_caller["jobs"]["build"]["if"] == "github.event.label.name == 'test-build'"
    assert pr_caller["jobs"]["build"]["with"] == {
        "plg_file": "PLUGIN.plg",
        "pr_number": "${{ github.event.pull_request.number }}",
        "head_sha": "${{ github.event.pull_request.head.sha }}",
        "base_branch": "${{ github.event.pull_request.base.ref }}",
    }


def test_manual_test_build_removes_failed_and_old_builds_after_verify_only():
    workflow = _workflow("unraid-plugin-test-build.yml")
    steps = workflow["jobs"]["test-build"]["steps"]
    names = [step["name"] for step in steps]
    verify = _step(workflow, "test-build", "Verify what a server will fetch")
    failed = _step(workflow, "test-build", "Remove the unverified build")
    assert verify["id"] == "verify"
    assert names.index("Verify what a server will fetch") < names.index("Remove the unverified build")
    assert names.index("Remove the unverified build") < names.index("Remove older test builds")
    assert "steps.verify.outcome == 'failure'" in failed["if"]
