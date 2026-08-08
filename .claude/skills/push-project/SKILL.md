---
name: push-project
description: Autonomous portfolio-advancement loop. Use when invoked by the scheduled morning run or as /push-project [project-id]. Picks the top focus project from the project-registry MCP (or uses the named one) and either drafts a spec PR (gear 1, no docs/SPEC.md yet) or ships one implementation PR against the spec's next unchecked chunk (gear 2). At most one project and one PR per run.
---

# push-project

Convert approved intent into shipped diffs. You never invent priorities:
selection comes from the registry's ranking, intent comes from specs the owner
has merged, and every run ends with at most one PR — or a clean "nothing to
do" notification.

## Constants

First detect where you are running — the three environments differ:

- **Mac (interactive)**: `REGISTRY_ROOT` = `/Users/kanumadhok/Documents/Claude/Projects/project-registry`,
  `REGISTRY_CLI` = `$REGISTRY_ROOT/.venv/bin/registry`, `WORKDIR` =
  `<your scratchpad dir>/push/<project-id>` (fresh clone per run, deleted at
  the end), and the Codex adapter at `~/.claude/model-adapters/codex.sh` is
  available for delegation.
- **PC (WSL Ubuntu on the always-on workstation, scheduled)**:
  `REGISTRY_ROOT` = `/home/learnmsds/Github/project-registry`.
  `REGISTRY_CLI` =
  `PYTHONPATH=src python3 -m project_registry.cli --root $REGISTRY_ROOT`.
  `WORKDIR` = `/home/learnmsds/Github/push-work/<project-id>` (fresh clone
  per run, deleted at the end). Codex is `~/bin/codex` (native Linux
  binary) — no adapter script; `gh` is `~/bin/gh` if not on PATH.
  Before selecting, run `git -C $REGISTRY_ROOT pull --ff-only` so the run
  sees the latest curated intent.
- **Cloud routine**: `REGISTRY_ROOT` = the cloned `project-registry`
  checkout (find it — you are started inside or beside it; `ls` the parent of
  your cwd). There is no `.venv`, so `REGISTRY_CLI` =
  `PYTHONPATH=src python3 -m project_registry.cli --root $REGISTRY_ROOT`.
  Target repos are cloned beside it, so `WORKDIR` = the existing checkout of
  the target repo — do NOT `gh repo clone` one that is already present. There
  is no Codex adapter: implement chunks yourself.

Detect in this order: if
`/Users/kanumadhok/Documents/Claude/Projects/project-registry` exists you are
on the Mac; else if `/home/learnmsds/Github/project-registry` exists you are
on the PC; otherwise you are in a cloud routine. Everything else in this
skill is identical everywhere.

- Branch namespace: every branch this skill creates is named `push/<slug>`.

## Invariants — read first, never break

1. One project, at most one PR, per run.
2. Branches and PRs only. Never commit to a default branch, never
   force-push, never touch a repo outside the focus list (exception: a
   project the owner named explicitly in the invocation).
3. Never read, copy, or quote secret values found in a clone — several repos
   have tracked `.env` files and hard-coded keys. Reference filenames only,
   and never into specs, PR bodies, or logs.
4. Registry writes: `record_project_review` (notes + rationale) only. NEVER
   set `lifecycle`, `active`, `priority`, or `next_action` — anything
   intent-shaped goes into `propose_project_update` and stops there for the
   owner.
5. Never open a PR you could not verify (tests run + entire diff read).
6. Never target `project-registry` itself, even though its lifecycle is
   `now` and it would otherwise rank first. It is this skill's own control
   plane, it already has a spec and plan under `docs/superpowers/`, and the
   owner develops it interactively — a cron-opened PR against it would
   collide with live sessions. Skip it during selection and move to the
   next candidate.

## Step 1 — Select

**Named invocation** (`/push-project <id>`): use that project. Still apply
the open-PR brake below unless the owner's message overrides it.

**Bare invocation:**
1. Focus list = `list_projects` (project-registry MCP) with lifecycle `now`,
   then lifecycle `next`, in that order. Empty → Step 4 (outcome
   `empty_focus`), notify
   "focus list is empty — mark projects with: registry record-review <id> --set lifecycle=next"
   and stop.
2. Rank: call `get_attention_queue`. Candidate order = focus-list ids in
   queue order, then focus-list ids absent from the queue. Order the
   absent group by this skill's last touch, oldest first: per repo, run
   `gh pr list --repo <repo> --state all --json headRefName,createdAt`,
   keep PRs whose `headRefName` starts with `push/`, and the newest
   `createdAt` among them is the touch time. Repos with no `push/` PRs
   sort first (never touched); break remaining ties alphabetically by id.
3. Brake: for each candidate in order, run
   `gh pr list --repo <repo> --state open --json number,headRefName,createdAt`
   and skip the project if any `headRefName` starts with `push/`.
4. First surviving candidate wins. If none survive → Step 4 (outcome
   `parked`), notify
   "all focus projects are waiting on your review: <open push/ PR urls>"
   and stop. That is a successful run.

## Step 2 — Load context

- `get_project <id>` → purpose, notes, relationships, `repo`, visibility,
  observed GitHub state.
- Evidence brief: read `$REGISTRY_ROOT/data/understanding/<id>.json`.
- Get the code: on the Mac and the PC, `gh repo clone <repo> $WORKDIR -- --depth 50`. In a
  cloud routine the repo is already checked out beside the registry — use
  that checkout and skip cloning. Either way, then read README,
  CLAUDE.md / AGENTS.md if present, `docs/`, and `git log --oneline -15`.

## Step 3 — Gear check

Does `docs/SPEC.md` exist on the default branch of the clone?
Absent → Gear 1. Present → Gear 2. An empty repo (no commits, unborn
branch) counts as absent → Gear 1.

## Gear 1 — draft the spec, stop

Write `docs/SPEC.md` in exactly this shape:

```markdown
# <Project name> — spec

_Drafted by push-project on <date>. Merging this PR approves the spec;
editing the checklist re-prioritizes the work._

## Goal
<the registry purpose, adjusted only for wording — never re-invented>

## Done looks like
- <3–5 concrete, checkable acceptance criteria>

## Current state
<what already exists — facts only, grounded in the evidence brief and code>

## Remaining work
- [ ] <chunk 1 — one coherent capability, reviewable in one sitting, tests included>
- [ ] <chunk 2>

## Non-goals
- <what this project deliberately will not do>
```

Rules: 3–8 chunks, ordered, each shippable as a single PR. If the repo is
too immature to spec honestly, say so under Current state and make chunk 1
the smallest step that changes that.

Before writing any chunk that adds a capability, prove the capability is
missing. Grep the codebase for it and enumerate the CLI's registered
subcommands (for argparse, `grep -n "add_parser("`); a mature repo often
already has the thing the issue tracker still asks for. A chunk that
rebuilds working code is worse than no chunk — it burns a run and produces
a PR the owner must reject. If the capability exists but is unused,
untested, or stale, say so and make the chunk "run it, verify the result,
add the missing test" instead of "build it".

Every number in *Current state* must come from a command you ran against
this clone in this run, and the command must be scoped to exactly what you
claim. Counting files in a subdirectory means `ls <dir>/<glob> | wc -l`, not
a repo-wide `find` — a recursive sweep silently pulls in matches from other
directories and inflates the count. Re-run each count immediately before
writing the sentence that cites it, and never carry a number over from the
evidence brief: the brief is a cache from an older commit and its counts
drift. A number you cannot reproduce on demand does not go in the spec.

Then: branch `push/spec`, commit only
`docs/SPEC.md`, open a PR titled `Spec: <one-line goal>` whose body restates
the merge-is-approval contract. Do Step 4 and Step 5, then stop.

## Gear 2 — ship one chunk

1. Take the first unchecked `- [ ]` item under **Remaining work**.
2. Reality-check it against the clone. Already done, or obsoleted by how the
   code has evolved? → open a spec-amendment PR instead (branch
   `push/spec-amend`, checklist updated, reason in the body), Step 4 + 5, stop.
3. Baseline: run the repo's test suite before touching anything; record the
   result. No test suite → note that in the PR body. "Verified" below means
   **no new failures**, and a red baseline is flagged, not silently fixed.
4. Expand the chunk into a precise task spec: files to touch, behavior,
   acceptance criteria, tests to add.
5. Implement per the model-routing policy:
   - Taste-critical (UI, user-facing copy, API/SDK shape) → implement it
     yourself.
   - Otherwise delegate the task spec (plus relevant file excerpts):
     - Mac: pipe it to `~/.claude/model-adapters/codex.sh exec --prompt -
       --cd $WORKDIR --sandbox workspace-write --label push-<id>`.
     - PC: pipe it to `~/bin/codex exec --cd $WORKDIR --sandbox
       workspace-write -` (prompt on stdin).
     Codex not logged in or the invocation itself errors → abort and
     notify; no silent fallback.
     In a cloud routine there is no Codex: implement the chunk yourself to
     the same standard, and verify it identically.
6. Verify: run the full test suite (no new failures) and read the entire
   diff. Misses the bar → fix inline or redo once; a second miss → delete
   the branch, Step 4 with outcome "aborted", notify with what was
   attempted, stop.
7. Check off the chunk in `docs/SPEC.md` in the same diff. Branch
   `push/<chunk-slug>`, open the PR: chunk text, acceptance criteria, test
   results, any red-baseline flag.

## Step 4 — Write back

Every run writes back, whatever the outcome — parked and empty-focus runs
are recorded too (they used to leave no trace, which made "ran, nothing to
do" indistinguishable from "never ran").

**Run journal (always):** append exactly one line to
`$REGISTRY_ROOT/data/push_runs.jsonl` (git-tracked, append-only; create if
absent):

```json
{"ts": "<UTC ISO-8601>", "host": "mac|pc|cloud", "project": "<id or null>", "gear": 1, "outcome": "spec|chunk|amend|aborted|parked|empty_focus", "pr": "<url or null>"}
```

`gear` is `null` when no gear was reached. Never rewrite or delete earlier
lines.

**Project review (only when a project was advanced or aborted):** call
`record_project_review` on the registry MCP with exactly these arguments
and no others:

- `project_id`: the project id
- `approved`: `true` — REQUIRED. Without it the tool files a pending
  proposal, applies nothing, and still reports success; the review is
  silently lost.
- `rationale`: `push-project <date>: gear <1|2>, <PR url or outcome>`
- `updates`: `{"notes": "<full replacement text>"}` — the existing notes
  (read them via `get_project` first) plus one appended line
  `push-project <date>: <spec drafted | chunk shipped | amended | aborted> <PR url>`.
  Notes replace wholesale; there is no top-level `notes` parameter.

The tool will technically accept other curated paths inside `updates`
(`lifecycle`, `active`, `priority`, `next_action.*`). You MUST NOT pass
them — Invariant 4 binds you, not the tool's permissiveness; `notes` is
the only allowed key.

Then commit the registry's own changes:
`cd $REGISTRY_ROOT && $REGISTRY_CLI dashboard && git add registry/ data/proposals/ data/audit_log.jsonl data/push_runs.jsonl DASHBOARD.md && git commit -m "push-project: record run for <id or outcome>" && git push`
(`record_project_review` also writes `data/proposals/` and `data/audit_log.jsonl`, which are git-tracked by design).

On the Mac and the PC that push goes straight to `main`. In a cloud routine,
if `main` is protected, push a `push/registry-run-<date>` branch **and open a
PR for it** — a write-back branch without a PR strands the run record, which
is exactly what happened to the 2026-08 cloud runs.

## Step 5 — Notify

Load the PushNotification tool via ToolSearch (`select:PushNotification`)
and send one line: `<id> · gear <1|2> · <PR title> — <url>` (or the
parked / empty-focus message). If the tool is unavailable, print the same
line as the final message instead — the tool being absent in a headless
run is normal, not an error.

Finally, delete `$WORKDIR`.

## Failure handling

| Case | Behavior |
|---|---|
| Clone or auth failure | Skip to the next focus candidate; notify if all fail |
| Codex not logged in / invocation errors | Abort with notification |
| Pre-existing red tests | Baseline first; verify = no NEW failures; flag in PR body |
| Verification fails twice | Abort, delete branch, notify with what was attempted |
| Spec chunk obsolete | Spec-amendment PR instead of code |

Nothing is written anywhere until a branch is pushed or Step 4 runs, so a
crashed run is safe to simply rerun. (A crash before Step 4 leaves no
journal line — the scheduler's own log is the record that the run started.)
