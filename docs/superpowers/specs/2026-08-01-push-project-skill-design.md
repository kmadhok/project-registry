# push-project: an autonomous skill that converts registry context into shipped PRs

**Date:** 2026-08-01
**Status:** Approved design, pre-implementation
**Owner:** Kanu Madhok

## Problem

The project registry solved context retrieval: every repo has a curated purpose,
an evidence brief, and live GitHub state, all queryable over MCP. What it
deliberately does not solve is "what should I do next" — `next_action`,
`active`, and priority are owner commitments, and almost no project has them.
The result: context is available, but projects still don't move.

`push-project` fills that gap. It is a converter of **approved intent into
shipped diffs**: it never invents priorities, never ranks by its own judgment,
and ends every run with either a spec awaiting approval or a PR awaiting
review. The human's entire interaction surface is the GitHub PR queue plus the
registry's focus markers.

## Decisions made during design

| Question | Decision |
|---|---|
| End state of a run | Shipped work — a branch/PR ready for review |
| Project selection | Registry work-queue ranking over the focus list; owner can override by naming a project |
| No-spec case | Draft a spec, open it as a PR, stop; merging the spec PR is approval |
| Trigger | Automatic (cron), not human-invoked, after a manual earn-in period |
| Focus list | The registry itself (`lifecycle: now`/`next`), not a separate file |

## Architecture

Three existing pieces, one new one:

- **Registry MCP server** (exists) — selection and context: work-queue,
  `get_project`, evidence briefs.
- **Codex adapter** (exists, `~/.claude/model-adapters/codex.sh`) — mechanical
  implementation from a fixed spec, per the model-routing policy.
- **`gh` CLI** (exists) — clone, branch, PR. The registry codebase's GET-only
  GitHub client is untouched; mutations happen from the skill's own session.
- **The skill** (new) — instructions in `~/.claude/skills/push-project/`,
  invoked bare by a scheduled headless session or as `/push-project <id>` by
  the owner.

## Focus list

Marking a project `lifecycle: next` (or `now`) **is** adding it to the focus
list — that is what the vocabulary means, and it is an owner-only field, so
selection authority stays with the human. One command per project:

```bash
registry record-review <id> --set lifecycle=next
```

Verified: `next` has no hard validation requirements (`now` requires
`desired_outcome`; "inactive but committed" is a suggestion, not an error).
No new file, no schema change; the dashboard shows the focus automatically and
every other session sees the same list through the MCP.

## Trigger and pacing

A scheduled headless Claude session (weekday mornings) invokes the skill bare.
Each run touches **exactly one project** and produces **at most one PR** —
spec PR or work PR — then sends a push notification with what it did and what
is now blocked on the owner.

Skill-authored work is identifiable by convention: branches are named
`push/<chunk-slug>`, so "open skill-authored PR" is an exact `gh pr list`
query, and "least recently pushed by this skill" falls out of PR history.

Self-regulating brake: a focus project with an open skill-authored PR is
**skipped**. If every focus project is blocked on review, the run does nothing
except notify "everything is waiting on you." Work can never pile up faster
than it is reviewed; if the owner goes quiet, the loop parks itself.

## Anatomy of a run

1. **Select.** Query the MCP work-queue, filter to lifecycle `now`/`next`,
   drop projects with an open skill-authored PR, take the top item
   (tie-break: least recently pushed by this skill). Log the pick and the
   registry's stated reasons.

2. **Load context.** Three layers: curated YAML (purpose, notes,
   relationships), the evidence brief (`data/understanding/<id>.json`), and a
   fresh clone (README, CLAUDE.md, code, recent commits).

3. **Gear check** — does `docs/SPEC.md` exist on the default branch?

   **Gear 1 — no spec.** Synthesize one from purpose + brief + code, in a
   fixed format:
   - *Goal* — taken from the registry purpose, not re-invented
   - *Done looks like* — 3–5 concrete acceptance criteria
   - *Current state* — what exists, cited from the evidence brief
   - *Remaining work* — an ordered **checklist of PR-sized chunks** (each one
     coherent capability, reviewable in one sitting, tests included)
   - *Non-goals* — what the skill must not drift into

   The spec goes up as a PR containing only `docs/SPEC.md`. **Merging the PR
   is approval**; editing the checklist is re-prioritizing the work. The run
   stops here.

   **Gear 2 — spec on main.** Take the first unchecked chunk from *Remaining
   work*, expand it into a precise task spec, and delegate implementation to
   Codex (the chunk is now a fixed spec — the routing policy's "doing" case).
   The session verifies before anything ships: run the tests, read the full
   diff, fix or redo if it misses the bar. The PR checks off the chunk in
   `SPEC.md` in the same diff, so merged PRs advance the checklist
   automatically. Taste-critical chunks (UI, copy, API shape) the session
   implements itself instead of delegating.

   When the last chunk is checked, the skill notifies "spec complete — extend
   the spec or change the lifecycle" and skips the project thereafter. It
   never decides what comes next.

4. **Write back.** `registry record-review <id>` with a rationale and an
   updated `notes` pointer (run date, gear, PR link). The skill **never**
   writes `lifecycle`, `active`, `priority`, or `next_action`; anything
   intent-shaped goes through `registry propose` for the owner to apply or
   ignore.

5. **Notify.** One push notification: project, gear, PR link, what is blocked
   on the owner.

## Guardrails

- **Branches and PRs only.** Never push to a default branch, never
  force-push, never touch repos outside the focus list.
- **Secrets.** Never read, copy, or quote secret values found in clones
  (several candidate repos have committed credentials). Filenames only, and
  never into specs, PRs, or logs.
- **Registry write boundary** as in step 4.
- **Registry codebase boundary.** No GitHub-mutation capability is added to
  the registry code; the skill uses `gh` in its own session.

## Failure handling

The run is idempotent up to the PR — nothing is written anywhere until a
branch is pushed, so a crashed run can simply rerun.

| Case | Behavior |
|---|---|
| Clone/auth failure | Skip to next focus project; notify if all fail |
| Codex unavailable (adapter exit 4) | Abort with notification; no silent fallback |
| Pre-existing red tests | Record the baseline first; "verified" = no *new* failures; flag red baseline in the PR description |
| Verification fails twice | Abort and notify with what was attempted; never ship an unverified PR |
| Spec drifted from code reality | Downgrade to a spec-amendment PR instead of implementing against a stale plan |

## Cost

One run = one headless orchestration session + typically one Codex call.
Weekday-daily cadence ≈ five runs/week.

## Rollout — earn the cron

1. **Manual, named:** `/push-project <id>` on one owner-picked project. Judge
   the first gear-1 spec hard — it is the template for everything after.
2. **Manual, bare:** merge that spec; invoke bare twice; verify selection
   picks sensibly and gear 2 ships a mergeable chunk.
3. **Cron on:** enable the schedule with 2–3 projects in focus.

## Success metric

**Merged** PRs per week on focus projects — not PRs opened. If more skill PRs
are closed than merged, fix the spec format, not the loop: every bad PR traces
back to a chunk approved in a spec.

## Out of scope (deliberately)

- Multi-project fan-out per run, or parallel workflows — one project, one PR.
- Registry schema changes (e.g. `spec_status` fields) — `git ls-tree` on the
  clone answers the gear check; revisit only if the convention proves out.
- The skill setting any intent field, ever.
