#!/usr/bin/env bash
# Cut a release on the current channel branch: stamp, render, build, commit, tag, GitHub release, verify.
# Env: CHANNEL BASE PLG CHANGELOG OPS GIT_USER GIT_EMAIL TZ DRY_RUN GH_TOKEN [PR_NUMBER] [BUILD_CMD]
set -euo pipefail

: "${CHANNEL:?}" "${BASE:?}" "${PLG:?}" "${CHANGELOG:?}" "${OPS:?}" "${GIT_USER:?}" "${GIT_EMAIL:?}" "${TZ:?}"
DRY_RUN="${DRY_RUN:-false}"
PR_NUMBER="${PR_NUMBER:-}"
read -ra BUILD <<< "${BUILD_CMD:-bash pkg_build.sh}"
PLGR="python3 $OPS/plg_release.py"

git config user.name "$GIT_USER"
git config user.email "$GIT_EMAIL"
git fetch --tags --force origin

version=$($PLGR next-version --channel "$CHANNEL" --tz "$TZ")
echo "release version: $version"

if [ "$CHANNEL" = stable ]; then
  $PLGR check --changelog "$CHANGELOG" --plg "$PLG" --channel stable --branch "$BASE" --require-nonempty --require-edited
else
  $PLGR check --changelog "$CHANGELOG" --plg "$PLG" --channel beta --branch "$BASE" --require-nonempty
  $PLGR check --changelog "$CHANGELOG" --plg "$PLG" --channel beta --branch "$BASE" --require-edited >/dev/null \
    || echo "::warning::beta notes still contain unedited seed bullets"
fi

beta_flag=()
[ "$CHANNEL" != beta ] || beta_flag=(--beta)
$PLGR stamp --changelog "$CHANGELOG" --version "$version" "${beta_flag[@]}"
$PLGR render --plg "$PLG" --changelog "$CHANGELOG" --channel "$CHANNEL"

rm -rf dist
# env -u GH_TOKEN: the plugin's own build script has no business seeing the release token.
env -u GH_TOKEN "${BUILD[@]}" --version "$version" --branch "$BASE"
mapfile -t txz < <(find dist -maxdepth 1 -name '*.txz' -type f)
[ "${#txz[@]}" -eq 1 ] || { echo "::error::expected exactly one dist/*.txz, found ${#txz[@]}"; exit 1; }
xmllint --noout "$PLG"
$PLGR check --changelog "$CHANGELOG" --plg "$PLG" --channel "$CHANNEL" --branch "$BASE"

notes=$(mktemp)
plugin_url=$($PLGR entity --plg "$PLG" --name pluginURL)
$PLGR notes --changelog "$CHANGELOG" --version "$version" --footer "Install / update URL: \`$plugin_url\`" > "$notes"

git add "$PLG" "$CHANGELOG"
git commit -q -m "chore(release): v$version [skip ci]"

# Before the dry-run exit: later steps consume this output on both paths.
echo "version=$version" >> "${GITHUB_OUTPUT:-/dev/null}"

if [ "$DRY_RUN" = true ]; then
  echo "::notice::dry run — would push $BASE, tag v$version and publish ${txz[0]}"
  git show --stat HEAD
  cat "$notes"
  exit 0
fi

# Publish and verify the package first; the served manifest on $BASE only moves once the asset is good.
git tag "v$version"
git push -q origin "v$version"

pre=()
[ "$CHANNEL" != beta ] || pre=(--prerelease)
gh release create "v$version" "${txz[0]}" --title "v$version" --notes-file "$notes" "${pre[@]}"

$PLGR check --changelog "$CHANGELOG" --plg "$PLG" --channel "$CHANNEL" --branch "$BASE" --verify-asset

if ! git push -q origin "HEAD:$BASE"; then
  echo "::error::$BASE moved during the release. Delete the release and tag (gh release delete v$version --cleanup-tag) and re-run."
  exit 1
fi

# Non-fatal: the release is already published and verified; a failed courtesy comment must not red the run.
if [ -n "$PR_NUMBER" ]; then
  url=$(gh release view "v$version" --json url --jq .url)
  gh pr comment "$PR_NUMBER" --body "Released as [v$version]($url)." >/dev/null \
    || echo "::warning::could not comment on PR #$PR_NUMBER"
fi
