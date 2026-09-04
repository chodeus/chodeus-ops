#!/usr/bin/env bash
# Rebuild release/<channel> from the channel branch and open/update its release PR.
# Env: CHANNEL BASE BETA_BRANCH PLG CHANGELOG OPS GIT_USER GIT_EMAIL DRY_RUN GH_TOKEN
set -euo pipefail

: "${CHANNEL:?}" "${BASE:?}" "${PLG:?}" "${CHANGELOG:?}" "${OPS:?}" "${GIT_USER:?}" "${GIT_EMAIL:?}"
BETA_BRANCH="${BETA_BRANCH:-}"
DRY_RUN="${DRY_RUN:-false}"
RB="release/$CHANNEL"
PLGR="python3 $OPS/plg_release.py"

. "$OPS/plg_release_git.sh"
plg_git_setup
git fetch --no-tags origin "+refs/heads/$BASE:refs/remotes/origin/$BASE"
git fetch --no-tags origin "+refs/heads/$RB:refs/remotes/origin/$RB" 2>/dev/null || true
[ -z "$BETA_BRANCH" ] || git fetch --no-tags origin "+refs/heads/$BETA_BRANCH:refs/remotes/origin/$BETA_BRANCH"
git fetch --tags origin

OLD_SHA=$(git rev-parse -q --verify "origin/$RB" || true)
OLD_SYNC=""
OLD_CL=""
if [ -n "$OLD_SHA" ]; then
  OLD_SYNC=$(git log -1 --format=%B "origin/$RB" | sed -n 's/^Release-Synced: //p' | head -1)
  OLD_CL=$(mktemp)
  git show "origin/$RB:$CHANGELOG" > "$OLD_CL" 2>/dev/null || OLD_CL=""
fi

git switch -q -C "$RB" "origin/$BASE"

# Promotion PR: merge beta into the stable channel, keeping the stable branch's manifest.
promote=false
if [ "$CHANNEL" = stable ] && [ -n "$BETA_BRANCH" ] && ! git merge-base --is-ancestor "origin/$BETA_BRANCH" HEAD; then
  promote=true
  if ! git merge --no-ff --no-commit "origin/$BETA_BRANCH" >/dev/null; then
    for f in $(git diff --name-only --diff-filter=U); do
      case "$f" in
        "$PLG") git checkout "origin/$BASE" -- "$PLG" ;;
        "$CHANGELOG") git checkout "origin/$BASE" -- "$CHANGELOG" ;;
        *) echo "::error::merge conflict in $f — merge $BETA_BRANCH into $BASE by hand, then push $BASE"; exit 1 ;;
      esac
    done
  fi
  git checkout "origin/$BASE" -- "$PLG"
  theirs=$(mktemp)
  git show "origin/$BETA_BRANCH:$CHANGELOG" > "$theirs"
  $PLGR merge-changelog --ours "$CHANGELOG" --theirs "$theirs" --out "$CHANGELOG"
  git add "$PLG" "$CHANGELOG"
  git commit -q -m "chore(release): merge $BETA_BRANCH into $BASE for the next stable"
fi

# Seed: promotion PRs reuse the beta notes; everything else reads commit subjects since the last sync.
seed_args=()
if [ "$promote" = true ]; then
  seed_args=(--beta-sections)
else
  since="$OLD_SYNC"
  if [ -z "$since" ] || ! git merge-base --is-ancestor "$since" HEAD 2>/dev/null; then
    last=$($PLGR last-version --changelog "$CHANGELOG" --channel "$CHANNEL")
    if git rev-parse -q --verify "refs/tags/v$last" >/dev/null; then
      since="v$last"
    else
      since=$(git describe --tags --abbrev=0 --match 'v*' 2>/dev/null || true)
    fi
  fi
  seed_args=(--since "$since")
fi
carry=()
[ -z "$OLD_CL" ] || carry=(--carry-from "$OLD_CL")
$PLGR seed --changelog "$CHANGELOG" "${carry[@]}" "${seed_args[@]}"

# Capture first: piping straight into grep -c would turn a notes failure into "nothing to release".
notes=$($PLGR notes --changelog "$CHANGELOG" --version Unreleased)
count=$(printf '%s\n' "$notes" | grep -c '^- ' || true)
if [ "$count" -eq 0 ] && [ -z "$OLD_SHA" ]; then
  echo "::notice::nothing to release on $BASE — no release PR opened"
  exit 0
fi

# --allow-empty: an emptied Unreleased section still has to record the Release-Synced trailer.
git add "$CHANGELOG"
git commit -q --allow-empty -m "chore($BASE): release changelog" -m "Release-Synced: $(git rev-parse "origin/$BASE")"

if [ "$DRY_RUN" = true ]; then
  echo "::notice::dry run — would push $RB and open/update the release PR"
  git log --oneline "origin/$BASE..HEAD"
  git diff "origin/$BASE" -- "$CHANGELOG"
  exit 0
fi

if [ -n "$OLD_SHA" ]; then
  git push -q --force-with-lease="refs/heads/$RB:$OLD_SHA" origin "$RB"
else
  git push -q origin "$RB"
fi

body=$(mktemp)
{
  echo "Merge this PR to cut the next **$CHANNEL** release. Edit the Unreleased section of \`$CHANGELOG\` on this branch first; the release job stamps the version, renders it into the plugin manifest, and attaches the package."
  echo
  echo "Raw seed bullets (\`- fix: ...\`, or a subject ending in a commit sha) must be rewritten before a stable release."
  [ "$promote" = false ] || echo "This PR also merges \`$BETA_BRANCH\` into \`$BASE\` — merge it with a merge commit, not a squash."
  echo
  echo "## Unreleased"
  echo
  printf '%s\n' "$notes"
} > "$body"

# No labels: the release gate keys off the release/<channel> branch name, and labels would
# need Issues:write on RELEASE_TOKEN for a purely cosmetic marker.
pr=$(gh pr list --head "$RB" --base "$BASE" --state open --json number --jq '.[0].number // empty')
if [ -z "$pr" ]; then
  gh pr create --head "$RB" --base "$BASE" --title "chore($BASE): release" --body-file "$body"
else
  gh pr edit "$pr" --body-file "$body" >/dev/null
  echo "updated release PR #$pr"
fi
