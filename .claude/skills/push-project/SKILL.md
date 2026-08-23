---
name: push-project
description: "Autonomous builder — selects a ready project via the registry, plans from the brief, implements chunks, reviews independently, self-merges per chunk, and records everything; `/push-project [project-id] [--force-named]`."
---

# push-project

Turn one approved project brief into independently reviewed, squash-merged chunks. Follow the phases in order. Accept an optional project id and `--force-named` only as `/push-project [project-id] [--force-named]`.

## Constants from `registry build env --json`

1. Run `registry build env --json` before all other CLI calls.
2. Set `HOST`, `REGISTRY_ROOT`, `REGISTRY_CLI`, `CODEX_BIN`, `WORKDIR_BASE`, and `TTL` from `host`, `registry_root`, `registry_cli`, `codex_bin`, `workdir_base`, and `ttl_seconds`.
3. Treat a null `CODEX_BIN` as an instruction to implement chunks directly.

The environment decides; never hardcode paths.

## Phase 0 — preflight

1. Run `git -C "$REGISTRY_ROOT" pull --ff-only`.
2. Run `$REGISTRY_CLI build start --host "$HOST" [--project <id>] [--force-named] --json`. Include only invocation arguments actually supplied.
3. For `lease_held`, `registry_dirty`, `stopped`, or `no_candidate`, print the registry's reason and stop; the attempt is already journaled.
4. Only when the output contains reconciliation `actions`: execute each under the guard —
   - `close_pr`: `gh pr close <n> --repo <repo>`.
   - `delete_branch`: from a checkout of `<repo>`, run `git push origin --delete <branch>`.
   — then run `$REGISTRY_CLI build reconcile --done --json` and repeat step 2 exactly once.
5. Save `RUN_ID` from `run_id`; `PROJECT`, `REPO`, the brief, automation policy, and contract expectations from `candidate`; and `DRY_RUN`, `BUDGET` (`chunks_per_run`, `minutes_per_run`), and `ALLOW` from `lease`.

## Phase 1 — clone

1. Set `WORKDIR="$WORKDIR_BASE/$RUN_ID"`.
2. Run `gh repo clone "$REPO" "$WORKDIR" -- --depth 50`.
3. Read README, `CLAUDE.md` or `AGENTS.md`, `docs/SPEC.md`, `.project-meta.yaml`, and `git log --oneline -15` when present.
4. Never read `data/understanding/*`.
5. Optionally run `$REGISTRY_CLI sync --repo "$REPO"` only to refresh PR listing data.
6. Every git/gh command in Phases 2–4 runs inside `$WORKDIR` unless it names `$REGISTRY_ROOT`.

## Phase 2 — contract

1. When `.project-meta.yaml` is absent, make the first chunk `bootstrap_contract`, class `contract`:
   - Discover setup, test, lint, typecheck, and verify commands from CI workflows, Makefile, `pyproject.toml`, `package.json`, and agent instructions.
   - Run every discovered command and prove it works.
   - Write `.project-meta.yaml` to the Phase 0 contract expectations.
   - Require `$REGISTRY_CLI validate-contract .project-meta.yaml --project-id "$PROJECT"` to pass.
   - Create its branch and `chunk_started` event as in Phase 4b, implement it directly, and ship it through Phase 4d–g. Contract and `plan` chunks have no SPEC checkbox to tick; give the reviewer a synthetic item instead (title, acceptance — e.g. "validate-contract passes", `Classes: contract` or `plan`).
   - Record `$REGISTRY_CLI build event "$RUN_ID" contract_bootstrapped --chunk-id bootstrap_contract`.
2. When the contract is invalid, or its baseline `test` command has a collection or command error, allow one `repair_contract` chunk through the same path and run `$REGISTRY_CLI build event "$RUN_ID" contract_repaired --chunk-id repair_contract`.
3. On a second contract failure, run `$REGISTRY_CLI build finish "$RUN_ID" --outcome contract_broken --summary "<reason>" --json` and proceed to Phase 5 finalization.
4. If `services` is non-empty, or `network.allowed` is false while tests require network, finish `contract_broken` with the reason.

## Phase 3 — baseline

1. On the clean clone, run contract `setup`, then `test`, then declared `lint`, `typecheck`, and `verify` commands.
2. On green, run `$REGISTRY_CLI build event "$RUN_ID" verify_passed --detail baseline=green`.
3. On red, run `$REGISTRY_CLI build event "$RUN_ID" verify_failed --detail baseline=red`, then attribute it:
   - Collection or command error: return once to Phase 2 for `repair_contract`.
   - Genuine test failures: save the counts/output, continue with **no NEW failures** as the bar, and flag the red baseline in every PR body.
   - Unattributable: finish with outcome `baseline_red` and proceed to Phase 5.

## Phase 4 — chunk loop

Before each new chunk, stop when `BUDGET.chunks_per_run` is reached, elapsed minutes are at least `BUDGET.minutes_per_run`, `$REGISTRY_ROOT/data/build/STOP` exists, or the control plane returns a stop outcome.

### a. Plan

1. Run `$REGISTRY_CLI validate-spec "$PROJECT" docs/SPEC.md --json`; treat a missing file as no ready item.
2. If `next_ready_index` is null, invoke the `build-planner` subagent with the Agent tool, `subagent_type: build-planner`, and fresh context. Supply the Phase 0 brief and automation policy JSON, current SPEC text or `absent`, gathered clone facts, and the complete validation report.
3. Handle the planner response:
   - `needs_intent`: run `$REGISTRY_CLI propose "$PROJECT" --set brief.open_decisions='<json>' --rationale "<reason>"`, then `$REGISTRY_CLI build event "$RUN_ID" needs_intent --reason "<reason>"`, finish `needs_intent`, and stop the loop. Permit only `brief.*` proposal paths.
   - `roadmap_done`: finish `roadmap_done` and stop the loop.
   - Full SPEC text: write `docs/SPEC.md` and rerun validation. If no item is ready, make one more fresh planner attempt; if still none is ready, finish `aborted` with summary `planner_no_ready_item`.
4. Treat a planner rewrite as a `plan` chunk: create its branch/event as in b, implement directly, and ship through d–g. Skip tests and checkbox ticking for this plan-only diff; still invoke the reviewer. Then continue the loop.

### b. Select

1. Select the item at `next_ready_index` and set `CHUNK_ID` to its index.
2. Set `SLUG` to a kebab-case form of its title, at most 40 characters.
3. Set `BRANCH="push/$RUN_ID-$CHUNK_ID-$SLUG"` and run `git checkout -b "$BRANCH"`.
4. Run `$REGISTRY_CLI build event "$RUN_ID" chunk_started --chunk-id "$CHUNK_ID" --detail branch="$BRANCH"`.

### c. Implement

1. Implement `plan`, `contract`, UI, user-facing copy, and API/SDK-shape work directly.
2. Otherwise write a precise task spec containing files, behavior, acceptance criteria, tests to add, and verified imports, local modules, and environment facts.
3. Run Codex synchronously in the foreground with the Bash timeout at maximum and the task spec on stdin:
   - Mac: `$CODEX_BIN exec --cd "$WORKDIR" --sandbox workspace-write --label "push-$PROJECT-$CHUNK_ID" --prompt -`.
   - PC: `$CODEX_BIN exec --cd "$WORKDIR" --sandbox workspace-write -`.
4. Never background Codex or end the turn while it runs. Retry one invocation or login error once; then finish `codex_unavailable`. When `CODEX_BIN` is null, implement directly to the same standard.

### d. Verify

1. Run contract `test`, plus declared `lint`, `typecheck`, and `verify` commands. Require no new failures versus baseline and explain changed counts.
2. Read the entire diff. Reject any contract `forbidden_paths` match.
3. Confirm every change class is declared by the item's `Classes` and allowed by `ALLOW`.
4. Tick the selected SPEC checkbox in the same diff.
5. On failure, fix once and repeat verification. On a second failure, run:
   - `$REGISTRY_CLI build event "$RUN_ID" verify_failed --chunk-id "$CHUNK_ID" --reason verify`.
   - `git checkout main && git branch -D "$BRANCH"`.
   - `$REGISTRY_CLI build event "$RUN_ID" chunk_rejected --chunk-id "$CHUNK_ID" --reason verify`.
   - Continue; let the circuit breaker decide whether to pause.
6. On success, run `$REGISTRY_CLI build event "$RUN_ID" verify_passed --chunk-id "$CHUNK_ID"`.

### e. Open PR

1. Commit the diff and run `git push -u origin "$BRANCH"`.
2. Run `gh pr create --head "$BRANCH" --title "<item title>" --body "<item text, acceptance, before/after tests, red-baseline flag if any, run $RUN_ID, chunk $CHUNK_ID>"`; save its URL and number.
3. Run `$REGISTRY_CLI build event "$RUN_ID" pr_opened --chunk-id "$CHUNK_ID" --pr-url "$PR_URL" --detail branch="$BRANCH" --detail pr_number="$PR_NUMBER"`.

### f. Review

1. Invoke the `build-reviewer` subagent in fresh, read-only context. Supply the brief, exact SPEC item, `git diff main...$BRANCH`, before/after test output, and `.project-meta.yaml`.
2. Save only its JSON as `$WORKDIR/../review-$RUN_ID-$CHUNK_ID.json`.
3. Run `$REGISTRY_CLI build event "$RUN_ID" review_verdict --chunk-id "$CHUNK_ID" --detail-json "$(cat "$WORKDIR/../review-$RUN_ID-$CHUNK_ID.json")"`.
4. On `request_changes`, address the findings once, repeat d, push, and invoke a fresh review.
5. On `reject` or a second non-approve, run `gh pr close "$PR_NUMBER" --repo "$REPO"`, then `git push origin --delete "$BRANCH"`, run `$REGISTRY_CLI build event "$RUN_ID" chunk_rejected --chunk-id "$CHUNK_ID" --reason review`, and continue.

### g. Merge

1. If `DRY_RUN` is true, leave the PR open, record `$REGISTRY_CLI build event "$RUN_ID" chunk_skipped --chunk-id "$CHUNK_ID" --reason shadow`, and continue.
2. Otherwise run `gh pr merge "$PR_NUMBER" --squash --delete-branch`.
3. Run `SHA=$(gh pr view "$PR_NUMBER" --json mergeCommit -q .mergeCommit.oid)`.
4. Run `git fetch origin main && git tag "checkpoint/$RUN_ID-$CHUNK_ID" "$SHA" && git push origin "checkpoint/$RUN_ID-$CHUNK_ID"`.
5. Run `$REGISTRY_CLI build event "$RUN_ID" merged --chunk-id "$CHUNK_ID" --pr-url "$PR_URL" --tag "checkpoint/$RUN_ID-$CHUNK_ID" --detail merge_sha="$SHA"`.
6. Run `git checkout main && git pull --ff-only`. On conflict, re-clone before the next chunk.

## Phase 5 — finish

1. Choose the registry-defined outcome: `completed`, `budget_exhausted`, `roadmap_done`, `needs_intent`, `blocked_by_policy`, `shadow_completed`, `aborted`, or the applicable earlier stop outcome.
2. Unless already finished, run `$REGISTRY_CLI build finish "$RUN_ID" --outcome <outcome> --summary "<one line>" --json`; save the digest path.
3. Run `$REGISTRY_CLI dashboard`.
4. Run `cd "$REGISTRY_ROOT" && git add data/build DASHBOARD.md && git commit -m "build: $RUN_ID $PROJECT <outcome>" && git push origin main`.
5. On non-fast-forward, run `git pull --rebase` once and push again. If it still fails, leave it for the next run's reconciliation.
6. Load PushNotification through ToolSearch when available and send `<project> · <outcome> · <merged n> · digest <path>`; otherwise print that line.
7. Delete only `"$WORKDIR"`.

## Invariants

- Keep one project per run. enforced by: registry.
- Create only `push/<run_id>-*` branches. enforced by: guard.
- Never force-push. enforced by: guard.
- Merge only after recorded passed verification and an `approve` verdict; squash only. enforced by: guard.
- Never write curated YAML; use `registry propose --set brief.*` only. enforced by: guard.
- Never target `project-registry`. enforced by: registry.
- Never read or quote secrets. enforced by: guard.
- Never deploy, send outbound messages, pay, or mutate external systems. enforced by: never-classes in validate-spec + guard (gh/api mutations).
- Never close or edit issues or PRs the builder did not open. enforced by: guard.

Treat a guard denial as a bug in the plan, never an obstacle to route around. Stop and finish `aborted` with the denial reason.

## Failure handling

| Case | Record and action |
|---|---|
| Duplicate scheduled run | The start attempt records `lease_held`; print its reason and stop. |
| Crash mid-chunk | Execute returned reconcile actions, run `build reconcile --done`, and restart; reconciliation records `crashed` and `reconciled`. |
| Merge succeeded but write-back failed | Let reconciliation rebuild from tags and PR run ids; record `reconciled`. |
| Registry push is non-fast-forward | Rebase and retry once; otherwise leave `finalize_pending` for reconciliation. |
| Branch-name collision | Let start reconciliation sweep the stale `push/*` branch; record `reconciled`. |
| Codex unavailable or logged out | Retry once; finish `codex_unavailable`. |
| Baseline red | Record `verify_failed`; repair collection/command errors, continue genuine failures with no-new-failures, or finish `baseline_red`. |
| Reviewer rejects twice | Record `chunk_rejected --reason review` and continue. |
| Breaker threshold reached or revert detected | Finish `paused`; only the owner resumes it. |
| STOP file or automation pause | Finish the current verification/review, record `stopped`, and finish `stopped` before another chunk. |
