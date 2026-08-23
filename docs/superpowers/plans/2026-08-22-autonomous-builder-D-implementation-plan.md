# Autonomous builder — D. Implementation plan

**Status 2026-08-22:** T1–T12, T14 implemented on branch `codex/autonomous-builder`; T13 (rollout) is owner-operated — see [E. Test and rollout](../specs/2026-08-22-autonomous-builder-E-test-and-rollout.md).

**Date:** 2026-08-22 · Implements [C. Technical design](../specs/2026-08-22-autonomous-builder-C-technical-design.md) · Each task = one reviewable PR, ordered by dependency. Codex implements from these specs; Fable verifies. Tests are requirement-driven (scenarios listed per task).

> Conventions: `registry` = `.venv/bin/registry`; all tasks keep `python -m pytest` green and `registry validate` at 0 errors; docs change in the same PR as behavior.

## Architecture decisions (summary)

- Policy in `src/project_registry/{automation,specs,contracts,build}.py`; CLI + MCP call the same functions.
- Enforcement via PreToolUse guard keyed on `data/build/lease.json`.
- Runs never rewrite curated YAML; machine state in `data/build/`.
- Per-chunk squash-merge + tag; fresh clone per chunk.

## Dependency graph

```
T1 sync merge fix ──────────────────────────────────────┐
T2 schema: brief + automation ─ T3 eligibility/ranking ─┤
                                 │                      ├─ T5 lease/begin/finish ─ T8 guard ─ T10 skill rewrite ─ T12 shadow+sandbox ─ T13 rollout
T4 build journal/state/report ───┘                      │            │
T6 contract validator ──────────────────────────────────┤            ├─ T9 agents (planner/reviewer)
T7 spec validator ──────────────────────────────────────┘            └─ T11 intent-refresh + brief-status
T14 docs/ADR (rolling, finalized last)
```

---

## Phase 1 — Foundation (registry code only)

### Task 1: Targeted sync merges into the previous snapshot
**Goal:** `sync --repo X` keeps every other repo's last-known state.
**Modules:** `github/sync.py`, `github/snapshot.py` (if coverage needs a `carried` marker), `tests/test_sync.py`.
**Behavior:** `fresh` starts as a copy of `previous.repos` minus targets; targets overwrite; `coverage` reports `repos_requested` = targets only; non-targets keep their `fetched_at`.
**Acceptance:** (a) after `sync(repos=[A])` on a 3-repo snapshot, snapshot has 3 repos; (b) A is fresh, B/C unchanged with original timestamps; (c) a failed target still carries forward as today; (d) `registry mismatches` no longer reports `repo_missing` for non-targets after a targeted sync.
**Tests:** the four scenarios above + existing sync tests.
**Docs:** `docs/RUNBOOK.md` sync section; ADR-002 note.
**Compat:** none. **Rollback:** revert commit. **Depends:** none. **Size:** S.

### Task 2: Schema — `brief` and `automation` objects
**Goal:** Curated intent and automation policy become first-class fields.
**Modules:** `registry/schema/project.schema.json`, `model.py` (dataclasses `Brief`, `OpenDecision`, `Automation`, parse/to_dict, `KNOWN_FIELDS`), `validation.py` (rules `brief_incomplete_for_build` ERROR, `automation_without_focus_lifecycle` SUGGESTION, `open_decision_unanswered` SUGGESTION when mode=build), `proposals.py` (`EDITABLE_PATHS` += `brief.done_criteria|non_goals|constraints|open_decisions|reviewed`, `automation.mode|allow|budget.chunks_per_run|budget.minutes_per_run|paused`; list/JSON coercion), `docs/SCHEMA.md`.
**Acceptance:** (a) a YAML with both objects loads and round-trips byte-stable; (b) unknown sub-keys raise `RegistryError`; (c) `mode=build` without `done_criteria` → validation ERROR; `mode=off` → no error; (d) `registry propose <id> --set automation.mode=build --set brief.done_criteria='["a","b"]'` then `proposal-apply --approve` writes the fields; (e) `brief.open_decisions` item without `question` rejected.
**Tests:** model parse/serialize; validation per rule (positive/negative); proposal coercion for each new path; MCP `propose_project_update` with `brief.*`.
**Docs:** SCHEMA.md, CLAUDE.md field list.
**Compat:** all existing YAML valid (objects optional). **Rollback:** revert; no data written. **Depends:** none. **Size:** M.

### Task 3: Eligibility classification and builder ranking
**Goal:** Deterministic `classify()` + `rank()` with reasons.
**Modules:** new `automation.py`; `cli.py` (`build-queue`, `build-readiness`); `mcp/server.py` (`get_build_queue`, `validate_project_readiness`); `dashboard.py` (Build section listing states).
**Behavior:** per C §2. Reads `data/build/state.json` if present (from T4; tolerate absence).
**Acceptance:** (a) each of the six states reachable with a fixture and explained by `reasons`; (b) `project-registry` is always `ineligible`; (c) ranking ignores GitHub urgency — fixture with huge `github_urgency` and no priority ranks below a `priority: high`; (d) fairness: equal priority → older `last_success_at` first, never-built first; (e) `blocked_by` → `paused`; open decision → `needs_intent` with the question in reasons; (f) CLI and MCP outputs equal.
**Tests:** one per acceptance item + determinism (same input → same order).
**Docs:** README cheatsheet; USER_STORIES new story "US-013 builder queue".
**Compat:** `get_attention_queue` untouched. **Rollback:** revert. **Depends:** T2. **Size:** M.

### Task 4: Build journal, machine state, report
**Goal:** `data/build/runs.jsonl`, `state.json`, `registry build-report`, `get_build_report`.
**Modules:** new `build.py` (event schema, `append_event`, `load_state`, `update_state_on_finish`, circuit breaker, report aggregation), `storage.py` (`Paths.build_dir`, `build_runs_file`, `build_state_file`, `build_lease_file`, `build_digests_dir`), `cli.py`, `mcp/server.py`, `.gitignore` (`data/build/lease.json`, `data/build/STOP`).
**Acceptance:** (a) events validated on append (unknown type rejected); (b) report aggregates merges/reverts/rejections by reason/time per chunk from a fixture journal; (c) legacy `data/push_runs.jsonl` still counted in "runs (legacy)"; (d) circuit breaker: 3 consecutive `chunk_rejected`/`run_finished(outcome=aborted)` → `paused_reason=consecutive_failures`; one `reverted` → paused; (e) `registry build resume <id>` clears pause and records event; (f) malformed lines reported, not fatal.
**Tests:** per item; report golden output.
**Docs:** RUNBOOK (build files), SCHEMA (data/build).
**Compat:** `push-report` kept. **Rollback:** revert; delete `data/build/`. **Depends:** none (T3 consumes). **Size:** M.

### Checkpoint 1
- [ ] 283 + new tests green; `registry validate` 0 errors; `registry build-queue` lists all 98 projects by state (expect 97 `manual_only`/`ineligible`, 0 `ready` until briefs exist).

---

## Phase 2 — Run lifecycle and contracts

### Task 5: Lease, begin/finish, reconcile
**Goal:** Atomic run leasing with preflight; outcome recording; crash reconciliation.
**Modules:** `build.py` (`begin_run`, `finish_run`, `reconcile`), `cli.py` (`build start|finish|event|reconcile|env`), `mcp/server.py` (`begin_build_run`, `record_build_event`, `finish_build_run`, `reconcile_build_runs`, `get_build_context`), `github/client.py` (GET `pulls?head=` for reconcile listing only).
**Behavior:** per C §3/§6/§7. Preflight: registry `git status --porcelain` ignoring `data/build/**` must be empty and branch == `main`; else `registry_dirty`. Lease `O_EXCL` create; TTL default 10800 s; expired lease → reconcile then proceed. `get_build_context` returns `do_not_read` hint for evidence briefs.
**Acceptance:** (a) second `begin` while lease held → `lease_held`, no files changed; (b) expired lease → reconciled (`crashed` event) and new lease issued; (c) dirty registry → `registry_dirty`, no lease; (d) `--project <id>` that is `needs_intent` → refused unless `--force-named` (records `forced_named`); (e) `finish` writes state, digest file, releases lease; (f) `record_build_event` with mismatched `run_id` rejected; (g) `reconcile` lists `push/<dead-run>-*` branches/PRs from a fake client and emits actions (it does not mutate GitHub — the skill executes `gh` closes under guard); (h) `build env` prints host constants.
**Tests:** per item; concurrency test with two processes racing on the lease.
**Docs:** RUNBOOK run lifecycle; CLAUDE.md command cheatsheet.
**Compat:** none. **Rollback:** revert; remove lease. **Depends:** T3, T4. **Size:** L → split if >5 files: 5a (lease/begin/finish), 5b (reconcile/context/env).

### Task 6: Target contract validator
**Goal:** `.project-meta.yaml` v1 schema + `registry validate-contract`.
**Modules:** new `contracts.py`, `cli.py`, `mcp/server.py` (`validate_project_readiness` gains `contract` section when a file is passed; `get_build_context.contract_expectations`), `docs/SETUP.md` convention section, registry's own `.project-meta.yaml` upgraded to v1.
**Acceptance:** (a) minimal valid file passes; (b) missing `test` → error; `deploy: anything-but-none` → error; `secrets_required` with `=`/value-like entries → error (names only); (c) `services` non-empty → `runnable: false, reason=services`; (d) `personal_data`/`forbidden_paths` globs parsed; (e) `registry_id` mismatch with the project → error; (f) output explains each problem.
**Tests:** per item + golden valid files for the four focus repos (proposed contents, as fixtures).
**Docs:** SETUP.md, FINDINGS_REPO_HYGIENE.md pointer.
**Depends:** none. **Rollback:** revert. **Size:** S.

### Task 7: Spec validator
**Goal:** `validate_spec` over `docs/SPEC.md` text.
**Modules:** new `specs.py`, `cli.py` (`validate-spec <id> <file>`), `mcp/server.py`.
**Behavior:** per C §4; policy check uses the project's `automation.allow`; never-class verbs list; non-goal token overlap; `needs_intent` when item references `open_decisions` questions (token overlap) or contains `owner decides|TBD|once the owner`.
**Acceptance:** (a) legacy items (no Acceptance/Tests lines) → `ready=false, problems=[missing_acceptance, missing_tests]`; (b) item with `Classes: public_api` and allow without it → `blocked_by_policy`; (c) "close issues #1–#10" → `never_class`; (d) underwriting chunk 5 → `needs_intent`; (e) `next_ready_index` picks the first ready unchecked item; (f) checked items ignored; (g) structure errors when `## Remaining work` missing.
**Tests:** fixtures from the four real SPEC.md files (copied into tests) + synthetic.
**Docs:** SKILL spec format section (T10), SCHEMA appendix "SPEC item format".
**Depends:** T2 (allow). **Rollback:** revert. **Size:** S.

### Checkpoint 2
- [ ] `registry build start --host mac --project <sandbox-id>` on a fixture registry issues a lease and `build finish` releases it; validators run on all four real SPEC.md files and produce expected states.

---

## Phase 3 — Enforcement and orchestration

### Task 8: PreToolUse guard
**Goal:** Deny forbidden actions while a lease is active.
**Modules:** `.claude/settings.json` (PreToolUse hook for `Bash`), new `scripts/build-guard.py` (stdlib; reads hook JSON on stdin, `data/build/lease.json`, the project's contract if cached in lease), `tests/test_build_guard.py` (invoke script with command fixtures; assert exit code/deny reason), `docs/RUNBOOK.md`.
**Rules:** C §6 list. No lease → allow (guard inert outside runs). Parse errors → deny.
**Acceptance (each a test):** deny `git push origin main`, `git push --force`, `git push -f`, push to non-leased repo, `gh pr create` when `prs_open ≥ 1`, `gh pr merge` without recorded `approve`, `gh pr merge --merge` (non-squash), `gh pr close <non-builder>`, `gh issue close`, `gh repo delete`, `cat .env`, `registry record-review … --set lifecycle=`, registry `git commit` touching `registry/`; allow `git push origin push/<run>-1-x`, `gh pr create --head push/<run>-1-x`, `gh pr merge --squash <n>` after `review_verdict=approve` + `verify_passed` events, registry commit confined to `data/build/**` + `DASHBOARD.md`.
**Docs:** RUNBOOK guard section; ADR-006.
**Depends:** T5 (lease/event shapes). **Rollback:** remove hook entry (guard inert). **Size:** M.

### Task 9: Planner and reviewer agents
**Goal:** Role definitions with structured I/O.
**Modules:** `.claude/agents/build-planner.md`, `.claude/agents/build-reviewer.md`, `docs/superpowers/specs/...` prompt contracts; `build.py` gains `REVIEW_VERDICT_SCHEMA` and `parse_verdict()` (rejects non-conforming JSON).
**Planner contract:** inputs brief + SPEC + clone facts + `validate_spec` report; must output a SPEC.md whose next unchecked item validates `ready`, or a `needs_intent`/`roadmap_done` decision; must include `Verified-missing:` evidence per new item; never adds items touching never-classes.
**Reviewer contract:** fresh context, read-only tools; inputs brief, SPEC item, `git diff`, test output, contract; outputs verdict JSON; must `reject` on forbidden paths, undeclared classes, missing tests, or contradiction with non-goals/constraints; `request_changes` once for fixable issues.
**Acceptance:** (a) `parse_verdict` accepts only the schema; (b) dry-run of each agent on a sandbox transcript fixture yields conforming output (manual check recorded in docs/observations); (c) reviewer denies a diff touching `personal_data` glob without the class allowed.
**Tests:** verdict parsing; format of planner output validated by T7.
**Depends:** T7. **Rollback:** remove agent files. **Size:** S.

### Task 10: Skill rewrite + launcher
**Goal:** Thin orchestrator with fixed phase order; single launcher; retire Mac home copy and cloud branch.
**Modules:** `.claude/skills/push-project/SKILL.md` (rewrite: phases call `registry build …`, planner/reviewer subagents, Codex foreground, merge via `gh pr merge --squash`, tag, events, finish; ≤ 250 lines; no host tables — uses `registry build env`), `scripts/run-build.sh` (replaces `run-push-project-wsl.sh`; Mac + PC), `.codex`/adapter invocation kept per host from `build env`, remove `~/.claude/skills/push-project` (instruction in RUNBOOK; not a repo file), `docs/ROUTINE_SETUP.md` marked retired.
**Acceptance:** (a) skill contains no selection/ranking/brake prose — selection is `registry build start`; (b) every GitHub mutation in the skill is one of: branch push, `pr create`, `pr merge --squash`, tag push, stale-branch cleanup under `reconcile`; (c) launcher works on PC (Task Scheduler command updated in docs) and Mac; (d) `diff` between repo skill and any installed copy is not needed (copy removed).
**Tests:** shell test that `run-build.sh --dry-run` prints the resolved command; doc lint that SKILL.md references only existing `registry build` subcommands (grep test).
**Docs:** RUNBOOK, README, CLAUDE.md, memory note for PC scheduler command.
**Depends:** T5, T8, T9. **Rollback:** previous SKILL.md from git. **Size:** M.

### Task 11: `intent-refresh` skill + `brief-status`
**Goal:** Repeatable brief maintenance; visibility of brief completeness/age.
**Modules:** `.claude/skills/intent-refresh/SKILL.md` (per project: read brief + repo + merged chunks since `brief.reviewed`; propose `brief.*` updates via `registry propose`; never apply), `automation.py`/`cli.py` (`brief-status`), `mcp/server.py` (`get_brief_status`), dashboard column.
**Acceptance:** (a) `brief-status` lists complete/missing/age/open decisions for all projects; (b) skill produces proposals only (guard-independent; test by asserting no `proposal-apply` in skill text and a run on a fixture writes only `data/proposals/`); (c) proposals carry rationale citing evidence (merged PR urls, done criteria met).
**Tests:** brief-status per state; CLI/MCP parity.
**Docs:** RUNBOOK (owner PM loop), PURPOSE.md (brief as intent).
**Depends:** T2. **Rollback:** remove skill. **Size:** S.

### Checkpoint 3
- [ ] Named run against a sandbox in `shadow` mode (T12) completes: lease → plan → chunk → verify → review → PR opened, **not merged** → finish → digest; guard denies a deliberately injected `git push origin main` in the same run.

---

## Phase 4 — Observation, rollout

### Task 12: Shadow mode, sandbox harness, fault injection
**Goal:** `automation.mode: shadow` (everything but merge); updated `push-project-observe` → `build-observe` for sandbox mirrors; fault-injection scripts for the matrix in E.
**Modules:** `build.py` (`dry_run` flag in lease → guard denies `gh pr merge`), `~/.claude/skills/push-project-observe` replaced by repo-tracked `.claude/skills/build-observe/SKILL.md`, `scripts/faults/*.sh` (each stages one condition on a sandbox), `docs/observations/`.
**Acceptance:** (a) shadow run leaves PR open with reviewer verdict in the PR body; (b) each fault script has a documented expected outcome and records to `docs/observations/`; (c) observe skill proves focus repos untouched (baseline/after counts).
**Depends:** T10. **Size:** M.

### Task 13: Rollout execution (owner + builder)
**Goal:** Seed briefs and contracts, then widen autonomy per E.
**Steps:** owner applies brief proposals for `interview-prep`, `ai-news-aggregator`, `ai-engineering-markets` (drafted by `intent-refresh`); `underwriting` set `automation.mode: off` with open decision recorded; contract bootstrap chunks run in shadow; first `build` runs with `chunks_per_run: 1`; then defaults.
**Acceptance:** per E gates. **Depends:** T11, T12.

### Task 14: Documentation and ADR
**Goal:** ADR-006 "Autonomous builder: policy in code, guard-enforced execution"; PURPOSE.md operating rules 9–11 (brief is approval; runs never write curated; guard); README/CLAUDE.md; retire `FINDINGS_PUSH_PROJECT_SKILL.md` S4 (now tested); `DOCS_LOG.md` entry.
**Depends:** all. **Size:** S.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Guard matches too loosely/tightly | High | Table-driven rules + fixture tests for allow *and* deny; fail-closed |
| Planner writes items that pass the validator but are wrong | Med | Reviewer checks against brief; circuit breaker; shadow period |
| Codex timeouts on PC | Med | Foreground with max timeout; per-chunk budget; `codex_unavailable` abort |
| Registry checkout drift on Mac | Med | Preflight `registry_dirty` refuses to run |
| Scope creep into option C | Low | C remains an explicit later decision |

## Open questions → owner (also in PRD)
Default `allow` set; breaker thresholds; budgets; intent-refresh cadence; lifecycle tiebreak.
