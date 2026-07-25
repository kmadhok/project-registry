# Implementation Plan

This plan turns [`USER_STORIES.md`](USER_STORIES.md) into concrete, buildable work. Every story below names its approach, the artifacts it produces, how each acceptance criterion is satisfied, and how it is tested.

## Architecture

The system is a Python package plus plain-text data. It has three layers, matching the separation of sources of truth in [`PURPOSE.md`](PURPOSE.md):

```
registry/projects/*.yaml      curated intent      (human-owned, hand-edited, git-tracked)
data/github/snapshot.json     observed evidence   (machine-owned, refreshable, timestamped)
data/proposals/*.json         pending changes     (proposed but not applied)
        │
        ▼
src/project_registry/
  model.py       schema, enums, dataclasses, parse/serialize
  storage.py     load/save registry, snapshot, proposals
  validation.py  core operating rules → errors vs suggestions
  queries.py     list/get/search/related/next-actions/work-queue/review-queue
  github/        read-only REST client + sync into snapshot
  signals.py     PR attention rules + registry/GitHub mismatch rules
  dashboard.py   markdown dashboard
  portfolio.py   public-safe export
  proposals.py   propose / apply / record-review with audit log
  cli.py         command line entry point
  mcp/server.py  stdio JSON-RPC MCP server
```

**The one invariant that shapes everything:** curated files and observed files are separate files, written by separate code paths. Sync writes only to `data/`, never to `registry/`. That makes operating rule 7 ("generated data must never overwrite human-curated fields") structural rather than a convention someone has to remember.

### Design decisions

| Decision | Reason |
|---|---|
| YAML for curated data, JSON for observed data | Curated files are hand-edited and want comments and block strings; observed files are machine-written and want exactness. |
| One file per project | Git diffs stay readable, merge conflicts stay local, and a project is greppable by name. |
| Stdlib-only except PyYAML | The MCP server and GitHub client must run anywhere without an install step. |
| Errors vs. suggestions, never auto-fix | Rules 6 and 8: the registry reports, the human decides. |
| Every derived result carries a `reason` | US-006, US-011, US-012 all require the output to explain itself. |

## Data model

A curated project (full field reference in [`SCHEMA.md`](SCHEMA.md)):

```yaml
id: project-registry              # slug, unique, stable
name: Project Registry
purpose: A private control plane for ...   # required unless needs_review
desired_outcome: ...              # required for lifecycle `now`
lifecycle: now                    # 8-value enum
active: true                      # separate boolean signal
needs_review: false
category: tooling
priority: high                    # human priority; never derived from GitHub
effort: medium
horizon: quarter
blocked_by: null
repo: kmadhok/project-registry    # null for non-repository projects
is_fork: false
visibility: private               # curated intent
last_reviewed: 2026-07-25
next_action:                      # at most one
  description: Define and validate the project schema
  reviewed: 2026-07-25
  link: https://github.com/...
accomplishments:
  - date: 2026-07-25
    kind: milestone               # milestone|artifact|demo|lesson|release
    summary: ...
    links: [...]
relationships:
  - kind: successor               # successor|predecessor|duplicate|component|part_of|related
    target: other-project-id
    note: ...
descriptions:
  private: ...
  public_safe: ...                # the only description an export may use
public: false                     # explicit opt-in for portfolio export
showcase_order: 1
tags: []
notes: ...
```

---

# Owner stories

## US-001 — See the whole portfolio (`P0`)

**Approach.** `Registry` loads every `registry/projects/*.yaml` into `Project` objects keyed by `id`. `repo` is optional, so a project without a repository is a first-class entry. `import-github` walks the owner's repositories and writes one stub file per repo with `needs_review: true`, never overwriting an existing file.

**Artifacts.** `model.Project`, `storage.load_registry`, `cli import-github`, `cli list`.

| Acceptance criterion | How it is met |
|---|---|
| Owned repos and non-repository projects representable | `repo` is nullable; `source: repo\|non_repo` derived from it |
| Forks and duplicate checkouts distinguishable | `is_fork` flag (seeded from GitHub on import) plus `duplicate` relationship kind |
| Entry shows purpose, lifecycle, activity, visibility, last review | `Project.summary()` returns exactly these six fields; `list` and the dashboard both render from it |

**Tests.** Round-trip a project with and without `repo`; import into a registry with an existing file and assert the curated file is byte-identical afterward.

## US-002 — Understand a project's purpose (`P0`)

**Approach.** `purpose` and `desired_outcome` live only in curated YAML. `descriptions.private` and `descriptions.public_safe` are separate fields, and no code path copies one into the other. `needs_review: true` is the escape hatch that lets an imported repo exist before its purpose is known — validation reports it as a suggestion, not an error.

**Artifacts.** `model.Project.purpose/desired_outcome/descriptions`, `validation.rule_purpose_present`.

| Acceptance criterion | How it is met |
|---|---|
| Purpose human-curated, not overwritable by sync | Sync writes only under `data/`; a test asserts `registry/` mtimes are unchanged by a sync |
| `needs_review` allowed when purpose unknown | Missing purpose is an error *unless* `needs_review` is set, in which case it is a suggestion |
| Public-safe and private descriptions stored separately | Two distinct fields; export reads `public_safe` only |

**Tests.** Purpose-missing-without-`needs_review` is an error; with it, a suggestion. Sync-does-not-touch-`registry/` test.

## US-003 — See what I accomplished (`P0`)

**Approach.** `accomplishments` is a list of dated entries, each with a `kind`, a `summary`, and zero or more `links`. Entries are sorted newest-first on load. `Project.accomplishment_summary(limit)` produces the condensed form the dashboard and portfolio export both use.

**Artifacts.** `model.Accomplishment`, `dashboard` accomplishments section, `portfolio` highlights.

| Acceptance criterion | How it is met |
|---|---|
| Multiple dated accomplishments | List field, each entry requires `date` and `summary` |
| Links to commits, releases, docs, demos, PRs | Free-form `links` list plus a `kind` enum covering milestone/artifact/demo/lesson/release |
| Concise summary usable in a portfolio view | `accomplishment_summary()` shared by dashboard and export |

**Tests.** Sorting is newest-first; an entry missing `date` fails validation; summary respects its limit.

## US-004 — Know what is active (`P0`)

**Approach.** `lifecycle` (8-value enum) and `active` (bool) are independent fields, never derived from each other or from commit recency. A `now`-count check warns above a configurable maximum (default 3). Lifecycle changes through the proposal workflow record before/after values.

**Artifacts.** `model.Lifecycle`, `validation.rule_now_capacity`, `validation.rule_lifecycle_activity_coherence`.

| Acceptance criterion | How it is met |
|---|---|
| Active status and lifecycle separately represented | Two fields; no code infers one from the other |
| Warn when too many projects in `now` | `rule_now_capacity` emits a suggestion above `max_now` |
| Lifecycle change is intentional and reviewable | `propose_project_update` → diff → `apply_approved_project_update`, appended to the audit log |

Unusual combinations (an `archived` project marked active, a `now` project marked inactive) are surfaced as suggestions, not errors — PURPOSE.md explicitly wants the split to *support* exceptions.

**Tests.** Four `now` projects yields the capacity suggestion; `archived` + `active: true` yields a coherence suggestion but no error.

## US-005 — Know what to do next (`P0`)

**Approach.** `next_action` is a single optional object, not a list — the cardinality constraint is expressed in the type. Rule: `active == true` requires it. `find_missing_next_actions()` reports the gaps.

**Artifacts.** `model.NextAction`, `validation.rule_active_needs_next_action`, `queries.list_next_actions`, `queries.find_missing_next_actions`.

| Acceptance criterion | How it is met |
|---|---|
| Missing next actions reported | `find_missing_next_actions()` + `cli next-actions --missing` + a dashboard section |
| Phrased as a concrete outcome or decision | Validation rejects vague stubs (bare "TODO", "continue", "keep going", under 8 chars) as a suggestion |
| Records review date, may link to issue or PR | `reviewed` date required, `link` optional |

**Tests.** Active project without a next action is an error; `next_action` as a list fails to parse; vague text yields a suggestion.

## US-006 — Choose work across projects (`P1`)

**Approach.** `build_work_queue()` merges curated next actions with live attention signals into one ranked list. Each item carries `human_priority`, `github_urgency`, and `reasons` as **separate** fields — the ranking never collapses them into a single opaque score, and the renderer prints both columns.

**Artifacts.** `queries.build_work_queue`, `cli work-queue`.

| Acceptance criterion | How it is met |
|---|---|
| Filter by lifecycle, category, priority, effort, blocker | `WorkQueueFilter` dataclass; all five exposed as CLI flags and MCP arguments |
| Human priority shown separately from GitHub urgency | Two independent fields on every item, rendered as two columns |
| Explains why each item appears | `reasons: list[str]`, populated by whichever rule contributed the item |

**Tests.** An item sourced from both a next action and a failing check lists both reasons; filters compose; a project with no recorded priority sorts by GitHub urgency without a priority being invented.

## US-007 — Connect overlapping projects (`P0`)

**Approach.** Relationships are stored on the declaring project with a `kind` and a `target` id. `successor`/`predecessor` and `component`/`part_of` are directional inverse pairs; `duplicate` and `related` are symmetric. `list_related_projects()` returns both declared and inferred-inverse edges, tagging which is which. Reference integrity and `superseded`-names-successor are validated.

**Artifacts.** `model.Relationship`, `model.RelationKind`, `queries.list_related_projects`, `validation.rule_relationships_resolve`, `validation.rule_superseded_names_successor`, `validation.rule_overlap_check`.

| Acceptance criterion | How it is met |
|---|---|
| Directional where appropriate | `RelationKind.inverse()` defines the pairs; symmetric kinds invert to themselves |
| Superseded must name successor | Error when `lifecycle == superseded` and no `successor` relationship exists |
| Broken references detected | Error for any `target` not present in the registry; error for self-reference |

Operating rule 4 (check new projects for overlap) ships as `rule_overlap_check`: a suggestion when two projects share a normalized name token set or an identical `repo`, and neither declares a `duplicate`/`related` edge.

**Tests.** Dangling target is an error; inverse inference returns the reverse edge with `inferred: true`; `superseded` without successor is an error.

## US-008 — Review stale projects (`P1`)

**Approach.** `build_review_queue()` scores staleness from two independent inputs: days since `last_reviewed` (human) and days since GitHub `pushed_at` (observed). Each queue item states which input triggered it. Nothing in this path writes lifecycle — it is a pure read.

**Artifacts.** `queries.build_review_queue`, `proposals.record_project_review`, `cli review-queue`.

| Acceptance criterion | How it is met |
|---|---|
| Uses both last human review and GitHub activity | `StalenessThresholds` holds separate `review_days` and `activity_days` per lifecycle |
| Never automatically changes lifecycle | The module has no write path; a test asserts registry files are unchanged after building the queue |
| Review can update purpose, lifecycle, next action, notes | `record_project_review()` stamps `last_reviewed` and accepts those four field updates through the proposal path |

**Tests.** Only-stale-review and only-stale-activity each surface with the correct reason; queue-building leaves files untouched.

## US-009 — Produce a portfolio view (`P2`)

**Approach.** `export_portfolio()` starts from an empty output and copies in an **allowlist** of fields, rather than starting from the project and removing private ones. A project appears only if `public: true` **and** `descriptions.public_safe` is set. Private repo names are excluded unless the project is public *and* GitHub reports the repo public.

**Artifacts.** `portfolio.export_portfolio`, `portfolio.PUBLIC_FIELDS`, `cli portfolio`.

| Acceptance criterion | How it is met |
|---|---|
| Only explicitly approved public-safe fields | Allowlist constant; a test feeds a project stuffed with secrets in every private field and asserts none appear in the output |
| Private repo names excluded by default | `repo` emitted only when the repo is public in the snapshot |
| Showcase ordering human-curated | Sort by `showcase_order`, then name; never by activity or star count |

**Tests.** The stuffed-secrets test above; `public: true` without `public_safe` is excluded; ordering respects `showcase_order`.

## US-010 — See open pull requests (`P1`)

**Approach.** Sync stores open PRs per repo in the snapshot, including draft state, review state, and CI conclusion. `list_open_prs()` reads the snapshot only — never live GitHub — so every result is attributable to a known refresh time.

**Artifacts.** `github.sync.sync_repository`, `queries.list_open_prs`, `cli prs`.

| Acceptance criterion | How it is met |
|---|---|
| Repo, title, draft state, age, review state, CI state | All six on `PullRequest`; review and CI degrade to `unknown` when the API is unavailable rather than being guessed |
| Draft and ready filterable separately | `draft` filter with three states: only-draft, only-ready, both |
| Cached results display refresh time | Every result set carries `fetched_at` and an `age` string; the CLI prints it in a header and marks it stale past the threshold |

**Tests.** Draft filter partitions correctly; a snapshot older than the staleness threshold renders a stale marker.

## US-011 — Find GitHub work needing attention (`P1`)

**Approach.** A rule table. Each rule is `(id, description, predicate, severity)` and produces `AttentionItem(rule_id, reason, url, severity)`. Adding a signal means adding a table row.

Rules: `ci_failing`, `changes_requested`, `stale_pr`, `unreviewed_pr`, `draft_pr_aging`, `selected_issue` (issues carrying a watched label).

**Artifacts.** `signals.ATTENTION_RULES`, `signals.build_attention_queue`, `cli attention`.

| Acceptance criterion | How it is met |
|---|---|
| Failing CI, requested changes, stale PRs, unreviewed PRs, selected issues | The six rules above |
| Each signal links to its GitHub source | `url` is required on every item; a test asserts no item has an empty URL |
| Explains the rule that produced it | `rule_id` plus the rendered `reason` string, both shown in output |

**Tests.** One fixture snapshot exercising every rule; each expected `rule_id` appears exactly once.

## US-012 — Detect registry/GitHub mismatches (`P1`)

**Approach.** A second rule table comparing curated intent against the snapshot, classified `error` (the registry is provably wrong) vs `suggestion` (worth a human look). Sync never resolves a mismatch — detection is a separate read-only pass over data sync already wrote.

Rules: `archived_but_github_active`, `repo_missing`, `visibility_drift`, `active_without_recent_review`, `inactive_but_recent_pushes`, `fork_flag_drift`.

**Artifacts.** `signals.MISMATCH_RULES`, `signals.find_mismatches`, `cli mismatches`.

| Acceptance criterion | How it is met |
|---|---|
| Covers archived-vs-active, missing repo, visibility change, active-without-review | Rules 1, 2, 3, 4 above |
| Sync does not resolve mismatches automatically | `find_mismatches` is pure; the sync path never calls it |
| Distinguishes errors from suggestions | `severity` on every mismatch; the CLI groups by severity and exits non-zero only on errors |

**Tests.** One fixture per rule; a sync run followed by a mismatch check leaves the mismatch present.

---

# MCP client stories

The server speaks JSON-RPC 2.0 over stdio with zero third-party dependencies (`initialize`, `tools/list`, `tools/call`). Every tool returns JSON with a `source_timestamps` block naming the registry load time and the snapshot's `fetched_at`.

## MCP-001 — Query projects (`P1`)

`list_projects`, `get_project`, `search_projects`, `list_related_projects` — thin wrappers over `queries.py`, so CLI and MCP cannot drift. `search_projects` matches id, name, purpose, tags, category, and accomplishment summaries, returning per-result matched fields.

**Tests.** Each tool returns a valid result for a seeded registry; `get_project` on an unknown id returns a structured error, not an exception.

## MCP-002 — Recommend the next review or action (`P1`)

`list_next_actions`, `find_missing_next_actions`, `list_projects_needing_review`, `get_attention_queue`.

The hard requirement — *"must not invent a project priority when none is recorded"* — is enforced by keeping `human_priority` nullable end to end and asserting in tests that a project with no priority yields `null`, never a default. Every response carries `reasons` and `source_timestamps`.

## MCP-003 — Inspect GitHub work (`P1`)

`list_open_prs`, `get_pr_attention`, `list_selected_issues`, `get_github_sync_status`. All read the snapshot; `get_github_sync_status` reports last start/completion, coverage, errors, and staleness.

## MCP-004 — Refresh observed state (`P1`)

`refresh_github` runs the sync.

| Acceptance criterion | How it is met |
|---|---|
| Read-only with respect to GitHub | The client exposes `GET` only; a test asserts no other verb appears in the client module |
| Records start, completion, errors, coverage | `SyncResult` carries all four and is persisted into the snapshot |
| Partial failure does not silently overwrite good data | Per-repo merge: a failed repo keeps its previous entry, marked `stale: true` with the error attached, and coverage reports the shortfall |

## MCP-005 — Propose registry updates (`P2`)

`propose_project_update` writes a pending proposal containing computed before/after values and returns its id — it does not touch `registry/`. `apply_approved_project_update` requires that id plus `approved: true`, re-validates, then writes and appends to `data/audit_log.jsonl`. `record_project_review` stamps `last_reviewed` through the same path.

| Acceptance criterion | How it is met |
|---|---|
| Propose and apply are distinct operations | Two tools, two files, no auto-apply path |
| Exact before/after shown before approval | The proposal stores a per-field `{before, after}` diff, rendered by `cli proposals show` |
| Validated and auditable | Apply re-runs validation and refuses on new errors; every apply appends an audit record |

**Tests.** Proposing does not modify the project file; applying without `approved` is refused; applying a proposal that would break validation is refused; the audit log grows by one entry per apply.

## MCP-006 — Guard external actions (`P2`)

No GitHub mutation tool exists in the first server. This is enforced by a test that asserts the advertised tool names contain nothing matching `merge|close|delete|archive|visibility|push|dispatch`, and that the GitHub client module contains no non-GET request. Any future external write must name its exact repository and target, and defaults to refusing without explicit confirmation.

---

# Build order

1. Model, schema, storage — everything depends on it.
2. Validation — makes seeded data trustworthy immediately.
3. Queries, work queue, review queue.
4. GitHub client, sync, inventory import.
5. Signals: attention + mismatches.
6. Dashboard, portfolio export.
7. Proposals, audit log, CLI.
8. MCP server over the same query functions.
9. Tests throughout, seeded registry last.

The first five stories in the "first implementation slice" (`US-001`, `US-002`, `US-004`, `US-005`, `US-007`) are complete after step 3; the rest builds on that base without changing it.
