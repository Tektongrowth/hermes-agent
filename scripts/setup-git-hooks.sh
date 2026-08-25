#!/usr/bin/env bash
set -euo pipefail
repo=$(git rev-parse --show-toplevel)
cd "$repo"
git config core.hooksPath .githooks
# Avoid auto-GC pack-refs transactions across unrelated shared-worktree branches.
git config gc.auto 0
for hook in .githooks/reference-transaction .githooks/pre-commit .githooks/pre-merge-commit .githooks/post-checkout .githooks/pre-rebase .githooks/pre-push .githooks/lib/active-branch-guard.sh scripts/setup-git-hooks.sh scripts/verify-active-branch.sh; do
  chmod +x "$hook"
done
printf 'Installed branch continuity hooks for %s\n' "$(sed -n '1p' .cutover/active-branch)"
