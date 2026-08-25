# Project Registry — instructions for LLM sessions

## Why this repo exists

Kanu has more side projects than attention. The scarce resource is deciding
what matters, not writing code. This repo makes that decision explicit and
machine-readable, and lets automation spend effort only where intent is
complete and the boundaries are enforced rather than merely described.

It does three things, built in this order:

1. **Records intent.** One YAML per project says what it is for, whether it is
   active, what to do next, and — for automation — a *brief*: desired outcome,
   done criteria, non-goals, constraints, open decisions.
2. **Keeps evidence honest.** GitHub state (PRs, issues, CI, activity) is
   cached and compared against intent. Evidence is never authority: when the
   two disagree the system reports a mismatch and a human resolves it.
3. **Runs an autonomous builder inside declared limits.** Kanu is the product
   manager who writes the brief. Agents derive the roadmap (`docs/SPEC.md` in
   the target repo), implement in chunks, get an independent review, and —
   once trusted — squash-merge their own PRs. The registry holds the policy:
   eligibility, allowed change classes, budgets, a per-run lease, a
   `PreToolUse` guard, an append-only journal, and a two-phase write-back.

## Who owns what

| Path | Owner | Written by | Authority |
|---|---|---|---|
| `registry/projects/*.yaml` | Kanu | human edits, or agents via `propose` → `proposal-apply --approve` | **authoritative intent** |
| `data/github/` | machine | `registry sync` only | refreshable cache, gitignored |
| `data/build/runs.jsonl`, `state.json`, `digests/` | builder | `registry build …` only | run record, git-tracked |
| `data/build/lease.json`, `STOP` | builder / owner | lease by `build start`; `STOP` by hand | ephemeral, gitignored |
| `data/proposals/`, `data/audit_log.jsonl` | registry | proposal workflow | change history |
| `data/understanding/` | unsettled | out-of-band | hints, never authority |
| `docs/SPEC.md` *(in each target repo)* | builder | planner agent, via reviewed `plan` PRs | agent-owned roadmap |
| `.project-meta.yaml` *(in each target repo)* | builder, owner-reviewed | `bootstrap_contract` chunk | executable boundary: test commands, `personal_data`, `forbidden_paths` |

Sync never writes under `registry/`. Builds never write curated YAML. Nothing
in this codebase mutates GitHub except the builder, inside its lease, on its
own `push/<run-id>-*` branches. See `docs/PURPOSE.md` for the full operating
rules.

## The autonomous builder in one paragraph

A run (`registry build start`) selects one `ready` project — brief complete,
`automation.mode` set, not paused — and takes the lease. It clones the target,
bootstraps or validates the contract, plans if no SPEC item is ready, then
loops: branch from `main` → implement (Codex) → verify against the contract →
open a `push/<run-id>-<chunk>-<slug>` PR → independent reviewer returns
`approve | request_changes | reject` → in `build` mode squash-merge and tag
`checkpoint/<run-id>-<chunk>`; in `shadow` mode leave the PR open, record
`chunk_skipped(shadow)`, and return to `main`. It stops at
`budget.chunks_per_run` (default 6), `budget.minutes_per_run` (default 120),
`data/build/STOP`, or a stop outcome. `build finish` writes state and the
digest and parks the lease as `finalize_pending`; the run commits
`data/build` + `DASHBOARD.md` to `main`, then `build finish
--confirm-writeback` releases the lease and journals `writeback_confirmed`,
which the run commits as well. Every step is an event in
`data/build/runs.jsonl`; `build_runs.EVENT_TYPES` is the vocabulary.

Modes: `shadow` = everything except merge, PRs left open for the owner;
`build` = self-merging. **Current state (2026-08-25): the three focus
projects — `interview-prep`, `ai-news-aggregator`, `ai-engineering-markets` —
are `build`; everything else is `off` or unset.** Kanu chose progress over a
proving period: safety is the branch-per-chunk + squash + `checkpoint/` tag
(one `git revert` undoes a chunk), not owner review of every PR.
`registry build-readiness <id> --shadow-gate` remains the evidence check for
any *new* project before it gets `build`.

## What is enforced vs. what is convention

Enforced by code (a violation is a denial, not a warning):

- `scripts/build-guard.py` (`PreToolUse`, active only while
  `data/build/lease.json` exists): pushes only to `push/<run-id>-*` and
  `checkpoint/<run-id>-*`; no force-push; no branch deletion outside the run's
  namespace; registry commits limited to `data/build/` + `DASHBOARD.md`; no
  merge without recorded `verify_passed` + `approve`; no merge at all in
  shadow; no secret reads; no curated-field writes via CLI or MCP. Rule
  catalogue: `docs/BUILD_GUARD.md`.
- `registry build start` refuses on a dirty registry checkout, a held lease, or
  `STOP`; it never invents intent — an incomplete brief is `needs_intent`.
- `registry build finish` refuses any outcome other than `aborted` (or
  `crashed`) once the run has a `guard_denied` event. A denial ends the run.
- `registry validate-spec` / `validate-contract`: an item whose change class
  is not in `automation.allow` is `blocked_by_policy`; `plan` and `contract`
  are always allowed; `personal_data` globs and `forbidden_paths` come from the
  target's contract.
- Circuit breaker: consecutive failures or a revert pause the project until
  `registry build resume`.

Convention (documented in the skill, checked by review, not by code):

- Reviewer verdicts are posted to the PR as a comment and journaled *before*
  the run acts on them.
- Plan and contract chunks skip tests; everything else adds or changes tests.
- Codex runs synchronously; the run never backgrounds it.

When prose and code disagree, the code is what happens. Fix the prose or open
a PR against the code — never paper over the gap inside a run.

## Where truth lives

1. `CLAUDE.md` (this file) — what Kanu wants and why. Canonical.
2. `docs/ADRs/ADR-006-autonomous-builder.md` — how it is enforced. Canonical.
3. `docs/PURPOSE.md` — registry operating rules (9–11 cover automation);
   `docs/SCHEMA.md` — fields, contract, SPEC item format; `docs/BUILD_GUARD.md`
   — guard rules; `docs/RUNBOOK.md` — hosts, launcher, shadow gate, recovery;
   `.claude/skills/push-project/SKILL.md` — the orchestration sequence.
4. `docs/superpowers/specs/*` and `docs/superpowers/plans/*` — the 2026-08-22
   design input. Historical; where they differ from 1–3, they are wrong.

## Running commands

If the `registry` entry point isn't installed, every command works as:

```bash
PYTHONPATH=src python3 -m project_registry.cli <command>
```

When MCP tools from the `project-registry` server are available in your session
(auto-discovered via `.mcp.json` in Claude Code), prefer them — they call the
same functions.

## Command cheatsheet

Read-only queries:

```bash
registry validate               # check operating rules; non-zero exit on errors
registry validate-contract <path> [--project-id <id>] # check a target repo's .project-meta.yaml contract
registry validate-spec <id> <path> # check an agent-owned docs/SPEC.md roadmap
registry list                   # whole portfolio (add --lifecycle/--active/... filters)
registry show <id>              # one project in full, with relationships and GitHub state
registry search <text>          # free-text search across curated fields
registry related <id>           # declared and inferred project relationships
registry work-queue             # ranked work; human priority and GitHub urgency separate
registry next-actions --missing # active projects lacking a next action
registry prs [--draft] [--ready] # cached open pull requests
registry issues [--label <name>] # cached issues carrying watched labels
registry attention              # GitHub work needing a human, with the rule per signal
registry mismatches             # registry intent vs GitHub state; exit 1 on errors
registry rules                  # signal and mismatch rule catalogue
registry review-queue           # projects due for a deliberate review
registry sync-status            # when GitHub evidence was last refreshed
registry briefs-status          # evidence-brief presence, age, and revision staleness
registry brief-status           # owner brief completeness/age; add --incomplete/--stale/--json
registry push-report            # push-run totals and cached PR states; add --refresh/--since/--json
registry build-report           # build-run/chunk metrics; add --since/--json
registry build-queue            # explain eligibility and builder rank; add --state/--json
registry build-readiness <id>   # explain one project's brief gaps and automation policy; add --shadow-gate
registry build resume <id>      # clear a project's build pause and failure count
registry build env --json       # resolved host, CLI, workdir, budget, and TTL
registry build context <id> --json # authoritative context for one candidate
registry build start --host <host> [--project <id>] # preflight, select, and atomically lease a run
registry build event <run> <type> # append a validated event and update the active lease
registry build finish <run> --outcome <outcome> # finalize state and digest, then park the lease
registry build finish <run> --confirm-writeback # release the lease after the registry push succeeded
registry build reconcile [--done] [--json] # plan or acknowledge crash cleanup
registry portfolio              # public-safe export; add --format json|markdown/-o
registry proposals              # pending/applied/rejected proposal records
registry proposal-show <id>     # exact before/after proposal diff
registry audit                  # recent curated-write audit records
```

State-changing (each has guardrails — respect them, don't work around them):

```bash
registry sync [--repo <owner/name>] [--no-details] [--no-branches] # refresh GitHub evidence (needs GITHUB_TOKEN)
registry import-github --owner kmadhok            # create needs_review stubs; never overwrites
registry record-review <id> [--set path=value]    # CLI applies directly; MCP record_project_review requires approved=true
registry propose <id> --set path=value --rationale "..."   # supports brief.done_criteria, brief.non_goals, brief.constraints, brief.open_decisions, brief.reviewed, automation.mode, automation.allow, automation.budget.chunks_per_run, automation.budget.minutes_per_run, automation.paused (applies nothing)
registry proposal-apply <proposal-id> --approve   # land it (re-validated, audited)
registry proposal-reject <proposal-id>            # reject a pending proposal
registry dashboard                                # regenerate DASHBOARD.md
registry mcp                                      # run the stdio MCP server
```

## Rules for agents

1. **Change curated fields through the proposal workflow** (`propose` →
   `proposal-apply --approve`), never by editing YAML directly. Proposals
   record before/after diffs, re-validate, and append to the audit log.
   `record-review` is the sanctioned shortcut for stamping a review.
2. **Never set `active` or `lifecycle` from commit recency.** GitHub activity
   is evidence, not priority. Report mismatches (`registry mismatches`); do
   not resolve them yourself.
3. **Never invent a priority or intent.** A project without `priority` reports
   null; an incomplete brief becomes a `needs_intent` proposal, not a guess.
4. **Run `registry validate` before committing** and fix any errors it reports.
   Suggestions are for the owner to weigh, not for you to auto-fix.
5. **Regenerate the dashboard after curated changes**: `registry dashboard`,
   and commit `DASHBOARD.md` alongside. Never hand-edit `DASHBOARD.md`.
6. **Never write secrets** (tokens, keys, `.env` values) into registry files or
   run output. Validation blocks known patterns; treat that as a boundary, not
   a filter to evade.
7. **No external GitHub mutations from this codebase** outside a leased build
   run. REST is GET-only; the guarded GraphQL POST accepts read queries only.
   Do not add merge/close/archive/delete capabilities.
8. **Inside a build run, work through `registry build start|event|finish` and
   stay inside the guard** (`docs/BUILD_GUARD.md`). A denial is a bug in the
   plan: it is already journaled, do not retry the command in another form,
   finish `aborted`. Write run output only under `data/build/` plus the
   generated dashboard, never to curated YAML.
9. **Journal before acting.** Every verdict, verification, PR, skip, and merge
   is an event before the next step depends on it.
10. **Read the brief via `registry build context` / `get_build_context`; never
    treat `data/understanding/*` as authority.** Evidence briefs are optional
    hints; the build context supplies approved intent and the target clone
    supplies code truth.
11. **Respect the target repo's contract.** `personal_data` globs need the
    `personal_data` class declared in the SPEC item and allowed in
    `automation.allow`; `forbidden_paths` are never touched; the brief's
    `non_goals` and `constraints` are hard limits.

## Answering "what should I work on?"

Use `registry work-queue` (or the `get_attention_queue` MCP tool). Each item
carries `reasons`, `human_priority`, and `github_urgency` — relay those
explanations rather than re-ranking by your own judgment. For the builder's
view use `registry build-queue`.

## Development

- Tests: `python3 -m pytest` (no network needed). Keep them green;
  `tests/test_docs.py` fails if this file's cheatsheet drifts from the CLI or
  ADR-006 loses a decision.
- Autonomous builder launcher: `scripts/run-build.sh`. Hosts, paths, and
  recovery: `docs/RUNBOOK.md`.
- Layout: `src/project_registry/` — `model` → `storage` → `validation` /
  `queries` / `signals` / `automation` / `contracts` / `specs` / `intent` /
  `build_runs` / `build` → `dashboard` / `portfolio` / `proposals` → `cli` /
  `mcp.server`. CLI and MCP call the same functions; keep it that way.
- Docs: `docs/PURPOSE.md` (operating model), `docs/USER_STORIES.md`
  (requirements), `docs/SCHEMA.md` (fields and validation rules),
  `docs/SETUP.md` (data import and MCP registration).
