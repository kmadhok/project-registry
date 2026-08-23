# Project Registry

A private control plane for understanding, prioritizing, and maintaining Kanu's project portfolio.

The registry combines two kinds of information:

- **Human intent:** why a project exists, whether it is active, what outcome matters, and what should happen next.
- **GitHub evidence:** open pull requests, issues, recent activity, CI state, and other repository signals.

Human intent remains authoritative. Automation may surface evidence and suggest actions, but it must not silently change priorities, close work, or archive repositories.

## How the pieces fit

```
registry/projects/*.yaml     curated intent      hand-edited, git-tracked, authoritative
data/github/snapshot.json    observed evidence   machine-written, refreshable, timestamped
data/understanding/*.json    evidence briefs     out-of-band, coverage/staleness reported
data/proposals/*.json        pending changes     proposed but not applied
```

Sync writes only under `data/`. Nothing in that path can touch `registry/`, which is what makes "generated data must never overwrite human-curated fields" a structural guarantee rather than a convention.

## Quick start

```bash
pip install -e .              # or: PYTHONPATH=src python3 -m project_registry.cli ...

registry validate             # check the registry against its own operating rules
registry validate-spec <project-id> <path>  # check an agent-owned docs/SPEC.md roadmap
registry list                 # the whole portfolio
registry show project-registry
registry dashboard            # regenerate DASHBOARD.md
```

To pull in GitHub evidence, export a token with read access ([docs/SETUP.md](docs/SETUP.md) covers creating one) and run:

```bash
export GITHUB_TOKEN=...
registry import-github --owner kmadhok   # create stubs marked needs_review
registry sync [--no-branches]            # refresh observed state; optionally skip branch capture (read-only)
registry sync --repo owner/repo          # refresh one repo while preserving all other snapshot entries
registry prs                             # open PRs across the portfolio
registry attention                       # work needing a human, with the rule behind each signal
registry mismatches                      # where registry intent and GitHub disagree
registry push-report                     # push-run totals and cached linked-PR states
registry build-report                    # autonomous build-run and chunk metrics
registry build-queue [--state ready]     # explain eligibility and rank every project
registry build-readiness <id>            # explain one project's brief and automation policy
registry brief-status [--incomplete] [--stale] # brief completeness and owner-review age
registry build resume <id>               # clear a project's build pause
registry build env --json                # resolve host, CLI, workdir, budget, and TTL
registry build context <id> --json       # authoritative context for one candidate
registry build start --host mac --json   # preflight, select, and atomically lease a run
registry build event <run> <type>         # journal progress and update lease counters
registry build finish <run> --outcome completed # state, digest, and lease release
registry build reconcile [--done]         # plan/acknowledge expired-run cleanup
```

Day-to-day:

```bash
registry work-queue           # ranked work; human priority and GitHub urgency shown separately
registry next-actions --missing
registry review-queue         # what is due for a deliberate look
registry briefs-status        # which repo-backed projects have current/stale/unknown briefs
registry brief-status         # owner brief completeness, open decisions, and staleness
registry record-review my-project --set lifecycle=next
```

Autonomous building uses the canonical `.claude/skills/push-project/SKILL.md`
orchestrator and the cross-platform `scripts/run-build.sh` launcher. The launcher
runs scheduled builds on PC and supports interactive Mac runs; use
`scripts/run-build.sh --dry-run` to inspect its resolved Claude command.

Changing curated fields from a script or an agent goes through propose-then-apply:

```bash
registry propose my-project --set purpose="..." --rationale "clarified after review"
registry proposal-show my-project-20260725120000     # exact before/after
registry proposal-apply my-project-20260725120000 --approve
registry audit
```

## MCP server

```bash
registry mcp                  # JSON-RPC 2.0 over stdio
```

33 tools: project queries, next actions and review recommendations, portfolio-wide PR and issue views, build lifecycle and readiness tools, a GitHub refresh, and the propose/apply pair. Every response carries `source_timestamps` so a client can tell live data from cached data. Build lifecycle writes are confined to `data/build/`; no tool mutates GitHub.

**Claude Code discovers the server automatically** via [`.mcp.json`](.mcp.json) when this repo is open. Claude Desktop and global registration are covered in [docs/SETUP.md](docs/SETUP.md). Sessions without MCP still work: [`CLAUDE.md`](CLAUDE.md) gives any LLM session the CLI commands and the rules.

## Documents

- [Setup](docs/SETUP.md) — token creation, inventory import, curation loop, MCP registration
- [Autonomous build guard](docs/BUILD_GUARD.md) — active-lease command and MCP safety rules
- [Agent instructions](CLAUDE.md) — how LLM sessions should use this repo
- [Purpose and operating model](docs/PURPOSE.md) — the problem, sources of truth, lifecycle vocabulary, safety boundaries
- [User stories and MCP capabilities](docs/USER_STORIES.md) — 19 stories with acceptance criteria
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — how each story is built and verified
- [Builder agents](docs/BUILDER_AGENTS.md) — planner and independent reviewer contracts
- [Schema reference](docs/SCHEMA.md) — every field, every validation rule
- [Dashboard](DASHBOARD.md) — generated portfolio view

## Status

| Stage | State |
|---|---|
| 1. Foundation — purpose, vocabulary, user stories, safety boundaries | done |
| 2. Registry — machine-readable catalog plus generated dashboard | done |
| 3. GitHub sync — read-only collection of repos, PRs, issues, activity, CI | done |
| 4. Decision support — active work, stale work, missing next actions | done |
| 5. MCP server — query and maintain the registry with explicit write controls | done |

What remains is data, not code: follow [docs/SETUP.md](docs/SETUP.md) to import your repositories, then curate a purpose, lifecycle, and next action for each imported stub.

Tests: `python3 -m pytest` (no network required).

## Non-goals for the first version

- Replacing GitHub as the source of repository and pull-request data.
- Automatically deciding which projects matter.
- Letting the registry CLI or MCP server close issues, merge PRs, delete code, or archive repositories; autonomous builder GitHub mutations are separately guard-enforced.
- Storing credentials, source code from other projects, or private document contents.
