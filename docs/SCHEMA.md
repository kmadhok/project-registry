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

### Brief

`brief` is optional owner-curated intent. `purpose` and `desired_outcome` remain top-level fields.

```yaml
brief:
  done_criteria: [The indexed count equals the collected count]
  non_goals: [No new content sources]
  constraints: [Local CLI only]
  open_decisions:
    - question: Which underwriting domain?
      status: open                 # open (default) | answered
      answer: null
  reviewed: 2026-08-22
```

All lists default to empty. `open_decisions[].question` is required; `answer` may be a string or null. Build readiness requires a purpose, desired outcome, at least one done criterion, a repository, and no open decisions.

`registry brief-status` reports those completeness gaps plus review freshness.
A brief with `brief.reviewed` more than 90 days ago is stale. A brief that has
never been reviewed is stale when `automation.mode` is `build`, `shadow`, or
`spec_only`; never-reviewed projects with automation off are not marked stale.
Use `--incomplete`, `--stale`, or both to narrow the report.

### Automation policy

`automation` is optional and defaults to a safe, disabled policy.

```yaml
automation:
  mode: off                       # off | shadow | spec_only | build
  allow: [dependencies, ci, generated_data]
  budget:
    chunks_per_run: 6             # integer >= 1
    minutes_per_run: 120          # integer >= 1
  paused: false
```

`allow` is limited to `dependencies`, `ci`, `generated_data`, `public_api`, `migrations`, and `personal_data`. An omitted budget uses 6 chunks and 120 minutes per run. `paused` is an owner-controlled kill switch.

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

## Editing curated fields

Hand-edit the YAML for prose. For scripted or agent-driven changes use the proposal path, which records exact before/after values and refuses anything that would introduce a validation error:

```bash
registry propose my-project --set lifecycle=next --rationale "deferred a quarter"
registry proposal-apply my-project-20260725120000 --approve
```

An applied proposal rewrites the file in canonical field order, so YAML comments in that file are not preserved.

The brief proposal paths are `brief.done_criteria`, `brief.non_goals`, `brief.constraints`, `brief.open_decisions`, and `brief.reviewed`. Automation paths are `automation.mode`, `automation.allow`, `automation.budget.chunks_per_run`, `automation.budget.minutes_per_run`, and `automation.paused`.

`registry record-review` is a sanctioned CLI shortcut that applies directly. The MCP `record_project_review` tool instead requires `approved=true`; without it, the tool files a pending proposal, returns an error saying nothing was applied, and names the proposal for inspection or separate approval through `apply_approved_project_update`.

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
| `brief_incomplete_for_build` | error | `build` or `shadow` mode with missing purpose, desired outcome, done criteria, repo, or an open decision |
| `automation_without_focus_lifecycle` | suggestion | `build`, `shadow`, or `spec_only` mode outside `now`, `next`, or `maintained` |
| `open_decision_unanswered` | suggestion | An open decision exists while mode is not `build` or `shadow` |
| `credential_material` | error | Text matches a token/key pattern |

The last one is a hard boundary: the registry stores no credential values, ever. If it fires, remove the value and rotate it.

## Build state (`data/build`)

Autonomous-builder data is machine-owned and never changes curated registry
YAML. `runs.jsonl`, `state.json`, and `digests/` are tracked; the active
`lease.json` and kill-switch `STOP` file are ignored.

`lease.json` is created atomically and contains `run_id`, `host`, `pid`,
`project_id`, `repo`, `dry_run`, `started_at`, `ttl_seconds`, `status`,
`prs_open`, `merges`, per-chunk PR/verification/verdict/merge data, the policy
`allow` list and `budget`, `contract_forbidden_paths`, and
`reconcile_targets`, and, while write-back remains, a `finalize` object with
`outcome`, `digest_path`, and `finished_at`. Status is `active`, `reconciling`,
or `finalize_pending`. A finalize-pending lease does not expire: start and
reconcile return a `confirm_writeback` action requiring dashboard regeneration,
commit/push of `data/build` and `DASHBOARD.md`, and
`registry build finish RUN_ID --confirm-writeback`. Other leases expire after
`started_at + ttl_seconds`; cleanup
is planned by `registry build reconcile` and acknowledged with
`registry build reconcile --done`.

Each line of `runs.jsonl` is an event object with these fields:

| Field | Type | Notes |
|---|---|---|
| `ts` | ISO-8601 UTC timestamp | Event time. |
| `run_id` | string | Required, non-empty run identifier. |
| `host` | string | Required execution host. |
| `type` | enum | `run_started`, `candidate_selected`, `contract_bootstrapped`, `contract_repaired`, `chunk_started`, `pr_opened`, `verify_passed`, `verify_failed`, `review_verdict`, `second_opinion`, `merged`, `merge_conflict`, `reverted`, `chunk_rejected`, `chunk_skipped`, `guard_denied`, `needs_intent`, `reconciled`, `writeback_confirmed`, `crashed`, `stopped`, `resumed`, or `run_finished`. |
| `project_id` | string or null | Registry project, when applicable. |
| `chunk_id` | string or null | Chunk identifier, when applicable. |
| `pr_url` | GitHub PR URL or null | Pull request associated with the event. |
| `tag` | string or null | Checkpoint or other event tag. |
| `outcome` | enum or null | Required only for `run_finished`. |
| `reason` | string or null | Machine-readable rejection, skip, or denial reason. |
| `detail` | object or null | Structured event-specific detail. |

Run outcomes are `completed`, `budget_exhausted`, `roadmap_done`,
`needs_intent`, `blocked_by_policy`, `no_candidate`, `lease_held`,
`registry_dirty`, `preflight_failed`, `clone_failed`, `contract_broken`,
`baseline_red`, `codex_unavailable`, `aborted`, `stopped`, `crashed`, `paused`,
`shadow_completed`, `forced_named`, and `finalize_pending`. Malformed journal lines are reported by
`build-report` and skipped; they do not make the report fail.

Startup outcomes `registry_dirty`, `lease_held`, `no_candidate`, and `stopped`
are journaled even though no new lease is issued. A successful finish writes
`digests/<run_id>.md` with the project and outcome, merged PRs and checkpoint
tags, available `git revert <merge-sha>` commands, rejections, guard denials,
pause information, and projects currently needing intent.

`state.json` is keyed by project ID. Each value contains `last_run_at`,
`last_success_at`, `consecutive_failures`, `paused_reason`, `paused_at`,
`chunks_merged_total`, `needs_intent_proposal_id`, `last_run_id`, and
`last_outcome`. Pause reasons are `consecutive_failures`, `revert`,
`contract_broken`, `baseline_red`, or `owner`.

Each rejected chunk and an aborted, crashed, or Codex-unavailable finished run
increments the consecutive-failure count. A merge resets it, records success,
and increments the lifetime merged-chunk count. Three consecutive failures
pause a project. A revert pauses immediately, as do `contract_broken` and
`baseline_red` outcomes. `registry build resume <id>` clears the pause and
failure count and records a `resumed` event.

## Target-repository contract

Each buildable target repository declares its executable boundary in `.project-meta.yaml`. `registry validate-contract <path> --project-id <id>` validates schema version 1 without changing the target.

```yaml
schema: 1
registry_id: my-project
runtime:
  kind: python                    # python | node | go | rust | other
  version: "3.11"
package_manager: pip              # supported manager or other
setup: [python3 -m venv .venv]
test: [.venv/bin/python -m pytest -q]
lint: []
typecheck: []
verify: []
max_test_minutes: 15
network: {allowed: false}         # declarative: may setup/test reach the network? not enforced
secrets_required: []              # names only, never values
services: []                      # non-empty makes the contract non-runnable
generated_files: []
generate: []
personal_data: []
forbidden_paths: [.env, "secrets/**"]
deploy: none                      # the only accepted deployment policy in v1
```

`schema`, `registry_id`, `runtime`, and at least one `test` command are required. Unknown fields, a mismatched registry id, value-like secret entries, or any `deploy` value other than `none` are errors. `runtime.kind`, `package_manager`, command lists, path-glob lists, timeout, and `network.allowed` are type-checked. `network.allowed` has exactly one meaning everywhere: it declares whether the `setup`, `test`, `lint`, `typecheck`, or `verify` commands need network access (installing packages from an index counts). It is a declaration for the owner and reviewer — nothing sandboxes network access at run time, and the guard does not enforce it — so a contract whose `setup` runs `pip install` must say `allowed: true`. A non-empty `services` list is valid metadata but reports `runnable: false` with reason `services`; the builder must pause instead of guessing how to provision infrastructure. Missing contracts are bootstrapped as a reviewed `contract` chunk, while a broken contract receives one repair attempt before the project pauses.

## SPEC item format

An agent-owned roadmap lives at `docs/SPEC.md`. Its `## Remaining work`
section is a Markdown checklist. Checked items are historical and are not
readiness-validated. Every unchecked item uses these indented, case-insensitive
metadata lines (multiple metadata fields may share a line):

```markdown
- [ ] A bounded, checkable change
      Acceptance: the observable condition that proves the change is done
      Tests: a test path or a precise verification description
      Size: S
      Classes: none
      Verified-missing: evidence that the capability does not already exist
```

`Size` is `S` or `M`. `Classes` is `none` or a comma-separated subset of
`dependencies`, `ci`, `generated_data`, `public_api`, `migrations`,
`personal_data`, `plan`, and `contract`. The first six must also appear in the
project's `automation.allow`; `plan` and `contract` are always allowed.

Readiness problems are `missing_acceptance`, `missing_tests`, `missing_size`,
`size_too_large`, `missing_verified_missing`, `unknown_class`,
`blocked_by_policy`, `never_class`, and `needs_intent`. Structural problems are
`missing_remaining_work`, `no_items`, and `malformed_checkbox`.
`contradicts_non_goal` is a suggestion-level warning and does not make an item
unready. `registry validate-spec <project-id> <path>` exits non-zero when the
structure is broken or no unchecked item is ready; an entirely checked list is
successful.

## Observed data (not editable here)

`data/github/snapshot.json` holds repository visibility, archival state, pushes, open PRs, issues, review state, CI state, and remote branch observations. Repository and branch reads have separate timestamps; branch records include head SHA, commit time, protection, default-branch status, and open in-repo PR numbers. `branches_fetched`, `branches_partial`, `branches_skipped`, and `branches_error` keep missing or incomplete evidence from reading as complete. The snapshot is rewritten by `registry sync` and read by everything else. A failed repository refresh keeps the previous entry and marks it `stale: true`; a branch-only failure keeps the previous branch observation, marks it unfetched, and surfaces a partial error.

The `stale_branches` attention rule emits at most one suggestion per repository. It currently uses a 60-day threshold, excludes the default branch and branches with open in-repo PRs, and never treats an unknown commit date or incomplete branch list as known-stale evidence.

`data/understanding/<project-id>.json` holds an evidence brief when one exists. `registry briefs-status` reads `analyzed_at` and `revision`: `analyzed_at` provides the brief age, while `revision` is compared with the snapshot's latest known default-branch head and reported as `current`, `stale`, or `unknown`. Missing, malformed, and revision-unknown briefs stay separate; unknown is never treated as current. `registry sync-status` includes the present/missing/stale summary, and the MCP exposes the same query through `get_briefs_status`.

Briefs are produced out-of-band today, not by `registry sync`, and are git-tracked. The registry only reports their coverage and staleness; it does not generate, refresh, or delete them. They are optional evidence hints and are never authoritative inputs to autonomous eligibility, ranking, or execution.
