# User Stories and MCP Capabilities

These stories define the behavior before the registry schema or MCP implementation is chosen. Priorities are:

- **P0:** required for the first useful registry;
- **P1:** required for the first useful MCP;
- **P2:** valuable after the core workflow is trustworthy.

## Owner user stories

### Portfolio understanding

#### US-001 — See the whole portfolio (`P0`)

As the project owner, I want one inventory of my projects so that completed work, experiments, and active products remain discoverable.

Acceptance criteria:

- Owned repositories and intentional non-repository projects can be represented.
- Forks and duplicate local checkouts can be distinguished from original projects.
- Each entry shows purpose, lifecycle, activity, visibility, and last review date.

#### US-002 — Understand a project's purpose (`P0`)

As the project owner, I want a concise purpose and desired outcome for each project so that I can remember why the work matters.

Acceptance criteria:

- Purpose is human-curated and cannot be overwritten by GitHub synchronization.
- A project may be marked `needs_review` when its purpose is not yet known.
- Public-safe and private descriptions can be stored separately.

#### US-003 — See what I accomplished (`P0`)

As the project owner, I want to record milestones, artifacts, demos, and lessons so that effort remains valuable even when a project stops.

Acceptance criteria:

- A project can contain multiple dated accomplishments.
- Accomplishments can link to commits, releases, documents, demos, or PRs.
- A concise accomplishment summary can be used in a portfolio view.

### Focus and next actions

#### US-004 — Know what is active (`P0`)

As the project owner, I want an explicit active flag and lifecycle state so that current commitments are not inferred from commit recency.

Acceptance criteria:

- Active status and lifecycle are separately represented.
- The registry can warn when too many projects are in `now`.
- Changing lifecycle is an intentional, reviewable edit.

#### US-005 — Know what to do next (`P0`)

As the project owner, I want every active project to have one clear next action so that I can resume work without reconstructing context.

Acceptance criteria:

- Missing next actions are reported.
- A next action is phrased as a concrete outcome or decision.
- The action records when it was reviewed and may link to an issue or PR.

#### US-006 — Choose work across projects (`P1`)

As the project owner, I want a prioritized view of actionable work so that I can choose the highest-value next step across repositories.

Acceptance criteria:

- Results can be filtered by lifecycle, category, priority, effort, or blocker.
- Human priority is shown separately from GitHub urgency signals.
- The view explains why each item appears.

### Lifecycle management

#### US-007 — Connect overlapping projects (`P0`)

As the project owner, I want to mark projects as related, duplicated, components, predecessors, or successors so that repeated effort becomes an understandable body of work.

Acceptance criteria:

- Relationships are directional where appropriate.
- A superseded project must name its successor.
- Broken relationship references are detected.

#### US-008 — Review stale projects (`P1`)

As the project owner, I want a periodic review queue so that inactive work is deliberately resumed, reclassified, or archived.

Acceptance criteria:

- Staleness uses both last human review and GitHub activity.
- Staleness never automatically changes lifecycle.
- Review results can update purpose, lifecycle, next action, and notes.

#### US-009 — Produce a portfolio view (`P2`)

As the project owner, I want a public-safe summary of selected work so that GitHub and professional materials reflect my strongest recent projects.

Acceptance criteria:

- Only explicitly approved public-safe fields are included.
- Private repository names and details are excluded by default.
- Showcase ordering is human-curated.

## GitHub intelligence stories

#### US-010 — See open pull requests (`P1`)

As the project owner, I want all open PRs in one view so that reviews and unfinished branches do not disappear.

Acceptance criteria:

- Results include repository, title, draft state, age, review state, and CI state when available.
- Draft and ready PRs can be filtered separately.
- Cached results display their refresh time.

#### US-011 — Find GitHub work needing attention (`P1`)

As the project owner, I want attention signals across repositories so that blocked or aging work is visible.

Acceptance criteria:

- Signals include failing CI, requested changes, stale PRs, unreviewed PRs, and selected issues.
- Each signal links to its GitHub source.
- The registry explains the rule that produced the signal.

#### US-012 — Detect registry/GitHub mismatches (`P1`)

As the project owner, I want to see conflicts between registry intent and GitHub state so that metadata stays trustworthy.

Acceptance criteria:

- Examples include registry `archived` while GitHub is active, a missing repository, visibility changes, or an active project with no recent review.
- Sync does not resolve mismatches automatically.
- The report distinguishes errors from suggestions.

#### US-013 — Builder queue (`P1`)

As the project owner, I want an explainable autonomous-builder queue so that only eligible, intentional work is selected in a fair and deterministic order.

Acceptance criteria:

- Every project is classified as `ready`, `spec_only`, `paused`, `needs_intent`, `manual_only`, or `ineligible`, with human-readable reasons.
- `project-registry` is always ineligible, `blocked_by` pauses a project, open decisions require intent, and shadow mode is ready only as a dry run.
- Ranking uses recorded human priority, oldest successful build, lifecycle order, and project id; never-built projects come first among equal priorities.
- GitHub urgency, snapshot freshness, and evidence briefs do not affect rank.
- Queue order is deterministic regardless of registry input order.
- CLI and MCP return the same candidate, state-count, and readiness structures.

Expected read tools:

- `get_build_queue`
- `validate_project_readiness`

## MCP client stories

#### MCP-001 — Query projects (`P1`)

As an MCP client, I need to list and retrieve projects by lifecycle, activity, category, relationship, or text query so that I can answer portfolio questions from structured context.

Expected read tools:

- `list_projects`
- `get_project`
- `search_projects`
- `list_related_projects`

#### MCP-002 — Recommend the next review or action (`P1`)

As an MCP client, I need to combine curated next actions with live attention signals so that I can present an explainable work queue.

Expected read tools:

- `list_next_actions`
- `find_missing_next_actions`
- `list_projects_needing_review`
- `get_attention_queue`

The MCP must return reasons and source timestamps. It must not invent a project priority when none is recorded.

#### MCP-003 — Inspect GitHub work (`P1`)

As an MCP client, I need portfolio-wide PR and issue views so that open work can be found without searching each repository.

Expected read tools:

- `list_open_prs`
- `get_pr_attention`
- `list_selected_issues`
- `get_github_sync_status`

#### MCP-004 — Refresh observed state (`P1`)

As an MCP client, I need to request a GitHub refresh so that answers can use current evidence.

Acceptance criteria:

- Refresh is read-only with respect to GitHub.
- Results record start time, completion time, errors, and coverage.
- Partial failure does not overwrite the last known good data without being identified.

#### MCP-005 — Propose registry updates (`P2`)

As an MCP client, I need to propose changes to curated fields so that an agent can help maintain the registry without silently changing intent.

Expected write tools:

- `propose_project_update`
- `apply_approved_project_update`
- `record_project_review`

Acceptance criteria:

- Proposed and applied changes are distinct operations.
- The exact before/after values are shown before approval.
- Changes are validated and auditable.

#### MCP-006 — Guard external actions (`P2`)

As an MCP client, I need external GitHub mutations to be separately authorized so that registry maintenance cannot accidentally alter repositories.

Acceptance criteria:

- The initial MCP provides no merge, delete, archive, visibility-change, or issue-closing tools.
- Any later external write tool names the exact repository and target.
- Destructive or public-facing changes always require explicit confirmation.

## First implementation slice

The first implementation should satisfy `US-001`, `US-002`, `US-004`, `US-005`, and `US-007` without an MCP server:

1. Define and validate a machine-readable project schema.
2. Import the GitHub repository inventory with `needs_review` placeholders.
3. Curate purpose, lifecycle, activity, relationships, and next action.
4. Generate a readable portfolio dashboard.
5. Review the workflow before exposing it as MCP tools.
