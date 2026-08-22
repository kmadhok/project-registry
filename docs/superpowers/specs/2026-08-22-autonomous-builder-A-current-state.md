# Autonomous builder — A. Current-state assessment

**Date:** 2026-08-22 · **Branch inspected:** `codex/marathon-2026-08-21` (PR #4 open) · **Tests:** 283 passed (`.venv/bin/python -m pytest`) · **`registry validate`:** 0 errors, 3 suggestions

Companion documents: [B. PRD](2026-08-22-autonomous-builder-B-prd.md) · [C. Technical design](2026-08-22-autonomous-builder-C-technical-design.md) · [D. Implementation plan](../plans/2026-08-22-autonomous-builder-D-implementation-plan.md) · [E. Test & rollout](2026-08-22-autonomous-builder-E-test-and-rollout.md)

---

## 1. What already works

| Capability | Where | Evidence |
|---|---|---|
| Curated/observed split is structural | `storage.py`, `sync.py` write only under `data/` | Tests `test_sync.py`; no code path writes `registry/` from sync |
| Propose → approve → apply with audit trail | `proposals.py` | `apply_proposal` refuses without `approved=True`, re-validates, appends `data/audit_log.jsonl`; MCP `record_project_review` now errors loudly when unapproved (marathon T3) |
| Explainable human work queue | `queries.build_work_queue`, `signals.py` | Every item carries `reasons`, `human_priority`, `github_urgency`, `rule_ids`; priority is never invented |
| GET-only GitHub client + guarded read-only GraphQL | `github/client.py` | Test enforces no mutation verbs in MCP tool names; GraphQL allowlist fail-closed |
| Run journal + report | `data/push_runs.jsonl`, `push_runs.py`, `registry push-report` | 16 runs recorded (mac 7 / cloud 5 / pc 4); schema-validated on read |
| Brief coverage/staleness visibility | `briefs.py`, `registry briefs-status` | 95 present / 3 missing / 1 stale |
| Gear-1 / gear-2 mechanics | `.claude/skills/push-project/SKILL.md` | 7 spec PRs + 8 chunk PRs opened, all merged by the owner; exact-path gear detection, capability-absence rule, scoped-count rule all proved in the 2026-08-02 audit |
| Scheduled unattended execution | `scripts/run-push-project-wsl.sh` (PC Task Scheduler, weekdays 07:00 Chicago) | Parked run recorded `{"host":"pc","outcome":"parked"}` 2026-08-08 |

## 2. What is unsafe or ambiguous

1. **Every execution invariant is prose.** Branch naming, one-PR rule, no force-push, notes-only write-back, "never target project-registry" exist only in `SKILL.md`; the run executes with `--dangerously-skip-permissions`. The harness spec (2026-08-01) deferred enforcement until "3–5 unattended runs"; 9 unattended runs have since happened.
2. **Selection is driven by the wrong signal.** `get_attention_queue` is the human queue; today it ranks `interview-prep` first (urgency 60) purely from a non-push draft PR (#7) and stale branches — and it is the only repo in the local snapshot. Stale/partial GitHub evidence is the de-facto engineering priority.
3. **`sync --repo X` replaces the whole snapshot.** `sync.py:94-135` builds `fresh` from `targets` only; non-targeted repos are dropped. The marathon's single-repo smoke left a 1-repo snapshot, which makes `registry mismatches` report ~90 false `repo_missing` errors and blinds ranking. **New defect.**
4. **Write-back assumes a clean `main` checkout.** The Mac checkout is on `codex/marathon-2026-08-21` with 15 modified files; local `main` is 2 commits ahead of `origin/main`. A Mac run today would commit the run record onto the marathon branch.
5. **Human and agent work are conflated.** All four focus `next_action`s are human tasks ("review the PR", "answer which domain") and none affects eligibility. `underwriting-` says "no agent work adds value" in `notes` while remaining `next`/`active` with three unchecked scaffolding chunks.
6. **Two skill copies diverge.** `~/.claude/skills/push-project/SKILL.md` (Aug 1) vs repo copy (Aug 21): 137-line diff. Interactive Mac runs load the stale one.
7. **Cloud write-back contradicts the one-PR invariant** (Step 4 opens a `push/registry-run-<date>` PR). Moot once the cloud Routine is retired (it is disabled, not deleted).
8. **Evidence briefs are unowned and stale.** No producer in `src/`; `ai-engineering-markets` has none; all briefs predate every push-project PR (26 days). The skill reads them as context.
9. **Failure paths unobserved.** 0 `aborted`, 0 `amend`, 0 `empty_focus` outcomes in the journal. The five failure behaviors in `SKILL.md` have never executed.
10. **Test-command discovery is heuristic.** No focus repo has `.project-meta.yaml`; the 2026-08-02 audit showed bare `pytest` fails collection in a real repo, which would have been reported as a red baseline.
11. **`push-report` PR cache never refreshed** — 15/15 linked PRs `unknown`, merge rate shows 0 % although all were merged.
12. **MCP name guard blocks `push_`.** `FORBIDDEN_TOOL_VERBS` includes `push_`; any new tool named `record_push_outcome` fails `test_no_tool_name_suggests_an_external_mutation` (special-cased for `get_push_report`). New tools must avoid the token or the guard must be reworked.

## 3. Findings verification (the 13 supplied)

| # | Finding | Verdict | Evidence |
|---|---|---|---|
| 1 | Strong inventory, weak planning surface | Confirmed | 98 projects, 5 active; no eligibility/readiness concept in `src/` |
| 2 | Few priority/effort/blockers/relationships | Confirmed | `priority`/`effort`: 1 project; `blocked_by`: 0; `relationships`: 0 |
| 3 | `get_attention_queue` is a human queue mixing signals | Confirmed | `queries.py:422-506`; today's ordering above |
| 4 | Human and agent work conflated | Confirmed | focus YAMLs |
| 5 | `underwriting-` blocked in prose, eligible in data | Confirmed | `registry/projects/underwriting.yaml`; SPEC chunks 2–5 unchecked |
| 6 | No `.project-meta.yaml` in focus repos | Confirmed | `gh api repos/<r>/contents/.project-meta.yaml` → 404 for all four |
| 7 | Briefs broad, freshness poor | Confirmed | `briefs-status`: 95/3/1, age 26 d, revision `unknown` for all but interview-prep |
| 8 | GitHub ranking evidence stale; brake checked live | Confirmed (worse: snapshot has 1 repo) | `sync-status`: repositories 1, stale, 25 h |
| 9 | Safety rules only prose | Confirmed | no selection/brake/branch code in `src/` |
| 10 | Abort/amend/stale/blocker paths unproven | Confirmed | journal outcome counts |
| 11 | Repo vs Mac skill differ | Confirmed | `diff` 137 lines |
| 12 | Write-back assumes clean checkout | Confirmed, live | `git status` on Mac |
| 13 | Cloud needs two PRs | Confirmed | `SKILL.md` Step 4 vs Invariant 1 |

## 4. Focus repositories — context and readiness (live `gh` reads, 2026-08-22)

Rating scale: **A** build-ready today · **B** build-ready after brief + contract bootstrap · **C** needs brief, contract and roadmap split · **D** needs owner intent.

| Repo | Default branch state | Agent context | Test/CI contract | SPEC.md (agent plan) | Registry brief | Rating & blockers |
|---|---|---|---|---|---|---|
| `kmadhok/interview-prep` | Pushed 2026-08-22 (daily `drip-runner:` commits — live) | `CLAUDE.md` + `AGENTS.md`, `docs/superpowers/` | `ci.yml` declares `pytest scripts/drip_runner`, `pytest scripts/ci`, JSON validator; `scripts/drip_runner/requirements-dev.txt`; no `.project-meta.yaml` | 2/6 done; next: `pipeline_health` reporter; last item "resolve PR #7" is a **human decision** | `purpose` only; no `desired_outcome`/done criteria | **B.** Personal data (recruiter contacts, résumés) → `personal_data` change class must be declared; clone freshness matters |
| `kmadhok/AI-News-Aggregator` | Pushed 2026-08-11 | `CLAUDE.md`, many `PLAN_*.md` | root `requirements.txt`/`-dev.txt`; `tests/` (2 files); no CI; no `.project-meta.yaml` | 1/7 done; next chunk **commits a regenerated `content.db`** (generated/binary data class); chunk 6 **closes GitHub issues** (never class — must be rewritten); open non-push drafts #11, #12 | `purpose` only | **B−.** Roadmap needs policy-aware rewrite before autonomous execution |
| `kmadhok/ai_engineering_markets` | Pushed 2026-08-11 | `purpose_and_context.md`, `research/` (no CLAUDE.md) | **No code, tests, manifest, or CI**; no `.project-meta.yaml` | 0/6 done; chunk 1 is ingest + schema + requirements + pytest (too large for one PR, per owner notes) | `purpose` only; **no evidence brief**; strong non-goals exist in repo brief (no order placement, Kalshi-only) | **C.** Needs brief (done criteria, constraints), contract from scratch, chunk split |
| `kmadhok/underwriting-` | Pushed 2026-08-11 (README + SPEC only) | README says "purpose undecided" | none | 1/5 done; remaining chunks are scaffolding the owner called low-value | placeholder purpose; owner question unanswered | **D.** `needs_intent`; should not be selected |

No focus repo has an open `push/*` PR; all four are past gear 1.

## 5. Documented vs enforced

| Documented behavior | Enforced? | Gap |
|---|---|---|
| "At most one PR per run" | No | Prose only; cloud path contradicts it |
| "Branches `push/*` only; never default branch; no force-push" | No | Prose only |
| "Registry writes: `notes` only" | Partially | MCP accepts any `EDITABLE_PATHS` key; nothing rejects `lifecycle` from a run |
| "Never target project-registry" | No | Prose only |
| "Skip projects with open `push/*` PR" | No | Live `gh` call in prose |
| "Record every run" | No | Journal append is prose; crash before Step 4 leaves no line |
| "Run from latest curated intent" | Partially | `git pull --ff-only` in the PC launcher only |
| "Evidence briefs are context" | No producer | `SCHEMA.md` states ownership is unresolved |
| "CLI and MCP call the same functions" | Yes for reads | Holds; new tools must keep it |
| "GET-only GitHub" | Yes | Test-enforced; but the builder mutates GitHub through `gh` in its own shell (by design) |
| "`.project-meta.yaml` convention" | Not consumed | `DOCS_LOG.md`: documented, no code reads it |

## 6. Implications carried into the design

- Selection, eligibility, ranking, leasing, and outcome recording move into `src/project_registry` (deterministic, tested, exposed via CLI + MCP).
- Execution invariants move into a PreToolUse guard keyed on a lease file (decision **B**).
- GitHub evidence is removed from builder ranking; the clone is the source of code truth; `sync --repo` is fixed to merge.
- Evidence briefs are demoted to optional hints; the registry brief (`brief` object) becomes the intent source and `intent-refresh` its producer.
- The cloud Routine is retired in documentation; the Mac home skill copy is removed in favor of the repo copy.
