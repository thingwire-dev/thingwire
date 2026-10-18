#!/usr/bin/env bash
# Manage agent git worktrees under .claude/worktrees/. Safe by default:
# never removes a dirty worktree, unlocks before removing, keeps branch refs.
#
#   agent_worktree.sh create NAME BRANCH BASE   new branch BRANCH cut from BASE
#   agent_worktree.sh list
#   agent_worktree.sh remove NAME               refuses if dirty or has unpushed commits
#   agent_worktree.sh prune-merged [BASE]       remove clean worktrees whose branch is merged (ancestor of BASE or merged PR)
set -euo pipefail

# Always resolve from the main checkout, even when run inside a worktree.
COMMON="$(git rev-parse --path-format=absolute --git-common-dir)"
MAIN="$(dirname "$COMMON")"
DIR="$MAIN/.claude/worktrees"
EXCLUDE="$COMMON/info/exclude"

die() { echo "agent_worktree: $*" >&2; exit 1; }

ensure_exclude() {
  mkdir -p "$(dirname "$EXCLUDE")"
  touch "$EXCLUDE"
  grep -qxF '.claude/worktrees/' "$EXCLUDE" || echo '.claude/worktrees/' >> "$EXCLUDE"
}

is_dirty() { [ -n "$(git -C "$1" status --porcelain)" ]; }

remove_one() {
  local path="$1"
  [ -d "$path" ] || die "no such worktree: $path"
  if is_dirty "$path"; then
    echo "skip (dirty): $path" >&2
    return 1
  fi
  git worktree unlock "$path" 2>/dev/null || true
  git worktree remove "$path"
  echo "removed: $path (branch ref kept)"
}

cmd_create() {
  [ $# -eq 3 ] || die "usage: create NAME BRANCH BASE"
  local name="$1" branch="$2" base="$3"
  case "$name" in */*|""|.*) die "NAME must be a plain directory name" ;; esac
  ensure_exclude
  mkdir -p "$DIR"
  [ ! -e "$DIR/$name" ] || die "already exists: $DIR/$name"
  git fetch -q origin 2>/dev/null || echo "warning: fetch failed, using local refs" >&2
  git rev-parse --verify -q "$base^{commit}" >/dev/null || die "unknown base: $base"
  if git show-ref --verify -q "refs/heads/$branch"; then
    die "branch already exists: $branch (pick a new name, never share a branch)"
  fi
  git worktree add -b "$branch" "$DIR/$name" "$base"
  git -C "$DIR/$name" branch --unset-upstream 2>/dev/null || true
  echo "created: $DIR/$name on $branch from $base"
}

cmd_list() {
  git worktree list
}

cmd_remove() {
  [ $# -eq 1 ] || die "usage: remove NAME"
  local path="$DIR/$1"
  if [ -d "$path" ]; then
    local ahead
    ahead="$(git -C "$path" log --oneline '@{u}..HEAD' 2>/dev/null | wc -l | tr -d ' ')" || ahead=0
    if ! git -C "$path" rev-parse -q --verify '@{u}' >/dev/null 2>&1; then
      echo "note: branch has no upstream, commits exist only locally (ref is kept)" >&2
    elif [ "$ahead" != "0" ]; then
      die "$ahead unpushed commit(s) in $path; push first"
    fi
  fi
  remove_one "$path" || die "not removed (dirty): commit or discard changes yourself"
}

# Feature PRs are squash-merged, so ancestry alone misses them: also ask GitHub.
is_merged() {
  local branch="$1" base="$2"
  git merge-base --is-ancestor "$branch" "$base" 2>/dev/null && return 0
  command -v gh >/dev/null 2>&1 || return 1
  [ -n "$(gh pr list --head "$branch" --state merged --json number --jq '.[0].number' 2>/dev/null)" ]
}

cmd_prune_merged() {
  local base="${1:-origin/release/robot-stack-v0}"
  git fetch -q origin 2>/dev/null || true
  git worktree prune
  local path branch
  while IFS= read -r line; do
    case "$line" in
      "worktree "*) path="${line#worktree }" ;;
      "branch "*)
        branch="${line#branch refs/heads/}"
        case "$path" in "$DIR"/*) ;; *) continue ;; esac
        if is_merged "$branch" "$base"; then
          remove_one "$path" || true
        else
          echo "keep (not merged into $base): $path"
        fi
        ;;
    esac
  done < <(git worktree list --porcelain)
}

main() {
  [ $# -ge 1 ] || die "usage: create|list|remove|prune-merged"
  local sub="$1"; shift
  case "$sub" in
    create) cmd_create "$@" ;;
    list) cmd_list ;;
    remove) cmd_remove "$@" ;;
    prune-merged) cmd_prune_merged "$@" ;;
    *) die "unknown command: $sub" ;;
  esac
}
main "$@"
