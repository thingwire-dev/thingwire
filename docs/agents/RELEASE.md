# Release process

```
feature branches --squash--> release/<name> --merge commit--> main --tag--> vX.Y.Z
```

## 1. Cut the release branch

Done by the owner or orchestrator, from an up-to-date main:

```bash
git fetch origin && git switch -c release/<name> origin/main && git push -u origin release/<name>
```

Name it for the goal, not the date (`release/robot-stack-v0`). Update `docs/agents/TASKS.md`.
Agents branch from `origin/release/<name>` (`make worktree ... BASE=origin/release/<name>`).

## 2. Keep it green

- Feature PRs target the release branch and are squash-merged after CI and review.
- CI runs on PRs into `release/**` and on every push to it. A red release branch stops all merges
  until fixed; the fix is a `fix/` PR, nothing else lands first.
- Merge main into the release branch (merge commit, no rebase) when main moves, so the final PR
  stays clean. Resolve conflicts in a `chore/sync-<name>` PR.
- Shared-file PRs land one at a time and the rest rebase after them (`AGENTS.md`, section 3).
- Contract PRs (`docs/contracts.md`) land before the code that depends on them.

## 3. Release PR

Open ONE PR `release/<name>` to `main` titled `release: <name>`. Body: list of merged feature PRs
(from TASKS.md), contract changes, safety impact, hardware or firmware flash notes, rollback.
Requirements: CI green, code owner review (CODEOWNERS), the owner reads the safety, identity and
`.github/` diffs. Merge with a **merge commit** (not squash, so feature history and bisect survive).

## 4. Tag

On main after the merge, by the owner only (publishing is an escalation item):

```bash
git switch main && git pull --ff-only && git tag -a vX.Y.Z -m "release: <name>" && git push origin vX.Y.Z
```

Bump the version in `pyproject.toml` in a normal PR before the release PR (shared file, needs label).
Then run the worktree sweep (`make worktree-clean`) and delete the merged release branch.

## 5. Hotfixes

For a defect on main that cannot wait for the next release:
1. `hotfix/<slug>` from `main`, smallest possible fix plus a regression test.
2. PR to `main`, full CI, code owner review, merge commit, tag a patch version.
3. Merge main into the active release branch the same day so the fix is not lost.
Hotfix branches may touch any path but shared files still need the `shared-file-ok` label.

## 6. Branch protection (human runs these once)

Agents must not run them. Update the check names if CI job names change. Requires
admin rights on thingwire-dev/thingwire.

```bash
# main: PR required, CI required, CODEOWNERS review required, no force push, no deletion
gh api -X PUT repos/thingwire-dev/thingwire/branches/main/protection --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["pytest", "ruff", "mypy", "dashes", "firmware (esp32s3)", "firmware (xiao_esp32s3_deskbot)"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "require_code_owner_reviews": true,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false
}
JSON

# release/**: PR required plus CI (including ownership). Rulesets cover the wildcard.
gh api -X POST repos/thingwire-dev/thingwire/rulesets --input - <<'JSON'
{
  "name": "release branches",
  "target": "branch",
  "enforcement": "active",
  "conditions": {"ref_name": {"include": ["refs/heads/release/**"], "exclude": []}},
  "rules": [
    {"type": "deletion"},
    {"type": "non_fast_forward"},
    {"type": "pull_request", "parameters": {
      "required_approving_review_count": 0,
      "dismiss_stale_reviews_on_push": false,
      "require_code_owner_review": false,
      "require_last_push_approval": false,
      "required_review_thread_resolution": false}},
    {"type": "required_status_checks", "parameters": {
      "strict_required_status_checks_policy": false,
      "required_status_checks": [
        {"context": "pytest"}, {"context": "ruff"}, {"context": "mypy"},
        {"context": "dashes"}, {"context": "ownership"},
        {"context": "firmware (esp32s3)"}, {"context": "firmware (xiao_esp32s3_deskbot)"}
      ]}}
  ]
}
JSON
```

Notes: `ownership` is not required on main because it only runs on PRs from agent branches that
have rules; the release PR branch (`release/*`) is skipped by design, so requiring it on main
would still pass, add it if you prefer. Squash-only for feature PRs is a repo setting
(Settings, General, Pull Requests); the release PR needs "Allow merge commits" on. Labels
`shared-file-ok` must exist: `gh label create shared-file-ok --color FBCA04 --description "owner approved a shared-file edit"`.
