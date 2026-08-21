# Schema Reference

One YAML file per project under `registry/projects/`, named `<id>.yaml`. The machine-readable form is [`registry/schema/project.schema.json`](../registry/schema/project.schema.json); this page explains what each field means and when it is required.

Everything here is **human intent**. Observed GitHub state lives in `data/github/snapshot.json` and is never merged into these files.

## Identity

| Field | Type | Required | Notes |
|---|---|:---:|---|
| `id` | slug | ✅ | Lowercase `[a-z0-9-]`. Stable — relationships reference it. |
| `name` | string | ✅ | Human-readable. Defaults to `id` if omitted. |
| `repo` | `owner/name` | | Omit for intentional non-repository projects. |
| `is_fork` | bool | | Distinguishes forks from original work. |
| `category` | string | | Free-form grouping, e.g. `tooling`, `research`. |
| `tags` | string[] | | Free-form labels. |

## Meaning

| Field | Type | Required | Notes |
|---|---|:---:|---|
| `purpose` | string | ✅* | Why the project exists. *Required unless `needs_review: true`. |
| `desired_outcome` | string | ✅† | †Required when `lifecycle: now` — the result that ends `now`. |
| `needs_review` | bool | | Placeholder for imported repos whose purpose isn't written yet. |
| `notes` | string | | Private working notes. |
| `descriptions.private` | string | | Detail that must never be published. |
| `descriptions.public_safe` | string | | The **only** description a public export may use. |

## Lifecycle and focus

| Field | Type | Required | Notes |
|---|---|:---:|---|
| `lifecycle` | enum | | `now`, `next`, `incubating`, `showcase`, `maintained`, `reference`, `superseded`, `archived`. Default `incubating`. |
| `active` | bool | | Independent of `lifecycle`. Default `false`. |
| `priority` | enum | | `high`, `medium`, `low`. **Human** priority — never derived from GitHub. Absent means *not recorded*, not "low". |
| `effort` | enum | | `small`, `medium`, `large`. |
| `horizon` | enum | | `week`, `month`, `quarter`, `year`, `someday`. |
| `blocked_by` | string | | What is in the way. |
| `visibility` | enum | | `public` or `private`. Curated intent; GitHub's answer is observed separately. |
| `last_reviewed` | date | | Set by `registry record-review`. |

`active` and `lifecycle` are deliberately separate so exceptions can be expressed. Unusual combinations are reported as *suggestions*, never corrected.

## Next action

At most one, as a mapping — not a list.

```yaml
next_action:
  description: Decide whether to keep the CLI or fold it into the MCP server
  reviewed: 2026-07-25          # required
  link: https://github.com/owner/repo/issues/4   # optional
```

Required whenever `active: true`. Phrase it as an outcome or a decision; bare stubs like `continue` are flagged.

## Accomplishments

```yaml
accomplishments:
  - date: 2026-07-25            # required
    kind: milestone             # milestone | artifact | demo | lesson | release
    summary: Shipped the importer          # required
    links: [https://github.com/owner/repo/releases/tag/v1]
    public: true                # opt-in before it appears in a public export
```

Sorted newest-first on load.

## Relationships

```yaml
relationships:
  - kind: successor             # successor | predecessor | duplicate | component | part_of | related
    target: other-project-id
    note: replaced after the rewrite
```

`successor`↔`predecessor` and `component`↔`part_of` are directional inverse pairs; `duplicate` and `related` are symmetric. Declaring one side is enough — `registry related <id>` shows the inverse from the other side, marked `inferred`. Targets must resolve, and `lifecycle: superseded` must declare a `successor`.

## Publication

| Field | Type | Notes |
|---|---|---|
| `public` | bool | Opt in to the portfolio export. Requires `descriptions.public_safe`. |
| `showcase_order` | int | Human-curated ordering. Lower first; unset sorts last. |

Three gates must pass before anything is exported: `public: true`, a non-empty `public_safe`, and — for the repository name specifically — GitHub confirming the repo is public. Accomplishments need their own `public: true`.

## Validation rules

`registry validate` reports **errors** (provably inconsistent) and **suggestions** (worth a look). Errors exit non-zero; suggestions never do.

| Rule | Severity | Trigger |
|---|---|---|
| `purpose_missing` | error | No purpose and no `needs_review` |
| `purpose_pending` | suggestion | `needs_review: true` with no purpose yet |
| `next_action_missing` | error | `active: true` with no next action |
| `next_action_unreviewed` | error | Next action with no `reviewed` date |
| `next_action_future_review` | error | `reviewed` date in the future |
| `next_action_vague` | suggestion | Next action names no outcome or decision |
| `now_without_outcome` | error | `lifecycle: now` with no `desired_outcome` |
| `now_overloaded` | suggestion | More than 3 projects in `now` |
| `active_but_closed` | suggestion | `active` while `archived`/`superseded` |
| `inactive_but_committed` | suggestion | Not `active` while `now`/`next`/`maintained` |
| `active_never_reviewed` | suggestion | Active with no review recorded |
| `active_review_stale` | suggestion | Active, last reviewed over 45 days ago |
| `relationship_broken` | error | Relationship target is not a registered project |
| `relationship_self_reference` | error | A project relates to itself |
| `superseded_without_successor` | error | `superseded` with no `successor` edge |
| `overlap_same_repo` | suggestion | Two projects point at the same repository |
| `overlap_similar_name` | suggestion | Two projects share the same name tokens |
| `public_without_public_safe` | error | `public: true` with no `public_safe` description |
| `showcase_order_collision` | suggestion | Two public projects share a `showcase_order` |
| `credential_material` | error | Text matches a token/key pattern |

The last one is a hard boundary: the registry stores no credential values, ever. If it fires, remove the value and rotate it.

## Observed data (not editable here)

`data/github/snapshot.json` holds repository visibility, archival state, pushes, open PRs, issues, review state, CI state, and remote branch observations. Repository and branch reads have separate timestamps; branch records include head SHA, commit time, protection, default-branch status, and open in-repo PR numbers. `branches_fetched`, `branches_partial`, `branches_skipped`, and `branches_error` keep missing or incomplete evidence from reading as complete. The snapshot is rewritten by `registry sync` and read by everything else. A failed repository refresh keeps the previous entry and marks it `stale: true`; a branch-only failure keeps the previous branch observation, marks it unfetched, and surfaces a partial error.

The `stale_branches` attention rule emits at most one suggestion per repository. It currently uses a 60-day threshold, excludes the default branch and branches with open in-repo PRs, and never treats an unknown commit date or incomplete branch list as known-stale evidence.

## Editing

Hand-edit the YAML for prose. For scripted or agent-driven changes use the proposal path, which records exact before/after values and refuses anything that would introduce a validation error:

```bash
registry propose my-project --set lifecycle=next --rationale "deferred a quarter"
registry proposal-apply my-project-20260725120000 --approve
```

Note that an applied proposal rewrites the file in canonical field order, so YAML comments in that file are not preserved.

`registry record-review` is a sanctioned CLI shortcut that applies directly.
The MCP `record_project_review` tool instead requires `approved=true`; without
it, the tool files a pending proposal, returns an error saying nothing was
applied, and names the proposal for inspection or separate approval through
`apply_approved_project_update`.
