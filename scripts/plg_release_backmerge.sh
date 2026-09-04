#!/usr/bin/env bash
# After a stable release: merge the stable branch into beta, keeping beta's manifest, and re-render its CHANGES.
# Env: BASE BETA_BRANCH PLG CHANGELOG OPS GIT_USER GIT_EMAIL VERSION DRY_RUN
set -euo pipefail

: "${BASE:?}" "${BETA_BRANCH:?}" "${PLG:?}" "${CHANGELOG:?}" "${OPS:?}" "${GIT_USER:?}" "${GIT_EMAIL:?}" "${VERSION:?}"
DRY_RUN="${DRY_RUN:-false}"
PLGR="python3 $OPS/plg_release.py"

git config user.name "$GIT_USER"
git config user.email "$GIT_EMAIL"
git fetch --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE" "+refs/heads/$BETA_BRANCH:refs/remotes/origin/$BETA_BRANCH"
git switch -q -C "$BETA_BRANCH" "origin/$BETA_BRANCH"

if ! git merge --no-ff --no-commit "origin/$BASE" >/dev/null; then
  for f in $(git diff --name-only --diff-filter=U); do
    case "$f" in
      "$PLG"|"$CHANGELOG") git checkout "origin/$BETA_BRANCH" -- "$f" ;;
      *) echo "::error::merge conflict in $f — merge $BASE into $BETA_BRANCH by hand"; exit 1 ;;
    esac
  done
fi
git checkout "origin/$BETA_BRANCH" -- "$PLG"
theirs=$(mktemp)
git show "origin/$BASE:$CHANGELOG" > "$theirs"
$PLGR merge-changelog --ours "$CHANGELOG" --theirs "$theirs" --out "$CHANGELOG"
$PLGR render --plg "$PLG" --changelog "$CHANGELOG" --channel beta
$PLGR check --changelog "$CHANGELOG" --plg "$PLG" --channel beta --branch "$BETA_BRANCH"

git add "$PLG" "$CHANGELOG"
git commit -q -m "chore(release): merge $BASE into $BETA_BRANCH after v$VERSION [skip ci]"

if [ "$DRY_RUN" = true ]; then
  echo "::notice::dry run — would push $BETA_BRANCH"
  git show --stat HEAD
  exit 0
fi
git push -q origin "HEAD:$BETA_BRANCH"
