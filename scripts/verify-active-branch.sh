#!/usr/bin/env bash
set -euo pipefail
REPO_ROOT=$(git rev-parse --show-toplevel)
source "$REPO_ROOT/.githooks/lib/active-branch-guard.sh"
require_active_branch "context verification"
local_sha=$(git rev-parse HEAD)
remote_ref="origin/$ACTIVE_BRANCH"
if git show-ref --verify --quiet "refs/remotes/$remote_ref"; then
  remote_sha=$(git rev-parse "$remote_ref")
  if [[ "$local_sha" == "$remote_sha" ]]; then
    remote_state="remote synchronized"
  elif git merge-base --is-ancestor "$remote_sha" "$local_sha"; then
    ahead_count=$(git rev-list --count "$remote_sha..$local_sha")
    remote_state="local ahead of remote by $ahead_count verified commit(s)"
  else
    echo "Blocked: local HEAD is behind or diverged from $remote_ref." >&2
    echo "Local:  $local_sha" >&2
    echo "Remote: $remote_sha" >&2
    exit 1
  fi
else
  remote_state="remote branch not created yet"
fi
if [[ -n "$(git status --short)" ]]; then
  echo "Context lock verified on $ACTIVE_BRANCH at $local_sha; $remote_state; worktree has uncommitted changes."
else
  echo "Context lock verified on $ACTIVE_BRANCH at $local_sha; $remote_state; worktree clean."
fi
