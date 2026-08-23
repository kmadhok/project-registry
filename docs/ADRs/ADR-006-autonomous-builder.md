# ADR-006 — Autonomous builder: policy in registry code, guard-enforced execution, self-merge per chunk

**Status:** Accepted  
**Date:** 2026-08-22

## Context

The 2026-08-22 assessment found that the earlier `push-project` loop had proved its core mechanics but still depended on owner merges and questions, selected work using stale or partial GitHub evidence, duplicated behavior across environments, and expressed critical safety rules only as prose while running with broad permissions. The builder therefore needed an owner-controlled approval surface, deterministic eligibility and ranking, durable run state, executable safety boundaries, independent verification, and a single cross-platform orchestration path.

## Decision

- **AD-1 — Put policy in registry code, orchestration in a thin skill, and enforcement in a lease-aware guard.** Eligibility and ranking are implemented in `src/project_registry/automation.py`; roadmap and contract policy in `src/project_registry/specs.py` and `src/project_registry/contracts.py`; lifecycle, journals, leases, state, digests, and verdict parsing in `src/project_registry/build_runs.py` and `src/project_registry/build.py`. `.claude/skills/push-project/SKILL.md` sequences those operations. `.claude/settings.json` invokes `scripts/build-guard.py` as a `PreToolUse` hook for Bash and registry MCP calls.
- **AD-2 — Treat the registry brief as approval and `docs/SPEC.md` as agent-owned planning state.** `Brief` and `Automation` live in `src/project_registry/model.py` and `registry/schema/project.schema.json`; changes to `purpose`, `desired_outcome`, and `brief.*` use `src/project_registry/proposals.py`. `src/project_registry/specs.py` validates the planner-owned roadmap, and `.claude/agents/build-planner.md` derives it from the approved brief.
- **AD-3 — Ship atomic chunks through squash merge, checkpoint tag, and a fresh clone of `main`.** `.claude/skills/push-project/SKILL.md` creates one `push/<run-id>-*` branch and PR per chunk, requires verification and independent review, squash-merges, writes `checkpoint/<run-id>-<n>`, and refreshes `main` before continuing. `scripts/build-guard.py` enforces branch, merge-method, verification, verdict, and tag scope.
- **AD-4 — Exclude GitHub urgency from builder ranking.** `src/project_registry/automation.py::rank_key` orders eligible work by owner priority, last successful build, lifecycle, and project id. GitHub state is consulted only to exclude archived repositories.
- **AD-5 — Separate runtime state from curated policy.** `src/project_registry/storage.py`, `src/project_registry/build.py`, and `src/project_registry/build_runs.py` keep journals, state, digests, and the active lease under `data/build/`; `brief` and `automation` remain under `registry/projects/`. The guard blocks direct curated writes during a run.
- **AD-6 — Run one project at a time, with multiple budget-bounded chunks.** `src/project_registry/build_runs.py::begin_run` creates one atomic lease containing one project and its budget. `AutomationBudget` in `src/project_registry/model.py` defines chunk and minute limits, which `.claude/skills/push-project/SKILL.md` applies to its chunk loop.
- **AD-7 — Make autonomous eligibility explicit and opt-in.** `src/project_registry/model.py::AutomationMode` defines `off`, `shadow`, `spec_only`, and `build`; `src/project_registry/automation.py::classify` selects only complete, unpaused `build` or `shadow` briefs and never infers consent from lifecycle or activity.
- **AD-8 — Keep evidence briefs non-authoritative.** `src/project_registry/build_runs.py::get_build_context` returns the approved brief and an explicit `do_not_read` warning for `data/understanding/*`; `.claude/skills/push-project/SKILL.md` uses that context and a fresh target clone as its authorities.
- **AD-9 — Use one in-repository skill across the supported hosts.** `scripts/run-build.sh` is the single PC-scheduled and Mac-interactive launcher, `registry build env` resolves host-specific constants, and `.claude/skills/push-project/SKILL.md` is the canonical orchestration definition. The cloud routine is retired.

## Consequences

An autonomous run can no longer write curated registry YAML, push target changes outside its leased `push/<run-id>-*` namespace, force-push, or merge a chunk without both recorded verification and an independent `approve` verdict. These restrictions are denials, not recommendations. Each autonomous project now requires a complete owner brief, a valid or bootstrapped `.project-meta.yaml` contract, and review of the per-run digest, including merged chunks, tags, rejections, guard denials, and pause state.

The owner retains product decisions and proposal approval. The builder gains authority to plan and self-merge only inside the declared brief, contract, allowed change classes, run budget, and guard boundaries. Runtime data grows as an append-only operational record under `data/build/`, and recovery must reconcile that record with GitHub after a crash.

## Alternatives rejected

- **A — Prose-only orchestration and safety rules.** Rejected because broad execution permissions made instructions advisory, difficult to test, and vulnerable to drift between host-specific copies.
- **C — Full Agent SDK harness.** Rejected for this iteration because the registry functions, thin skill, lease, and fail-closed guard provide a smaller path to the required behavior on both hosts. Revisit option C if observation or fault-injection shows that the guard can be bypassed, reconciliation cannot reliably reconstruct external state, structured agent handoffs are not dependable, or orchestration complexity outgrows a fixed skill.
