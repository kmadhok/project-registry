# Autonomous builder — C. Technical design

> **Status (2026-08-25): historical design input.** Superseded by `CLAUDE.md` (intent) and `docs/ADRs/ADR-006-autonomous-builder.md` (enforcement). The event vocabulary, finish sequence, and per-chunk clone described below are not current; see `build_runs.EVENT_TYPES` and ADR-006 AD-3/AD-10. Retained for the record.

**Date:** 2026-08-22 · Implements [B. PRD](2026-08-22-autonomous-builder-B-prd.md) · Layout follows the existing `model → storage → validation/queries/signals → cli/mcp` stack.

## 0. Shape in one picture

```
scheduler (PC Task Scheduler / Mac manual)
  └─ scripts/run-build.sh            host detect, PATH, budget env, `claude -p "/push-project"`
       └─ skill: push-project        THIN orchestrator — fixed phase order, no policy
            ├─ registry build start  → lease + candidate + context      (registry code)
            ├─ planner subagent      → ensures SPEC.md has a ready item  (model)
            ├─ Codex                 → implements chunk                  (model)
            ├─ registry verify gates → baseline/tests/lint/diff stats   (registry code)
            ├─ reviewer subagent     → structured verdict               (model, fresh ctx)
            ├─ gh pr create / merge  → squash + tag                     (guard-checked)
            ├─ registry build event  → journal                          (registry code)
            └─ registry build finish → state, digest, commit data       (registry code)
  guard: .claude/settings.json PreToolUse → scripts/build-guard.py (reads the lease, denies)
```

## 1. Registry data model

### 1.1 Curated additions (`registry/projects/*.yaml`, schema + `model.py`)

```yaml
brief:                                  # owner-owned intent; proposal-editable
  done_criteria: ["indexed count == collected count", "..."]   # ≥1 required for build
  non_goals: ["no new content sources", "..."]
  constraints: ["local CLI only", "no third-party republishing"]
  open_decisions:                       # any item with status=open blocks `ready`
    - question: "Which underwriting domain?"
      status: open | answered
      answer: null
  reviewed: 2026-08-22                  # brief freshness date
automation:                             # owner-owned policy; proposal-editable
  mode: off | shadow | spec_only | build      # default off
  allow: [dependencies, ci, generated_data]    # change classes beyond tier 0
  budget: {chunks_per_run: 6, minutes_per_run: 120}
  paused: false                          # owner pause (machine pauses live in data/)
```

`purpose` and `desired_outcome` stay top-level (already exist). **Brief completeness** (computed, `validation.py` + `automation.py`): `purpose` ∧ `desired_outcome` ∧ `brief.done_criteria` non-empty ∧ no `open_decisions[status=open]` ∧ `repo` set. Validation rule `brief_incomplete_for_build` (ERROR when `automation.mode in {build, shadow}` and incomplete). Suggestion `automation_without_focus_lifecycle` when mode=build and lifecycle ∉ {now,next,maintained}.

`proposals.EDITABLE_PATHS` gains `brief.*`, `automation.*` (lists coerce like `tags`; `open_decisions` as JSON list). Falsy round-trip already preserved (marathon T2).

### 1.2 Machine state (`data/build/`, machine-written, git-tracked except lease)

| File | Owner | Content |
|---|---|---|
| `data/build/lease.json` (gitignored) | `begin_build_run` | `{run_id, host, pid, project_id, repo, started_at, ttl_seconds, prs_opened, merges, status}` — atomic create (`O_EXCL`) |
| `data/build/runs.jsonl` | `record_build_event`/`finish_build_run` | append-only events: `run_started, candidate_selected, contract_bootstrapped, chunk_started, pr_opened, verify_passed/failed, review_verdict, merged, reverted, chunk_rejected, chunk_skipped, run_finished(outcome)`; each carries `run_id, ts, host, project_id, chunk_id, pr_url, tag, detail` |
| `data/build/state.json` | `finish_build_run` | per project: `last_run_at, last_success_at, consecutive_failures, paused_reason, paused_at, chunks_merged_total, needs_intent_proposal_id` |
| `data/build/digests/<run_id>.md` | `finish_build_run` | human digest |
| `data/push_runs.jsonl` | legacy | kept read-only; `build-report` reads both |

Curated YAML is **never** rewritten by a run (replaces today's `record_project_review` notes append). Dashboard gains a "Build" section rendered from `state.json` + `runs.jsonl`.

### 1.3 Target-repo contract — `.project-meta.yaml` (v1)

```yaml
schema: 1
registry_id: ai-news-aggregator
runtime: {kind: python, version: "3.11"}        # or node/…; kind + version
package_manager: pip                             # pip|uv|poetry|npm|pnpm|cargo|…
setup: ["python3 -m venv .venv", ".venv/bin/pip install -r requirements-dev.txt"]
test: [".venv/bin/python -m pytest -q"]          # ≥1 required; exit 0 = green
lint: []                                         # optional; must pass if declared
typecheck: []                                    # optional
verify: []                                       # repo-specific extra checks
max_test_minutes: 15
network: {allowed: false}                        # tests may not need network
secrets_required: []                             # names only, never values
services: []                                     # e.g. postgres — if non-empty, builder cannot run → paused(reason=services)
generated_files: ["content.db"]                  # only regenerable via `generate`
generate: []                                     # commands allowed to touch generated_files
personal_data: ["Roles/**", "Pipeline.md"]       # globs → `personal_data` change class
forbidden_paths: [".env", "secrets/**"]          # guard + reviewer deny any diff here
deploy: none                                     # only value accepted in v1
```

Validation in registry (`contracts.py`, JSON-schema-like checks, `registry validate-contract <file>`). **Missing** contract → builder's first chunk is `bootstrap_contract` (planner discovers commands from CI/Makefile/pyproject/package.json/CLAUDE.md, verifies each command runs, opens PR, reviewer checks, merge). **Wrong** contract (setup/test fails with a collection/command error at baseline, not a test failure) → one `repair_contract` chunk; second failure → `paused(reason=contract_broken)`.

## 2. Eligibility and ranking (`src/project_registry/automation.py`)

```python
classify(project, state, today) -> Eligibility(state, reasons[])
  ineligible   : no repo | id == "project-registry" | lifecycle in {archived, superseded} | github archived
  manual_only  : automation.mode == off
  paused       : automation.paused | state.paused_reason | blocked_by set
  needs_intent : mode in {build, shadow, spec_only} and not brief_complete   (reasons = missing fields / open decisions)
  spec_only    : mode == spec_only and brief_complete
  ready        : mode in {build, shadow} and brief_complete      (shadow flagged `dry_run=True`)

rank(ready) key = (-priority_weight, last_success_at or epoch, lifecycle_order, id)
```

GitHub urgency, snapshot freshness and evidence briefs are **not** inputs. `blocked_by` maps to `paused(reason=blocked_by)`; `open_decisions` to `needs_intent`. Output is explainable: every candidate carries `reasons` and `rank_key`.

## 3. MCP / CLI contracts

All tools share the existing envelope (`source_timestamps`), call the same functions as the CLI, and avoid the `push_` token. Read tools: R. Write tools (data/ only): W.

| Tool (MCP) | CLI | R/W | Input | Output (key fields) | Errors |
|---|---|---|---|---|---|
| `get_build_queue` | `registry build-queue [--json]` | R | `{limit?}` | `{candidates:[{project_id, state, reasons, rank_key, brief_freshness_days, contract_status: unknown}], by_state:{…}}` | — |
| `validate_project_readiness` | `registry build-readiness <id>` | R | `{project_id}` | `{state, reasons, brief:{complete, missing[]}, policy:{allow, budget}, paused_reason}` | unknown id |
| `get_build_context` | `registry build-context <id>` | R | `{project_id}` | `{brief, purpose, desired_outcome, automation, repo, default_branch?, contract_expectations, last_runs[5], state, do_not_read: ["data/understanding/*"]}` | unknown id; not ready → still returns with `state` |
| `begin_build_run` | `registry build start --host pc [--project id] [--run-id]` | W | `{host, project_id?, ttl_seconds?}` | `{run_id, lease, candidate:{…context…}}` or `{outcome: lease_held|registry_dirty|no_candidate, detail}` | lease held (not expired); registry checkout dirty/not on main; named project not ready (unless `--force-named`) |
| `validate_spec` | `registry validate-spec <id> <file>` | R | `{project_id, spec_text}` | `{items:[{index, checked, title, ready, problems[], classes[], size, needs_intent}], next_ready_index, structure_ok, problems[]}` | malformed |
| `record_build_event` | `registry build event <run-id> <type> [--json k=v]` | W | `{run_id, type, detail}` | `{recorded: true, seq}` | unknown/finished run; lease mismatch |
| `finish_build_run` | `registry build finish <run-id> --outcome …` | W | `{run_id, outcome, summary}` | `{state_after, digest_path, circuit_breaker: {…}}` | unknown run |
| `reconcile_build_runs` | `registry build reconcile` | W | `{}` | `{expired_leases[], stale_push_branches[], actions[]}` | — |
| `get_build_report` | `registry build-report [--since]` | R | `{since?}` | runs/chunks/merges/reverts/rejections by reason, time per chunk, guard denials, needs_intent list | — |
| `get_brief_status` | `registry brief-status` | R | `{}` | per project: complete?, missing, age, open decisions | — |
| `refresh_github` (existing, **fixed**) | `registry sync --repo` | W (cache) | | targeted sync merges into previous snapshot | |

Example — `begin_build_run`:

```json
{"host": "pc"}
→
{"run_id": "20260825T120001Z-pc-a1b2",
 "lease": {"project_id": "ai-news-aggregator", "repo": "kmadhok/AI-News-Aggregator", "ttl_seconds": 10800},
 "candidate": {"state": "ready", "rank_key": [0, "2026-08-11T...", 1, "ai-news-aggregator"],
   "reasons": ["automation.mode=build", "brief complete", "last success 14 d ago"],
   "brief": {"done_criteria": ["…"], "non_goals": ["…"], "constraints": ["…"], "open_decisions": []},
   "automation": {"allow": ["dependencies", "ci", "generated_data"], "budget": {"chunks_per_run": 6, "minutes_per_run": 120}},
   "contract_expectations": {"path": ".project-meta.yaml", "required": true, "bootstrap_if_missing": true}},
 "source_timestamps": {"registry_loaded_at": "…", "github_fetched_at": null, "github_age": "never", "github_stale": true}}
```

Approval requirements: `brief.*`/`automation.*` changes → `propose_project_update` + owner `apply … approved=true` (unchanged machinery). `reconcile`, `begin/record/finish` write only `data/build/`.

## 4. Spec readiness (`specs.py`)

Parse `docs/SPEC.md` (existing format: `## Remaining work` checklist). For each unchecked item, the deterministic validator requires: an **Acceptance** line, a **Tests** line, a **Size** tag (`S|M`), and **Classes** tag (subset of `dependencies, ci, generated_data, public_api, migrations, personal_data, plan, contract`). Flags: forbidden verbs (`deploy`, `send`, `email`, `pay`, `close issue`, `delete repo`, `secret`) → `never_class`; classes not in `automation.allow` → `blocked_by_policy`; references to an open decision → `needs_intent`; overlap with `brief.non_goals` tokens → `contradicts_non_goal`; already-implemented check is left to the planner (model) with the validator requiring a `Verified-missing:` line. Unready outcomes: planner rewrites the item (same PR flow, class `plan`); `needs_intent` → registry proposal (once); `blocked_by_policy` → digest line, item skipped.

New item format (planner writes, validator enforces):

```markdown
- [ ] Close the 24-of-44 index gap using the existing `index` command
      Acceptance: content_index rows == collected markdown files; regression test asserts equality
      Tests: tests/test_index_gap.py
      Size: S   Classes: generated_data
      Verified-missing: `python run.py index` exists; test does not (grep tests/ for index_gap → 0)
```

## 5. Freshness model

| Input | Refreshed when | Source of truth for |
|---|---|---|
| Registry YAML + `data/build/state.json` | `git pull --ff-only` in launcher; `begin_build_run` refuses a dirty/non-main checkout | eligibility, ranking, policy |
| Target clone | fresh `--depth 50` clone of default branch before **each chunk** | code facts, SPEC.md, contract, baseline |
| GitHub snapshot | `refresh_github(repos=[target])` after selection (merging, not replacing) | open PRs/issues listing only; never ranking |
| Brief freshness | `brief.reviewed`; `get_brief_status` reports age; `intent-refresh` proposes | intent |
| Evidence briefs | not refreshed by builder; optional hint; `intent-refresh` may consume | nothing authoritative |

Disagreement rule: clone > registry brief > snapshot > evidence brief, per domain above. The registry stores indexes and pointers (PR urls, tags, SHAs), never copies of target content.

## 6. Execution state machine

```
START ─ preflight(registry clean on main, tools present) ─fail→ FINISH(outcome=preflight_failed)
  └ LEASE ─held→ FINISH(lease_held)
    └ SELECT ─none→ FINISH(no_candidate)
      └ CLONE(main) ─fail→ FINISH(clone_failed)
        └ CONTRACT? ─missing→ CHUNK(bootstrap_contract) ; broken→ CHUNK(repair_contract) once, else PAUSE(contract_broken)
          └ BASELINE(setup, test) ─red & unattributed→ PAUSE(baseline_red) ; red & attributed→ note
            └ PLAN (planner ensures next ready item; validate_spec) ─none ready→ FINISH(roadmap_done | needs_intent | blocked_by_policy)
              └ IMPLEMENT(Codex) ─invoke error→ retry once → ABORT_CHUNK
                └ VERIFY(tests no new failures, lint, forbidden paths, class check) ─fail→ fix once → REJECT_CHUNK
                  └ REVIEW(reviewer subagent, fresh ctx) ─request_changes→ revise once → ─reject→ REJECT_CHUNK
                    └ MERGE(gh pr create on push/<run>-<n>-<slug>; gh pr merge --squash; git tag checkpoint/<run>-<n>) ─conflict→ re-clone & retry once → REJECT_CHUNK
                      └ RECORD(merged) → budget/stop? ─no→ CLONE(main) ; ─yes→ FINISH(outcome)
REJECT_CHUNK: delete branch, record `chunk_rejected(reason)` (SPEC.md untouched; skip state lives in data/build/state.json), consecutive_failures++ → ≥N → PAUSE
FINISH: release lease, state.json, digest, commit data/build + DASHBOARD.md on main, push (pull --rebase once on non-ff), notify
```

Guard rules (PreToolUse, `scripts/build-guard.py`, only active while `data/build/lease.json` exists and names this run):

- `git push` allowed only to `origin push/<run_id>-*` of the leased repo, never `--force*`, never `main|master|<default>`; registry repo push allowed only to `main` with a diff confined to `data/build/**`, `DASHBOARD.md`.
- `gh pr create` only with `--head push/<run_id>-*` in the leased repo and only if `lease.prs_open == 0`.
- `gh pr merge` only `--squash` on a PR whose number is in the lease with `review_verdict == approve` and `verify == passed` (both recorded via `record_build_event`).
- Deny: `gh pr close|merge` on non-builder PRs, `gh issue *` mutations, `gh repo delete|archive|edit`, `git push --delete` outside `push/*`, `git commit` in the registry outside `data/build/**`, any `registry propose`/`proposal-apply`/`record-review` with `lifecycle|active|priority|next_action|brief|automation` (intent fields are proposal-only and owner-applied), secret-looking env dumps (`cat .env`, `printenv | grep KEY`).
- Deny any command touching `forbidden_paths` from the contract.
- Fail-closed: guard error → deny.

## 7. Failure and recovery

| Case | Behavior | Record |
|---|---|---|
| Duplicate scheduled run | `begin_build_run` sees unexpired lease → exit | `lease_held` |
| Crash mid-chunk | lease expires (TTL 3 h); next run `reconcile`: closes open `push/<dead-run>-*` PRs, deletes branches, marks run `crashed` | events |
| Merge succeeded, registry write-back failed | tags + PR bodies carry `run_id`; `reconcile` rebuilds events from GitHub (`gh pr list --search run_id`) | `reconciled` |
| Registry push non-ff | `git pull --rebase` once; else leave `finalize_pending` in lease for next run | event |
| Branch-name collision | names include `run_id`; stale `push/*` swept at start | event |
| Codex unavailable/login | retry once; abort run (no inline fallback on PC/Mac) | `codex_unavailable` |
| Baseline red | attribute: collection/command error → contract repair; test failure → pre-existing, note & continue with "no new failures" | event |
| Reviewer rejects twice | skip chunk, `consecutive_failures++` | `chunk_rejected` |
| N consecutive failures / a revert | `paused(reason)`; owner resumes with `registry build resume <id>` | state |
| Kill switch | `data/build/STOP` file or `automation.paused` → no new chunk; current chunk finishes verify/review then stops | `stopped` |

## 8. Environment adapters

Single launcher `scripts/run-build.sh` (replaces `run-push-project-wsl.sh`): detects host (`mac|pc`), exports PATH/timeouts, `git pull --ff-only`, `claude -p "/push-project" --mcp-config .mcp.json`, logs to `$REGISTRY_ROOT/../build-work/`. `registry build env` prints the resolved constants (`REGISTRY_CLI`, `CODEX_BIN`, `WORKDIR`, budget) so the skill has no host tables. Unavoidable differences (Codex binary path, venv presence) live in `run-build.sh` only. Canonical skill: `.claude/skills/push-project/SKILL.md`; Mac home copy deleted. Cloud Routine: retired (documented); no cloud branch in the skill.

## 9. Agents (roles)

| Role | Type | Input | Output |
|---|---|---|---|
| Orchestrator | the skill | phase order | drives commands |
| Planner | subagent `.claude/agents/build-planner.md` | brief, SPEC.md, clone facts, validate_spec report | updated SPEC.md (plan PR) |
| Implementer | Codex (`codex exec`, foreground) | task spec with verified facts | diff |
| Reviewer | subagent `.claude/agents/build-reviewer.md`, fresh context, read-only | brief, SPEC item, diff, test output, contract | JSON `{verdict: approve|request_changes|reject, reasons[], risk_flags[], classes_seen[]}` |
| Intent-refresh | skill `.claude/skills/intent-refresh/SKILL.md` | brief, repo, merged chunks | proposals (brief.*) |

## 10. Security boundaries

- Secrets: never read; contract lists names only; guard denies dumps; `validation.credential_material` still scans registry text.
- GitHub mutations only via `gh` in the leased repo on `push/*`; registry code stays GET-only.
- Guard fail-closed; lease TTL; reconciliation compares GitHub with the journal.
- Personal-data globs require opt-in class; reviewer must flag any diff touching them.
