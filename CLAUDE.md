# Project Registry — instructions for LLM sessions

This repository is a control plane for Kanu's project portfolio. It holds two
kinds of data with different owners, and the distinction governs everything you
do here:

- `registry/projects/*.yaml` — **curated human intent** (purpose, lifecycle,
  active flag, priority, next action). Authoritative. Git-tracked.
- `data/github/` — **observed GitHub evidence** (PRs, issues, CI, activity),
  written only by `registry sync`. A refreshable cache, gitignored, never authoritative.
- `data/understanding/` — **out-of-band evidence briefs**, currently produced
  outside the registry and git-tracked; their long-term owner is not settled.

Sync never writes under `registry/`; generated data must never overwrite
curated fields. See `docs/PURPOSE.md` for the full operating rules.

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
registry build-readiness <id>   # explain one project's brief gaps and automation policy
registry build resume <id>      # clear a project's build pause and failure count
registry build env --json       # resolved host, CLI, workdir, budget, and TTL
registry build context <id> --json # authoritative context for one candidate
registry build start --host <host> [--project <id>] [--force-named] [--json]
registry build event <run> <type> [--chunk-id <id>] [--detail key=value]
registry build finish <run> --outcome <outcome> [--summary <text>]
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
   `proposal-apply --approve`), not by editing YAML directly. Proposals record
   before/after diffs, re-validate, and append to the audit log. Direct YAML
   edits are for the human owner. `record-review` is the sanctioned shortcut
   for stamping a review.
2. **Never set `active` or `lifecycle` from commit recency.** GitHub activity
   is evidence, not priority. If evidence and intent disagree, report the
   mismatch (`registry mismatches`) — do not resolve it yourself.
3. **Never invent a priority.** A project without `priority` reports null;
   present it that way.
4. **Run `registry validate` before committing** and fix any errors it reports.
   Suggestions are for the owner to weigh, not for you to auto-fix.
5. **Regenerate the dashboard after curated changes**: `registry dashboard`,
   and commit `DASHBOARD.md` alongside. Never hand-edit `DASHBOARD.md`.
6. **Never write secrets** (tokens, keys, `.env` values) into registry files.
   Validation blocks known patterns; treat that as a boundary, not a filter to
   evade.
7. **No external GitHub mutations from this codebase.** REST is GET-only; the
   guarded GraphQL POST accepts read queries only. Do not add
   merge/close/archive/delete capabilities.
8. **Honor the autonomous build guard while a lease is active.** The
   `PreToolUse` rules in `docs/BUILD_GUARD.md` are safety boundaries; do not
   bypass, disable, or evade them.
8. **Autonomous builds go through `registry build start/event/finish`; never
   write curated YAML from a run.** Run output belongs only under
   `data/build/` (plus the generated dashboard workflow).

## Answering "what should I work on?"

Use `registry work-queue` (or the `get_attention_queue` MCP tool). Each item
carries `reasons`, `human_priority`, and `github_urgency` — relay those
explanations rather than re-ranking by your own judgment.

## Development

- Tests: `python3 -m pytest` (no network needed). Keep them green.
- Autonomous builder launcher: `scripts/run-build.sh` (PC scheduled, Mac interactive).
- Layout: `src/project_registry/` — `model` → `storage` → `validation` /
  `queries` / `signals` → `dashboard` / `portfolio` / `proposals` → `cli` /
  `mcp.server`. CLI and MCP call the same query functions; keep it that way.
- Docs: `docs/PURPOSE.md` (operating model), `docs/USER_STORIES.md`
  (requirements), `docs/SCHEMA.md` (fields and validation rules),
  `docs/SETUP.md` (data import and MCP registration).
