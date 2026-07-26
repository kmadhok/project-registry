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
data/proposals/*.json        pending changes     proposed but not applied
```

Sync writes only under `data/`. Nothing in that path can touch `registry/`, which is what makes "generated data must never overwrite human-curated fields" a structural guarantee rather than a convention.

## Quick start

```bash
pip install -e .              # or: PYTHONPATH=src python3 -m project_registry.cli ...

registry validate             # check the registry against its own operating rules
registry list                 # the whole portfolio
registry show project-registry
registry dashboard            # regenerate DASHBOARD.md
```

To pull in GitHub evidence, export a token with read access and run:

```bash
export GITHUB_TOKEN=...
registry import-github --owner kmadhok   # create stubs marked needs_review
registry sync                            # refresh observed state (read-only)
registry prs                             # open PRs across the portfolio
registry attention                       # work needing a human, with the rule behind each signal
registry mismatches                      # where registry intent and GitHub disagree
```

Day-to-day:

```bash
registry work-queue           # ranked work; human priority and GitHub urgency shown separately
registry next-actions --missing
registry review-queue         # what is due for a deliberate look
registry record-review my-project --set lifecycle=next
```

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

21 tools: project queries, next actions and review recommendations, portfolio-wide PR and issue views, a GitHub refresh, and the propose/apply pair. Every response carries `source_timestamps` so a client can tell live data from cached data. There is no tool that merges, closes, deletes, archives, or changes visibility on GitHub — the HTTP client is restricted to `GET`, and a test enforces both.

Client config:

```json
{
  "mcpServers": {
    "project-registry": {
      "command": "registry",
      "args": ["--root", "/path/to/project-registry", "mcp"],
      "env": { "GITHUB_TOKEN": "..." }
    }
  }
}
```

## Documents

- [Purpose and operating model](docs/PURPOSE.md) — the problem, sources of truth, lifecycle vocabulary, safety boundaries
- [User stories and MCP capabilities](docs/USER_STORIES.md) — 18 stories with acceptance criteria
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — how each story is built and verified
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

What remains is data, not code: run `import-github`, then curate a purpose, lifecycle, and next action for each imported stub.

Tests: `python3 -m pytest` (213 tests, no network required).

## Non-goals for the first version

- Replacing GitHub as the source of repository and pull-request data.
- Automatically deciding which projects matter.
- Automatically closing issues, merging PRs, deleting code, or archiving repositories.
- Storing credentials, source code from other projects, or private document contents.
