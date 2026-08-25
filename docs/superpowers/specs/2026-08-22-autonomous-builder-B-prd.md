# Autonomous builder — B. Product requirements

> **Status (2026-08-25): historical design input.** Superseded by `CLAUDE.md` (intent) and `docs/ADRs/ADR-006-autonomous-builder.md` (enforcement). Where this document differs from those or from the code, it is wrong. Retained for the record.

**Date:** 2026-08-22 · **Owner:** Kanu Madhok · **Status:** Draft for owner review (interview-confirmed intent)

## Problem

The current `push-project` loop proves the mechanics (16 runs, 15 merged PRs) but parks on the owner at four points: spec merge, chunk-PR merge, owner questions, and spec exhaustion. The registry cannot say whether a project is *buildable*; it ranks by a human-attention queue that today is driven by stale, partial GitHub evidence; and every safety rule is prose executed under `--dangerously-skip-permissions`. Result: projects move only as fast as the owner reviews, and the system is not provably safe when he does not.

## Users and stakeholders

- **Owner as product manager (Kanu):** writes and maintains each project's *brief* (purpose, desired outcome, done criteria, non-goals, constraints, open decisions). Reads a digest. Never merges builder PRs.
- **Builder (scheduled agent pipeline):** orchestrator + planner + implementer (Codex) + independent reviewer. Derives roadmap and chunks from the brief, implements, verifies, reviews, merges, records.
- **Registry (this repo):** control plane — intent, eligibility, ranking, leases, run records, digest, policy enforcement hooks.
- **Target repositories:** own code, tests, `.project-meta.yaml` contract, agent-owned `docs/SPEC.md`.

## End-state capabilities

1. **Intent as data.** Each project carries a structured brief; the registry computes *brief completeness*. `intent-refresh` (repeatable skill) proposes brief updates through the proposal workflow; the owner approves.
2. **Machine-readable eligibility.** Every project resolves to exactly one state: `ready`, `spec_only`, `paused`, `needs_intent`, `manual_only`, `ineligible`, with reasons. Only `ready` projects are selected.
3. **Deterministic selection.** `get_build_queue` ranks `ready` projects by owner priority, then fairness (least recently advanced), then lifecycle/id. GitHub urgency is excluded.
4. **Self-directed planning.** The planner agent creates/extends `docs/SPEC.md` from the brief; `validate_spec` checks each item for acceptance criteria, test expectation, size, policy class, and open-decision dependence.
5. **Continuous building.** A run keeps going within one project — chunk → verify → independent review → squash-merge → tag → record → re-clone — until budget or a stop condition.
6. **Safety by construction.** Per-chunk atomic merges; main-always-green gates; allowed-change classes; PreToolUse guard denies forbidden actions; circuit breakers; kill switch; revert command per merged chunk.
7. **Self-healing contracts.** A missing `.project-meta.yaml` is the builder's first chunk; a broken one gets one repair attempt, then the project pauses with reason.
8. **Legible operation.** One digest per run; `registry build-report` with aborts by reason, merges, reverts, time per chunk, projects waiting on intent.

## Acceptance criteria (system level)

- AC-1 A scheduled run on the PC with a `ready` project ends with ≥1 squash-merged, tagged chunk on `main`, tests green, reviewer verdict `approve`, run record written — with no owner action.
- AC-2 A project whose brief is incomplete is never selected; the queue lists it as `needs_intent` with the missing fields; a proposal is filed at most once per missing-field set.
- AC-3 Any attempt during a run to push to a non-`push/*` ref, force-push, merge without a recorded `approve` verdict, or write a curated field other than via an approved proposal is **denied** by the guard and recorded.
- AC-4 A second run starting while a lease is held exits with outcome `lease_held` and touches nothing.
- AC-5 A run that crashes mid-chunk leaves: lease with TTL, no unmerged partial state on `main`, and the next run reconciles (closes stale `push/*`, records `crashed`).
- AC-6 Two reviewer rejections on one chunk skip the chunk; N consecutive failures pause the project; the digest says so.
- AC-7 `sync --repo X` never drops other repositories from the snapshot.
- AC-8 CLI and MCP expose identical results for every builder query/write.
- AC-9 All existing tests stay green; every new rule has a test that fails when the rule is removed.

## Durable architectural decisions

| ID | Decision | Why |
|---|---|---|
| AD-1 | Policy in registry code; orchestration in a thin skill; enforcement in a PreToolUse guard keyed on a lease (option **B**) | Violations become denials with tests, one runtime, works on Mac/PC |
| AD-2 | The registry brief is the approval; `docs/SPEC.md` is agent-owned planning state | Removes the owner from spec approval; intent stays human-owned |
| AD-3 | Per-chunk squash-merge + `checkpoint/<run>-<n>` tag; fresh clone of `main` before each chunk | Atomic, independently revertible units; reviewer sees small diffs |
| AD-4 | Builder ranking excludes GitHub urgency | Finding 3/8: stale evidence was the de-facto priority |
| AD-5 | Runtime automation state lives in `data/build/`; curated `brief`/`automation` policy lives in `registry/` | Preserves the curated/observed boundary; runs never rewrite YAML |
| AD-6 | One project per run, many chunks, budget-bounded | Throughput without losing blast-radius legibility |
| AD-7 | Eligibility is opt-in via `automation.mode`, never derived from lifecycle or activity | Human intent authoritative over GitHub activity |
| AD-8 | Evidence briefs are optional hints, not inputs to eligibility or ranking | Unowned and stale; clone is code truth |
| AD-9 | Cloud Routine retired; PC scheduled is primary, Mac interactive; single canonical skill in-repo | Removes the two-PR contradiction and the copy drift |

## Boundaries

**Always (no approval):** select a `ready` project; clone; bootstrap or repair `.project-meta.yaml`; create/extend/amend `docs/SPEC.md`; implement chunks within allowed classes; run tests/lint; open `push/*` PRs; independent review; squash-merge approved chunks; tag; record runs; file proposals for intent gaps; send digest.

**Policy-gated (per-project `automation.allow`, default listed):** add/change dependencies (**allowed by default**); CI workflow changes (**allowed**); regenerate declared generated files/binary data (**allowed**); change public API or user-facing behavior (**opt-in**); schema/data migrations (**opt-in**); touch files flagged `personal_data` in the contract (**opt-in**).

**Never:** deploy; outbound messages; payments; mutate external systems; read or quote secret values; commit to a default branch directly; force-push; delete branches outside `push/*`; close/edit issues or PRs not created by the builder; choose a project's domain, priority, lifecycle, or `active`; target `project-registry`; rewrite curated YAML outside the proposal workflow.

## Non-goals

- Multi-project runs or parallel builders.
- Replacing GitHub as code/PR truth, or mirroring target-repo content into the registry.
- Automatic production deploys, release publishing, or notifications to third parties.
- A full Agent-SDK harness (option C) in this iteration; it remains the escalation path if the guard proves insufficient.
- Generating product intent for thin projects.

## Risks

| Risk | Mitigation |
|---|---|
| Reviewer agent rubber-stamps | Reviewer runs in fresh context with a structured rubric against the brief; merge denied without its `approve` JSON; sampled owner audits from the digest |
| Roadmap drift from intent | `validate_spec` rejects items that contradict non-goals/constraints; `intent-refresh` proposes brief updates; digest shows roadmap changes |
| Guard bypass (e.g. `python -c` that shells out) | Guard matches command text *and* the lease restricts which repo/branch is valid; post-run reconciliation compares GitHub reality with the journal and flags discrepancies |
| Broken `main` after merge | Merge gates: baseline green or attributed, chunk tests, CI if present; revert command per tag; circuit breaker |
| Token/time runaway | Hard per-run budget; per-chunk Codex timeout; `chunks_per_run` cap |
| Stale clone on live repos (interview-prep) | Fresh clone per chunk; merge uses `--squash` on current `main`; rebase-on-conflict once, else reject chunk |

## Success metrics

- Merged chunks per week on `ready` projects; reverts per 100 merges (< 5).
- Reviewer rejections per 100 chunks, by reason.
- Aborts by reason; crashed runs reconciled automatically (100 %).
- Projects in `needs_intent` and median time-to-brief.
- Guard denials per run (expect near zero; any spike is a prompt-quality signal).
- Owner minutes per week on the loop (target: digest reading only).

## Open questions (owner)

1. Default `automation.allow` set — accept (dependencies, ci, generated_data) as default-on?
2. Circuit-breaker threshold N (proposed 3 consecutive failures or 1 revert).
3. Per-run budget defaults (proposed 120 min, 6 chunks on PC).
4. Should `intent-refresh` run on the same schedule (weekly) or on demand only?
5. Keep `lifecycle: next/now` as a soft signal in ranking (proposed yes, as a tiebreak only)?
