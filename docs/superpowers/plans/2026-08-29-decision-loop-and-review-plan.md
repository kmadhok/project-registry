# Implementation Plan: Decision loop, honest review, and the un-mistypable happy path

Date: 2026-08-29. Input: the run record as of run `20260828T120017Z-vps-e7eb4af7-9160`
(7 build runs, 8 merges, 0 reverts, 7 guard denials, 20/22 reviewer approvals),
the 2026-08-25 shadow-run audit, and `docs/ideas/owner-loop.md`.

## Progress (2026-08-30, branch `worktree-decision-loop`)

Phases 1–5 landed (Tasks 1–15) with the full suite and the fault harness
green; Phase 6 (Tasks 16–17) in flight — the listener script and RUNBOOK unit
exist, `registry request-run` is being wired. Deviations: verbs are CLI-only
(no MCP twins); a `build push` verb was added for round-2 fix pushes;
`reject` discards a dirty attempt before returning to `main`; the sandbox
observe checkpoint is deferred until the branch is on `main` (from a worktree
the hook and skill paths point at the main checkout). Codex's review of
Phase 1 found two defects (same-day `brief.reviewed` re-arming a wait; a stale
`waiting on` line after release) — both fixed in Task 5's commit.

## Overview

The builder merges a chunk in ~6 minutes and has never used more than half its
budget; every run has ended by hitting a wall that only the owner can move
(`needs_intent`, `blocked_by_policy`) or by tripping over its own git command.
One of those walls (`blocked_by_policy`) is invisible to the inbox, and nothing
stops the project from being re-selected into the same wall the next day. The
reviewer approves 91% of chunks and has approved merged defects. Nothing
measures progress against `brief.done_criteria`. GitHub evidence is 8 days
stale and the retro has never run.

This plan makes the *decision loop* the product: every stop becomes an inbox
item with a one-line command, a project waits instead of re-running, the
reviewer must falsify before it approves, the happy-path git operations become
registry verbs the agent cannot mistype, progress is reported per done
criterion, evidence refreshes before each run, and a decision on the Mac can
kick a run on the VPS.

Not in scope: more runs per day, more projects in `build`, any web UI, brief
authoring for the ~30 `incubating` projects (that is `/intent-refresh` work,
revisit when the three focus projects run dry).

## Architecture decisions

- **State, not events, carries "waiting on the owner".** `ProjectBuildState`
  gains `waiting_on`; the inbox and eligibility read it. Scanning the journal
  for `chunk_skipped` (today's `inbox.py:77`) accumulates forever and missed
  the 08-28 run entirely.
- **A waiting project is not `ready`.** New eligibility state `waiting_owner`.
  It clears when the blocking classes are allowed, or when the owner has acted
  on the project (any proposal applied/rejected, per `data/audit_log.jsonl`)
  after the run that set it — one more attempt, never a loop.
- **`finish_run` refuses an unexplained `blocked_by_policy`**, the same shape as
  the AD-11 refusal after `guard_denied`. Enforced in code; the skill's job is
  to record which items and classes.
- **The reviewer keeps one JSON verdict; a second model feeds it.** Codex
  reviews the diff first; the Claude reviewer must confirm or refute each Codex
  finding with evidence. Journal schema and the guard's `verdict == approve`
  check stay unchanged.
- **Happy-path git/gh operations become `registry build` verbs** in a new
  `build_ops.py`. Verbs derive branch and tag names from the lease, enforce the
  same invariants the guard enforces (namespace, no force, verified+approved
  before merge, shadow never merges), and journal the matching event
  themselves. The guard stays as the backstop for raw commands. `build_runs.py`
  is already 1,209 lines; no new code goes there beyond state fields.
- **Done criteria are referenced by 1-based index from SPEC items**
  (`Criteria: 1, 3`). Coverage is a pure function of brief + SPEC; nothing
  executes criteria in this plan.
- **Runs are triggered by decisions through ntfy, not by SSH.** The Mac cannot
  reach the VPS; the VPS already subscribes to ntfy. A `run` action on a
  private command topic is the whole protocol; the lease makes it safe.

Model routing (per `~/.claude/CLAUDE.md`): mechanical tasks from this spec go
to Codex via `codex-implementation`; prompts, skills, docs, and anything that
merges into target repos are written or verified inline. Every Codex task is
verified against its acceptance list before it lands.

---

## Phase 1 — Close the decision loop

### Task 1: `waiting_on` in project build state

**Description:** `ProjectBuildState` gains `waiting_on: dict | None` with keys
`kind` (`blocked_by_policy` | `needs_intent`), `classes` (sorted list, may be
empty), `since` (event ts), `run_id`. `apply_run_to_state` sets it when the
run's `run_finished.outcome` is one of those two, collecting `classes` from that
run's `chunk_skipped` events with `reason == "blocked_by_policy"`
(`detail.classes`), and clears it on any other `run_finished`.

**Acceptance criteria:**
- [ ] Folding a run with `chunk_skipped(blocked_by_policy, classes=[generated_data])` + `run_finished(blocked_by_policy)` yields `waiting_on == {"kind": "blocked_by_policy", "classes": ["generated_data"], ...}`.
- [ ] Folding `run_finished(needs_intent)` yields `kind == "needs_intent"`, `classes == []`.
- [ ] A later run with any other outcome clears `waiting_on`; an existing `state.json` without the key loads unchanged; a malformed `waiting_on` raises `BuildError`.

**Verification:** `python3 -m pytest tests/test_build.py tests/test_build_lifecycle.py -q`
**Dependencies:** None
**Files:** `src/project_registry/build_runs.py`, `tests/test_build_lifecycle.py`
**Scope:** S · Route: Codex

### Task 2: `waiting_owner` eligibility

**Description:** Add `waiting_owner` to `ELIGIBILITY_STATES` (grouped right
after `ready`). `classify` returns it when `build_state.waiting_on` is set and
unresolved. Resolved means: `kind == blocked_by_policy` and every class is in
`project.automation.allow`; or the owner acted on the project after
`waiting_on.since`. Owner actions come from a new helper
`audit.last_owner_action(paths) -> dict[project_id, ts]` reading
`data/audit_log.jsonl` (`apply_proposal`, `reject_proposal`,
`record_review`). `build_queue`/`classify`/`readiness` take an optional
`owner_actions` mapping; `None` means "unknown" and counts as unresolved. Every
caller passes it — verified list: `cli.py:589`, `mcp/server.py:298`,
`inbox.py:46`, `dashboard.py:168`, `build_runs.py:685` (`begin_run`),
`build_runs.py:1017` (`finish_run`). `build start --project <id>
--force-named` still overrides. `build-readiness` prints `waiting_on`.

**Acceptance criteria:**
- [ ] Blocked classes ⊄ `allow` and no owner action → `waiting_owner`, reason names the classes, run id, and date.
- [ ] `allow` widened to cover the classes → `ready`; owner rejected a proposal after `since` → `ready` (one more attempt).
- [ ] `kind == needs_intent` with no owner action → `waiting_owner`; with a proposal applied after `since` and no brief gaps → `ready`.
- [ ] `by_state` counts and queue ordering include the new state; a bare `build start` never selects a `waiting_owner` project; `--force-named` does.

**Verification:** `python3 -m pytest tests/test_automation.py tests/test_build_lifecycle.py tests/test_cli.py tests/test_mcp.py -q`
**Dependencies:** Task 1
**Files:** `src/project_registry/automation.py`, `src/project_registry/audit.py` (new, or the existing audit reader if one exists in `proposals.py`), `src/project_registry/build_runs.py`, `src/project_registry/cli.py`, `src/project_registry/mcp/server.py`, `src/project_registry/inbox.py`, `src/project_registry/dashboard.py`, tests
**Scope:** M · Route: Codex, verified inline

### Task 3: `finish_run` refuses an unexplained block; inbox reads state

**Description:** `finish_run(outcome="blocked_by_policy")` raises `BuildError`
unless the run has ≥1 `chunk_skipped` event with `reason == blocked_by_policy`
and non-empty `detail.classes`; the message prints the exact `build event`
command. `owner_inbox` replaces the journal scan (`inbox.py:65–93`, blocked
part) with `state.waiting_on`: kind `blocked_by_policy`, summary names classes,
run id, and date; action is `registry propose <id> --set
automation.allow=<existing ∪ blocked> --rationale ...` (must preserve already
allowed classes — `ai-engineering-markets` has `dependencies`). Digest "Needs
the owner" already renders the inbox, so no digest change.

**Acceptance criteria:**
- [ ] Finishing `blocked_by_policy` without the event fails with the command in the message; with the event it succeeds.
- [ ] Replaying an 08-28-shaped journal (five merges, then a blocked event, then finish) produces exactly one `blocked_by_policy` inbox item whose action contains both existing and new classes.
- [ ] After a later run with another outcome, the item is gone; `needs_intent` items are unchanged.

**Verification:** `python3 -m pytest tests/test_inbox.py tests/test_build_lifecycle.py -q`
**Dependencies:** Tasks 1–2
**Files:** `src/project_registry/build_runs.py`, `src/project_registry/inbox.py`, `tests/test_inbox.py`, `tests/test_build_lifecycle.py`
**Scope:** S · Route: Codex

### Task 4: Skill records blocked items; docs and ADR

**Description:** `.claude/skills/push-project/SKILL.md` Phase 4a: when
`validate-spec` has no ready item and the planner adds none, record one
`chunk_skipped --chunk-id <index> --reason blocked_by_policy --detail
classes=<csv>` per blocked unchecked item, then finish `blocked_by_policy`.
`.claude/skills/inbox/SKILL.md`: the **Allow** option proposes `existing ∪
blocked`. `scripts/faults/f09-policy-block.sh` asserts the inbox item.
`CLAUDE.md` "What is enforced" gains the refusal; `docs/ADRs/ADR-006` gains
**AD-13** (waiting_owner + refusal); `docs/RUNBOOK.md` "Reading a run" mentions
`waiting_owner`.

**Acceptance criteria:**
- [ ] Fault script f09 passes and shows the item.
- [ ] `tests/test_docs.py` and `tests/test_skills.py` pass.
- [ ] `registry build-queue` explains a waiting project in one line.

**Verification:** `python3 -m pytest tests/test_docs.py tests/test_skills.py -q && PYTHONPATH=src bash scripts/faults/f09-policy-block.sh`
**Dependencies:** Task 3
**Files:** two SKILL.md files, `scripts/faults/f09-policy-block.sh`, `CLAUDE.md`, `docs/ADRs/ADR-006-autonomous-builder.md`, `docs/RUNBOOK.md`
**Scope:** S · Route: inline

### Checkpoint 1
- [ ] Full `python3 -m pytest` green; `registry validate` 0 errors.
- [ ] `registry build-queue` on real data: after the owner decides `ai-news-aggregator`'s classes via `/inbox`, it is `ready`; a project finishing `blocked_by_policy` under the new code shows `waiting_owner` the next day.
- [ ] Note: the real 08-28 run has no `chunk_skipped(blocked_by_policy)` event, so its state is not backfilled; the owner decides from the digest once, by hand.

---

## Phase 2 — Review that can say no

### Task 5: Reviewer must falsify

**Description:** Rewrite `.claude/agents/build-reviewer.md`. Order: correctness
before policy. For every `Acceptance:` clause the reviewer names the probe it
ran (command or traced code path) and the observed result; it re-runs the
contract `test` command itself instead of trusting supplied output; it reads
every changed function for inverted conditions, `None` handling, off-by-one,
and silent excepts. A new input block `SECOND_OPINION` (free text, may be
`unavailable`) lists another model's findings; each must get a disposition
`confirmed | refuted | out_of_scope` with evidence. `approve` requires a probe
per acceptance clause and a disposition per finding. JSON gains optional
`probes` and `second_opinion` arrays; `build_runs.parse_verdict` accepts and
validates them (and still rejects unknown keys). Remove "do not cycle through
cosmetic rounds" wording that biases toward approve; keep the one-fix-pass
limit.

**Acceptance criteria:**
- [ ] `parse_verdict` accepts the two new optional keys, rejects malformed entries, and still parses today's four-key JSON.
- [ ] The prompt makes `approve` conditional on probes and dispositions; `tests/test_skills.py` (if it checks agent files) passes.

**Verification:** `python3 -m pytest tests/test_build.py tests/test_skills.py -q`
**Dependencies:** None (contract for Task 6)
**Files:** `.claude/agents/build-reviewer.md`, `src/project_registry/build_runs.py`, `tests/test_build.py`
**Scope:** S · Route: inline (prompt is taste-critical)

### Task 6: Codex second opinion before the reviewer

**Description:** SKILL Phase 4f step 0: in `$WORKDIR`, run `"$CODEX_BIN" review
--base main` in the foreground (scope flag and custom prompt are mutually
exclusive per `codex review --help`; use the scope flag), stdout →
`$WORKDIR/../codex-review-$RUN_ID-$CHUNK_ID.md`; on non-zero exit write
`unavailable: <exit code>` and continue — a second opinion never blocks. Supply
the file as `SECOND_OPINION` to the reviewer. `review_verdict.detail` gains
`second_opinion: {"source": "codex", "status": "ok|unavailable", "findings": N}`.
`build-report` prints "second opinions: N raised / M confirmed / K refuted"
from the journal. Skip for `plan` and `contract` chunks.

**Acceptance criteria:**
- [ ] `codex review --base main` produces a report in a throwaway clone on the Mac; the RUNBOOK records the Codex version requirement for the VPS.
- [ ] `build-report` counts come from `review_verdict.detail` and are zero on the existing journal.
- [ ] Skill text has no backgrounded Codex call.

**Verification:** `python3 -m pytest tests/test_build.py tests/test_skills.py tests/test_docs.py -q`; manual: run the review command once on a scratch clone.
**Dependencies:** Task 5
**Files:** `.claude/skills/push-project/SKILL.md`, `src/project_registry/build_runs.py` (`build_report`), `docs/RUNBOOK.md`, tests
**Scope:** S · Route: skill inline; report metric to Codex

### Checkpoint 2
- [ ] `/push-project-observe kmadhok/<small-repo>` on the Mac: every `review_verdict` carries `probes` and `second_opinion`; no `guard_denied`.
- [ ] Owner reads one reviewer JSON end-to-end and agrees it is falsification, not confirmation.

---

## Phase 3 — Evidence on, retro on

### Task 7: Sync before every scheduled run

**Description:** `scripts/run-build.sh`: after `git pull --ff-only`, run
`registry sync` when `GITHUB_TOKEN` is set, non-fatal (`|| echo "sync failed"`).
RUNBOOK: where the token lives on the VPS (environment of the scheduler entry,
never in the repo).

**Acceptance criteria:**
- [ ] `registry sync-status` shows the last refresh ≤ 1 day after the next scheduled run.
- [ ] A missing token or a sync failure does not prevent the build.

**Verification:** `bash -n scripts/run-build.sh`; next day's digest + `sync-status`.
**Dependencies:** None
**Files:** `scripts/run-build.sh`, `docs/RUNBOOK.md`
**Scope:** XS · Route: inline

### Task 8: Retro runs weekly on the VPS

**Description:** `scripts/run-retro.sh` mirrors `run-build.sh` (`git pull`,
`claude -p /build-retro`, log to `../build-work/logs/`). `.claude/skills/build-retro/SKILL.md`
ends by committing `docs/observations/<date>-retro.md` + `data/proposals/` to
`main`, pushing, and sending the proposals through `registry notify`.
`notify.build_notification(digest, inbox)` is digest-based today, so add an
inbox-only path (`registry notify --inbox`) that renders the inbox without a
digest. RUNBOOK: schedule Mondays 13:00Z after the 12:00Z build.
(`tests/test_docs.py` does not enumerate scripts; a new launcher needs no test change.)

**Acceptance criteria:**
- [ ] First VPS retro produces an observation file and ≥0 proposals, committed to `main`, and a notification.
- [ ] Retro never runs while a lease exists (check `data/build/lease.json`, exit if present).

**Verification:** `bash -n scripts/run-retro.sh`; `python3 -m pytest tests/test_docs.py tests/test_notify.py -q`; first Monday run.
**Dependencies:** Task 7 (retro uses fresh evidence)
**Files:** `scripts/run-retro.sh` (new), `.claude/skills/build-retro/SKILL.md`, `src/project_registry/notify.py`, `src/project_registry/cli.py`, `docs/RUNBOOK.md`, `tests/test_notify.py`
**Scope:** S · Route: script to Codex; skill prose inline

### Checkpoint 3
- [ ] `registry sync-status` fresh; one retro file exists; `owner-inbox` lists any retro proposals.

---

## Phase 4 — The happy path cannot be mistyped

### Task 9: `build_ops.py` with `branch` and `pr`

**Description:** New module `src/project_registry/build_ops.py`. Shared
`_require_active_lease(paths, run_id)` (lease exists, matches, not
`finalize_pending`). `create_branch(paths, run_id, workdir, chunk_id, title)`:
`git checkout main && git pull --ff-only`, branch
`push/<run_id>-<chunk_id>-<slug>` (kebab, ≤40 chars), `checkout -b`, journal
`chunk_started` with `branch`. `open_pr(paths, run_id, workdir, chunk_id,
title, body_file)`: refuse if the current branch is outside the run namespace
or any changed path matches `lease.contract_forbidden_paths`; `git add -A`,
commit, `git push -u origin <branch>` (never `--force`), `gh pr create --head
<branch> --title --body-file`, parse URL and number, journal `pr_opened`.
CLI: `registry build branch <run> --chunk-id --title --workdir`, `registry
build pr <run> --chunk-id --title --body-file --workdir`. **CLI-only, no
MCP twins** (decided 2026-08-30 during execution): the MCP server forbids
mutating tool names (`FORBIDDEN_TOOL_VERBS`) and README promises no tool
mutates GitHub; the verbs are usable only under a lease from the CLI.

**Acceptance criteria:**
- [ ] Branch name derivation and slug truncation are deterministic; no lease or wrong run id → error before any git call.
- [ ] A changed forbidden path aborts before commit; push args never include a force option; remote is always `origin`.
- [ ] `chunk_started` and `pr_opened` are journaled with branch, PR URL, and number; a fake `gh` shim on `PATH` records the exact argv.

**Verification:** `python3 -m pytest tests/test_build_ops.py tests/test_cli.py tests/test_mcp.py tests/test_docs.py -q` (tests use a temp bare origin + clone and a shim `gh`; no network).
**Dependencies:** None
**Files:** `src/project_registry/build_ops.py` (new), `src/project_registry/cli.py`, `src/project_registry/mcp/server.py`, `README.md`, `tests/test_build_ops.py` (new)
**Scope:** M · Route: Codex, verified inline (writes to target repos)

### Task 10: `merge`, `reject`, `skip`

**Description:** `merge_chunk(paths, run_id, workdir, chunk_id, pr_number)`:
refuse unless the journal has `verify_passed` for the chunk and the latest
`review_verdict` for it is `approve`, the lease is active, and `dry_run` is
false (shadow → "use skip"); `gh pr merge <n> --squash --delete-branch`; fetch;
tag `checkpoint/<run_id>-<chunk_id>` at the merge SHA; push the tag; journal
`merged` (pr_url, tag, merge_sha); `checkout main && pull`. `reject_chunk(...,
reason)`: `gh pr close`, delete only a namespaced branch, journal
`chunk_rejected`. `skip_chunk(...)` for shadow: journal `chunk_skipped
--reason shadow`, return to `main`. CLI `registry build merge|reject|skip`;
CLI-only, no MCP twins (see Task 9).

**Acceptance criteria:**
- [ ] Missing `verify_passed`, non-`approve` verdict, `finalize_pending`, or shadow each refuse before any `gh` call, with the rule named.
- [ ] Happy path: `gh pr merge` argv is exactly `--squash --delete-branch`; the tag name and `merged` event match; the clone ends on `main`.
- [ ] `reject` refuses to delete a branch outside `push/<run_id>-*`.

**Verification:** `python3 -m pytest tests/test_build_ops.py -q`
**Dependencies:** Task 9
**Files:** `src/project_registry/build_ops.py`, `src/project_registry/cli.py`, `src/project_registry/mcp/server.py`, `README.md`, `tests/test_build_ops.py`
**Scope:** M · Route: Codex, verified inline line-by-line (this is the merge)

### Task 11: `writeback`

**Description:** `registry build writeback <run>`: regenerate the dashboard;
`git add data/build data/proposals DASHBOARD.md`; commit
`build: <run> <project> <outcome>`; push `origin main` (on non-fast-forward,
`pull --rebase` once and retry); `confirm_writeback`; commit the confirm line
and push again; report `{committed, pushed, confirmed, pending_commit}`.
Idempotent: safe to re-run for a `finalize_pending` lease in Phase 0.

**Acceptance criteria:**
- [ ] Happy path produces two commits on `main` and no lease file.
- [ ] Non-fast-forward → rebase → push succeeds; a second-push failure leaves the lease released and reports `pending_commit: true`.
- [ ] Staged paths are only `data/build/**`, `data/proposals/**`, `DASHBOARD.md`.

**Verification:** `python3 -m pytest tests/test_build_ops.py tests/test_build_lifecycle.py -q`
**Dependencies:** Task 9 (shared helpers)
**Files:** `src/project_registry/build_ops.py`, `src/project_registry/cli.py`, tests
**Scope:** S · Route: Codex

### Task 12: Skill, guard doc, and ADR use the verbs

**Description:** SKILL Phases 4b, 4e, 4f.6, 4g, and 5 call the verbs;
hand-composed git/gh is limited to reads (`git diff`, `gh pr view`), the
verdict comment (`gh pr comment --body-file`), and Phase 0 reconcile actions.
`docs/BUILD_GUARD.md`: "the happy path is registry verbs; the guard is the
backstop for anything else". ADR-006 **AD-14**. `CLAUDE.md` cheatsheet gains
the six verbs (`tests/test_docs.py` checks). Fault scripts that exercise these
steps updated.

**Acceptance criteria:**
- [ ] No `git push`, `gh pr create`, or `gh pr merge` remains in the skill text outside Phase 0 reconcile.
- [ ] `tests/test_docs.py`, `tests/test_skills.py`, `tests/test_build_guard.py` pass; `scripts/faults/run-all.sh` green.

**Verification:** `python3 -m pytest -q && PYTHONPATH=src bash scripts/faults/run-all.sh`
**Dependencies:** Tasks 9–11
**Files:** `.claude/skills/push-project/SKILL.md`, `docs/BUILD_GUARD.md`, `docs/ADRs/ADR-006-autonomous-builder.md`, `CLAUDE.md`, `scripts/faults/*`
**Scope:** S · Route: inline

### Checkpoint 4
- [ ] `/push-project-observe` sandbox run: zero `guard_denied`, events in the expected order per chunk, checkpoint tag present.
- [ ] A raw `git push origin feature` under a lease is still denied (guard test unchanged).

---

## Phase 5 — Know how far the outcome is

### Task 13: SPEC items reference done criteria

**Description:** `specs.py` parses an optional `Criteria:` metadata line
(`1, 3` = 1-based indexes into `brief.done_criteria`, or `none`) into
`SpecItem.criteria: list[int]`. `validate_spec` adds suggestion-level
`unknown_criterion` (index out of range) and `uncovered_criteria` (criteria no
item references, checked or unchecked). Items without the line stay valid.
`docs/SCHEMA.md` documents it.

**Acceptance criteria:**
- [ ] Parse and round-trip; out-of-range index → suggestion, item still ready.
- [ ] A SPEC covering 2 of 5 criteria reports the 3 uncovered ones by text.

**Verification:** `python3 -m pytest tests/test_specs.py -q`
**Dependencies:** None
**Files:** `src/project_registry/specs.py`, `docs/SCHEMA.md`, `tests/test_specs.py`
**Scope:** S · Route: Codex

### Task 14: `registry outcome-status` and the digest line

**Description:** `specs.outcome_status(project, spec_text)` → per criterion
`{index, text, status, items}` with status `met` (all referencing items
checked), `in_progress` (an unchecked referencing item is ready), `blocked`
(unchecked items all `blocked_by_policy`, with classes), `needs_intent`,
`unplanned` (no items). CLI `registry outcome-status <id> <spec-path> [--json]`;
MCP `get_outcome_status`. `build finish --spec <path>` (optional) stores the
summary in `run_finished.detail.criteria`; the digest gains an `## Outcome`
line ("criteria: 2/5 met · 2 blocked (generated_data) · 1 unplanned") and
`notify` puts the counts in the title.

**Acceptance criteria:**
- [ ] Status matrix covered by tests (each status, mixed items, empty SPEC).
- [ ] `build finish` without `--spec` is unchanged; with it, digest and notification carry the counts.

**Verification:** `python3 -m pytest tests/test_specs.py tests/test_build_lifecycle.py tests/test_notify.py tests/test_cli.py tests/test_mcp.py tests/test_docs.py -q`
**Dependencies:** Task 13
**Files:** `src/project_registry/specs.py`, `src/project_registry/build_runs.py` (finish + digest), `src/project_registry/notify.py`, `src/project_registry/cli.py`, `src/project_registry/mcp/server.py`, `README.md`, `CLAUDE.md`, tests
**Scope:** M · Route: Codex; digest wording inline

### Task 15: Planner emits `Criteria:` and covers or explains

**Description:** `.claude/agents/build-planner.md`: every item carries
`Criteria:`; every done criterion is referenced by ≥1 item, or the planner
returns `needs_intent` naming the uncovered criterion; `roadmap_done` only when
all criteria are `met`. SKILL Phase 5 passes `--spec docs/SPEC.md` to finish.

**Acceptance criteria:**
- [ ] Skill and planner text updated; `tests/test_skills.py` passes.
- [ ] Next real run's digest shows an `## Outcome` line.

**Verification:** `python3 -m pytest tests/test_skills.py -q`; next digest.
**Dependencies:** Task 14
**Files:** `.claude/agents/build-planner.md`, `.claude/skills/push-project/SKILL.md`
**Scope:** XS · Route: inline

### Checkpoint 5
- [ ] `registry outcome-status ai-news-aggregator <clone>/docs/SPEC.md` prints a table; before the next planner pass most rows are `unplanned` (expected), after it none are.

---

## Phase 6 — A decision on the Mac kicks a run on the VPS

### Task 16: `/inbox` requests a run after an unblocking decision

**Description:** `data/build/notify.json` gains `command_topic`. New
`registry request-run <id>` POSTs `{"action": "run", "project": "<id>"}` to it
(reuse `notify`'s HTTP code). `/inbox` calls it only when the decision it just
applied made the project `ready` (re-check `build-queue`).

**Acceptance criteria:**
- [ ] No command topic configured → prints the message, exit 0.
- [ ] Unknown project id → error; ready project → one POST with the JSON body.

**Verification:** `python3 -m pytest tests/test_notify.py tests/test_cli.py -q`
**Dependencies:** Phase 1
**Files:** `src/project_registry/notify.py`, `src/project_registry/cli.py`, `.claude/skills/inbox/SKILL.md`, `CLAUDE.md`
**Scope:** S · Route: Codex; skill inline

### Task 17: VPS listener

**Description:** `scripts/decision-listener.sh`: `ntfy subscribe
<command_topic>` (JSON stream); for `{"action":"run","project":"<id>"}` with an
id present in `registry list --json`, run `git pull --ff-only &&
scripts/run-build.sh <id>` (a held lease makes it a cheap `lease_held` exit);
ignore other actions; log to `../build-work/logs/listener.log`. RUNBOOK gets a
systemd unit snippet; the owner installs it (the Mac cannot SSH to the VPS).

**Acceptance criteria:**
- [ ] Unknown action or project id is logged and ignored; no shell interpolation of message fields (validate against the project list before use).
- [ ] A `run` request while a lease is held results in a `lease_held` attempt, not a second run.

**Verification:** `bash -n`; a dry test with a throwaway topic on the Mac; then the owner installs on the VPS.
**Dependencies:** Task 16
**Files:** `scripts/decision-listener.sh` (new), `docs/RUNBOOK.md`
**Scope:** S · Route: Codex; owner installs

### Checkpoint 6
- [ ] End to end: `/inbox` on the Mac approves an allow proposal → VPS run starts within minutes → digest + notification arrive.

---

## Order and parallelism

Sequential spine: 1 → 2 → 3 → 4 (state → eligibility → inbox → skill).
Phase 3 (Tasks 7–8) is independent and can land any time.
Phase 2: Task 5 defines the `SECOND_OPINION` contract, then Task 6.
Phase 4: Task 9 first (shared helpers), then 10 ∥ 11, then 12.
Phase 5: 13 → 14 → 15. Phase 6 after Phase 1.
Suggested calendar: Phases 1–3 first (one working day of agent time), observe
one scheduled run, then Phase 4, then 5, then 6.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| "Owner acted" heuristic re-arms a still-blocked project | Low — one wasted run, re-enters `waiting_owner` with fresh classes | Task 3 records classes per item; digest explains |
| Verbs bypass the PreToolUse guard (subprocess is not a Bash tool call) | High if verbs drift from guard rules | Verbs re-implement the invariants with tests naming each guard rule id; raw commands stay guarded; parity test in `test_build_ops.py` |
| Codex review unavailable or slow on the VPS | Medium — weaker review that run | Never blocks; journaled as `unavailable`; RUNBOOK records the version requirement |
| `Criteria:` indexes drift when the owner edits `done_criteria` | Low | `unknown_criterion` suggestion; planner repairs on the next run |
| README/CLAUDE.md drift from new commands | Low | `tests/test_docs.py` fails the build |
| VPS changes need the owner (no SSH from the Mac) | Medium — Phases 3 and 6 wait on a manual step | RUNBOOK snippets are copy-paste; everything else lands without it |
| More reviewer rigor lowers merge rate | Intended | Watch `build-report` "second opinions confirmed"; if zero after 10 chunks, the second opinion is noise — drop it |

## Open questions (defaults chosen; change by editing this file)

- Rejecting an allow proposal re-arms one attempt (default) vs. keeps the project waiting until `allow` changes.
- Second opinion on every non-plan chunk (default) vs. only diffs above a size threshold.
- Criteria referenced by index (default) vs. by slug.
- Listener now (Phase 6) vs. after Phase 1 proves the inbox item shows up — default: last.

## Owner actions outside this plan

- Decide `ai-news-aggregator`'s classes (`generated_data`, `dependencies`) via `/inbox` from the 08-28 digest — nothing surfaces it until Phase 1 lands.
- Scrub the PII in `interview-prep-prod` (`interview-prep-intake.skill` zip, `scripts/build_resume_pdf.py:211`) before its next run; it is ranked #2 and will fail the contract bootstrap again.
- Check whether the interview-prep #13/#14 defects from the 08-25 audit (inverted "drafts awaiting send", `now=None` crash, anti-flap message) were ever fixed on `main` — that repo is the live daily runner.
- Close or rewrite issue #3 (July checklist, mostly done).
- On the VPS: set `GITHUB_TOKEN` for Task 7; add the retro schedule (Task 8) and the listener unit (Task 17).
