# Project Registry

A private control plane for understanding, prioritizing, and maintaining Kanu's project portfolio.

The registry combines two kinds of information:

- **Human intent:** why a project exists, whether it is active, what outcome matters, and what should happen next.
- **Observed evidence:** GitHub metadata, open PRs and issues, repository contents, implementation evidence, and confidence-rated understanding briefs.

Human intent remains authoritative. Refreshes may add repositories and update observed state, but they preserve purpose, lifecycle, accomplishments, relationships, priority, and next actions already stored in the registry.

## What works now

- Imports every repository visible to the authenticated GitHub owner, including private repositories and forks.
- Keeps GitHub-observed state separate from curated project fields.
- Enriches open PRs with draft, review, merge, and check state.
- Validates lifecycle invariants, relationships, registry coverage, and evidence references.
- Stores revision-specific repository-understanding briefs with facts, inferences, confidence, and unanswered questions.
- Generates a private Markdown dashboard with active work, PRs, attention signals, understanding coverage, and the owner-review queue.
- Provides validated CLI updates for purpose, lifecycle, active state, next action, and understanding-brief attachment.

## Quick start

Prerequisites:

- Python 3.11 or newer.
- [GitHub CLI](https://cli.github.com/) authenticated with access to the repositories being inventoried.

```bash
# Inspect available commands
python -m project_registry --help

# Refresh GitHub data without modifying any external repository
python -m project_registry sync --owner kmadhok

# Validate the registry and every attached understanding brief
python -m project_registry validate

# Generate DASHBOARD.md
python -m project_registry dashboard

# Run the complete routine workflow
python -m project_registry refresh --owner kmadhok

# Run regression tests
python -m unittest discover -s tests -v
```

When `gh` is not on `PATH`, set `GH_BIN` or pass `--gh-bin`. The CLI also detects the standard Windows GitHub CLI path from WSL.

## Curate a project

`set-project` changes only fields explicitly provided and validates the entire registry before writing.

```bash
python -m project_registry set-project kmadhok/example \
  --review-status reviewed \
  --purpose "Explain what this project exists to accomplish." \
  --lifecycle next \
  --active \
  --desired-outcome "Define the outcome that moves it out of next." \
  --next-action "Name one concrete action." \
  --review-now \
  --tag example
```

If the proposed update violates an invariant—for example, an active project without a next action—the file is not changed.

## Understand a repository

Start with a read-only evidence inventory:

```bash
python -m project_registry scaffold-understanding \
  kmadhok/example ../example
```

Then review the repository according to [the understanding process](docs/REPOSITORY_UNDERSTANDING.md), replace scaffold placeholders with evidence-backed claims, validate the brief, and attach it:

```bash
python -m project_registry set-project kmadhok/example \
  --understanding-path data/understanding/kmadhok__example.json
```

An understanding brief does not automatically make its proposed purpose, lifecycle, or priority owner-approved.

## Data layout

| Path | Authority |
|---|---|
| `data/projects.json` | Human-curated project meaning and lifecycle; new repositories start as `needs_review`. |
| `data/github_snapshot.json` | Generated read-only GitHub observation with a refresh timestamp. |
| `data/understanding/*.json` | Revision-specific evidence and analysis. |
| `DASHBOARD.md` | Generated operational view. |
| `schema/*.schema.json` | Machine-readable contracts. |

## Documents

- [Purpose and operating model](docs/PURPOSE.md)
- [User stories and MCP capabilities](docs/USER_STORIES.md)
- [Repository understanding process](docs/REPOSITORY_UNDERSTANDING.md)
- [Operating the registry](docs/OPERATIONS.md)

## Planned evolution

1. Review imported repositories and deliberately assign lifecycle, purpose, and relationships.
2. Expand evidence-backed understanding coverage beyond the pilot set.
3. Add comparison and incremental-refresh support for related repositories.
4. Expose proven read workflows through an MCP server.
5. Add separately authorized write proposals only after the read model is trustworthy.

## Safety boundaries

- GitHub synchronization is read-only.
- The registry never stores credential values or `.env` contents.
- Generated observations never overwrite curated fields.
- Repository analysis distinguishes facts, inferences, owner context, and unanswered questions.
- No command merges PRs, closes issues, archives repositories, changes visibility, or modifies analyzed repositories.
