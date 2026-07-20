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

## Repository understanding stories

### RU-001 — Perform a repository understanding pass (`P0`)

As the project owner, I want each repository examined systematically so that the registry explains what the project actually is rather than relying on its name or GitHub description.

Acceptance criteria:

- The process inspects documentation, source structure, entry points, dependencies, tests, workflows, recent history, issues, PRs, and representative outputs when available.
- The process records which evidence was available, inspected, missing, or inaccessible.
- Large repositories may be sampled strategically, but the sampling method and limitations are recorded.
- The result is dated and tied to a repository revision.

### RU-002 — Distinguish intent from implementation (`P0`)

As the project owner, I want intended capabilities separated from implemented capabilities so that plans and stale documentation are not mistaken for completed work.

Acceptance criteria:

- The brief separately records stated purpose, observed implementation, and planned work.
- Implemented-capability claims cite supporting files, tests, commits, releases, or runnable artifacts.
- Conflicts between documentation and code are surfaced as questions or mismatches.
- Unsupported claims are never presented as facts.

### RU-003 — Explain architecture and operation (`P1`)

As the project owner, I want a concise explanation of how a repository works so that I or an agent can resume work without rediscovering its structure.

Acceptance criteria:

- The brief identifies primary entry points, major components, dependencies, external systems, and important data flows.
- Setup and execution paths are summarized when evidence supports them.
- Important operational constraints and credential requirements are named without capturing secret values.
- Architecture detail is proportional to the repository's complexity.

### RU-004 — Assess maturity, accomplishments, and gaps (`P0`)

As the project owner, I want evidence-backed maturity and accomplishment summaries so that effort is recognized and unfinished work is visible.

Acceptance criteria:

- The brief identifies working artifacts, meaningful milestones, tests, deployments, demos, or outputs.
- It records material gaps, broken paths, missing documentation, security concerns, and maintenance risks.
- Maturity uses a defined vocabulary rather than commit count alone.
- Suggested next actions explain which evidence motivated them.

### RU-005 — Record confidence and unresolved questions (`P0`)

As the project owner, I want analysis confidence and unanswered questions recorded so that inference is not confused with knowledge.

Acceptance criteria:

- Material conclusions are labeled as observed fact, supported inference, or owner-provided context.
- The brief has an overall confidence level and explains important limitations.
- Questions requiring owner knowledge are collected for review instead of guessed.
- Owner corrections become curated context and remain distinguishable from generated analysis.

### RU-006 — Refresh understanding incrementally (`P1`)

As the project owner, I want repository understanding refreshed when meaningful changes occur so that analysis remains current without repeatedly rereading everything.

Acceptance criteria:

- The system detects changes since the last analyzed revision.
- Refresh focuses on affected evidence while preserving still-valid findings.
- Material changes to purpose, architecture, maturity, or next actions are highlighted.
- Previous briefs remain auditable.

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

#### MCP-007 — Analyze a repository (`P1`)

As an MCP client, I need to request or retrieve an evidence-backed repository understanding brief so that agents can reason from durable analysis instead of repeatedly exploring the repository.

Expected read tools:

- `analyze_repository`
- `get_repository_understanding`
- `get_repository_evidence`
- `list_repository_questions`

Acceptance criteria:

- Analysis follows `RU-001` through `RU-005`.
- Results identify the analyzed revision, timestamp, evidence, confidence, and limitations.
- Facts and inferences are distinguishable in structured output.
- Analysis does not modify the analyzed repository.

#### MCP-008 — Compare and refresh repository understanding (`P1`)

As an MCP client, I need to compare related repositories and refresh prior analysis so that overlap, successors, and meaningful changes can be detected.

Expected read tools:

- `refresh_repository_understanding`
- `compare_repositories`
- `find_repository_overlaps`
- `list_understanding_changes`

Acceptance criteria:

- Comparisons cite evidence from every repository involved.
- Similarity does not automatically create a registry relationship.
- Refresh preserves prior analysis and highlights changed conclusions.
- Suggested relationship or lifecycle changes require owner review.

## First implementation slice

The first implementation should satisfy `US-001`, `US-002`, `US-004`, `US-005`, `US-007`, `RU-001`, `RU-002`, `RU-004`, and `RU-005` without an MCP server:

1. Define and validate a machine-readable project schema.
2. Import the GitHub repository inventory with `needs_review` placeholders.
3. Define the repository-understanding brief, evidence model, maturity vocabulary, and confidence rules.
4. Run the understanding process on a small, varied pilot set of repositories.
5. Curate purpose, lifecycle, activity, relationships, accomplishments, and next action using reviewed evidence.
6. Generate a readable portfolio dashboard.
7. Review the workflow before scaling the analysis or exposing it as MCP tools.
