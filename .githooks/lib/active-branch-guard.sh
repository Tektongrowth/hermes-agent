#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel)
LOCK_FILE="$REPO_ROOT/.cutover/active-branch"
APPROVER_FILE="$REPO_ROOT/.cutover/approver"

[[ -f "$LOCK_FILE" ]] || { echo "Blocked: missing .cutover/active-branch context lock." >&2; exit 1; }
[[ -f "$APPROVER_FILE" ]] || { echo "Blocked: missing .cutover/approver." >&2; exit 1; }
IFS= read -r ACTIVE_BRANCH < "$LOCK_FILE"
IFS= read -r REQUIRED_APPROVER < "$APPROVER_FILE"
[[ -n "$ACTIVE_BRANCH" ]] || { echo "Blocked: .cutover/active-branch is empty." >&2; exit 1; }
[[ -n "$REQUIRED_APPROVER" ]] || { echo "Blocked: .cutover/approver is empty." >&2; exit 1; }

context_change_is_approved() {
  [[ "${TEKTON_BRANCH_CONTEXT_APPROVED_BY:-}" == "$REQUIRED_APPROVER" ]] \
    && [[ -n "${TEKTON_BRANCH_CONTEXT_APPROVAL_REF:-}" ]]
}

require_active_branch() {
  local action=${1:-operation}
  local current
  current=$(git symbolic-ref --quiet --short HEAD || true)
  if [[ "$current" == "$ACTIVE_BRANCH" ]] || context_change_is_approved; then
    return 0
  fi
  cat >&2 <<MESSAGE
Blocked: $action is outside the locked working branch.
Locked branch: $ACTIVE_BRANCH
Current branch: ${current:-detached HEAD}

Do not create, switch, commit, merge, rebase, or push another branch because context is unclear.
A branch-context change requires exact approval and both values:
  TEKTON_BRANCH_CONTEXT_APPROVED_BY=$REQUIRED_APPROVER
  TEKTON_BRANCH_CONTEXT_APPROVAL_REF=<Discord message URL or TaskTracker approval reference>
MESSAGE
  return 1
}

require_ref_allowed() {
  local ref=$1
  local action=${2:-reference update}
  [[ "$ref" == refs/heads/* ]] || return 0
  local candidate=${ref#refs/heads/}
  if [[ "$candidate" == "$ACTIVE_BRANCH" ]] || context_change_is_approved; then
    return 0
  fi
  echo "Blocked: $action attempted unauthorized branch '$candidate'; locked branch is '$ACTIVE_BRANCH'." >&2
  return 1
}
