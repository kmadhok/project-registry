# push-project Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `push-project` personal skill — an autonomous loop that picks a focus project from the project-registry MCP and ends every run with a spec PR or one implementation PR — then stage its rollout to a weekday cron.

**Architecture:** Pure instructions, no new code: one `SKILL.md` in `~/.claude/skills/push-project/` orchestrating four existing pieces (registry MCP for selection/context, `gh` for clone/branch/PR, the Codex adapter for mechanical implementation, PushNotification for the report). Rollout is gated: manual named run → manual bare runs → cron.

**Tech Stack:** Claude Code personal skill (markdown), project-registry MCP (user scope), `gh` CLI, `~/.claude/model-adapters/codex.sh`, Claude Code cron.

**Testing note:** There is no unit-testable code in this plan — the deliverable is instructions plus configuration. Verification is the preflight task (each dependency proven with an exact command) and the staged rollout (each gear exercised on a real project before the cron exists). Do not invent pytest files for this.

**Design spec:** `docs/superpowers/specs/2026-08-01-push-project-skill-design.md` — the plan implements it exactly; consult it for rationale.

Facts this plan relies on (verified 2026-08-01):
- Registry project ids are slugs (e.g. `fitness-sync`); each YAML carries `repo: kmadhok/<Name>` and briefs live at `data/understanding/<id>.json`.
- MCP tool names: `list_projects`, `get_project`, `get_attention_queue`, `record_project_review`, `propose_project_update`.
- `~/.claude` is NOT a git repo — the skill file is not committed anywhere; the registry repo holds spec + plan.
- `lifecycle: next` has no hard validation requirement; `now` requires `desired_outcome`.

---

### Task 1: Write the skill file

**Files:**
- Create: `~/.claude/skills/push-project/SKILL.md`

- [ ] **Step 1: Create the file with exactly this content**

````markdown
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

- `REGISTRY_ROOT` = `/Users/kanumadhok/Documents/Claude/Projects/project-registry`
- `REGISTRY_CLI` = `$REGISTRY_ROOT/.venv/bin/registry`
- `CODEX` = `~/.claude/model-adapters/codex.sh`
- `WORKDIR` = `<your scratchpad dir>/push/<project-id>` (fresh clone per run, delete at the end)
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
   then lifecycle `next`, in that order. Empty → notify
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
4. First surviving candidate wins. If none survive → notify
   "all focus projects are waiting on your review: <open push/ PR urls>"
   and stop. That is a successful run.

## Step 2 — Load context

- `get_project <id>` → purpose, notes, relationships, `repo`, visibility,
  observed GitHub state.
- Evidence brief: read `$REGISTRY_ROOT/data/understanding/<id>.json`.
- Clone: `gh repo clone <repo> $WORKDIR -- --depth 50`, then read README,
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
   - Otherwise delegate: pipe the task spec (plus relevant file excerpts) to
     `$CODEX exec --prompt - --cd $WORKDIR --sandbox workspace-write --label push-<id>`.
     Adapter exit 4 (not logged in) → abort and notify; no silent fallback.
6. Verify: run the full test suite (no new failures) and read the entire
   diff. Misses the bar → fix inline or redo once; a second miss → delete
   the branch, Step 4 with outcome "aborted", notify with what was
   attempted, stop.
7. Check off the chunk in `docs/SPEC.md` in the same diff. Branch
   `push/<chunk-slug>`, open the PR: chunk text, acceptance criteria, test
   results, any red-baseline flag.

## Step 4 — Write back

Call `record_project_review` on the registry MCP with exactly these
arguments and no others:

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
`cd $REGISTRY_ROOT && .venv/bin/registry dashboard && git add registry/ data/proposals/ data/audit_log.jsonl DASHBOARD.md && git commit -m "push-project: record run for <id>" && git push`
(`record_project_review` also writes `data/proposals/` and `data/audit_log.jsonl`, which are git-tracked by design).

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
| Codex adapter exit 4 | Abort with notification |
| Pre-existing red tests | Baseline first; verify = no NEW failures; flag in PR body |
| Verification fails twice | Abort, delete branch, notify with what was attempted |
| Spec chunk obsolete | Spec-amendment PR instead of code |

Nothing is written anywhere until a branch is pushed, so a crashed run is
safe to simply rerun.
````

- [ ] **Step 2: Verify the file parses as a skill**

Run: `head -5 ~/.claude/skills/push-project/SKILL.md`
Expected: the frontmatter opens with `---` and `name: push-project` — matching the convention of the other skills in `~/.claude/skills/`.

---

### Task 2: Preflight — prove every dependency with one command each

**Files:** none (verification only)

- [ ] **Step 1: Registry MCP answers from a neutral directory**

Run: `cd ~ && claude -p "Using the project-registry MCP list_projects tool with lifecycle filter 'next', reply with only the project ids, or 'none'."`
Expected: a short answer (currently `none` — the focus list hasn't been seeded yet). Any MCP connection error fails this step.

- [ ] **Step 2: gh is authenticated with repo scope**

Run: `gh auth status`
Expected: logged in as `kmadhok`, token scopes include `repo`.

- [ ] **Step 3: Codex adapter round-trips**

Run: `echo "Reply with exactly: OK" | /opt/homebrew/bin/gtimeout 180 ~/.claude/model-adapters/codex.sh exec --prompt - --sandbox read-only --label push-preflight; echo "exit: $?"`
Expected: a `REPORT: <path>` line and `exit: 0`. Exit 4 means Codex is logged out — stop and tell the owner.

- [ ] **Step 4: Registry CLI works for the write-back path**

Run: `cd /Users/kanumadhok/Documents/Claude/Projects/project-registry && .venv/bin/registry validate | tail -1`
Expected: `0 error(s)` (suggestions are fine).

---

### Task 3: Seed the focus list (owner decision required)

**Files:**
- Modify: `registry/projects/<chosen-id>.yaml` (via `record-review`, never by hand)
- Modify: `DASHBOARD.md` (regenerated)

- [ ] **Step 1: Ask the owner which 1–3 projects to focus on**

Present the 4 `maintained` projects (ai-news-aggregator, dotfiles, interview-prep, remote-workstation) and the `now` project as natural candidates, but the choice is theirs — any project qualifies.

- [ ] **Step 2: Mark each chosen project**

Run (per project): `.venv/bin/registry record-review <id> --set lifecycle=next --rationale "Added to push-project focus list"`
Expected: success message; `registry list --lifecycle next` now shows the project.

- [ ] **Step 3: Regenerate dashboard, validate, commit**

```bash
cd /Users/kanumadhok/Documents/Claude/Projects/project-registry
.venv/bin/registry dashboard && .venv/bin/registry validate
git add registry/ data/proposals/ data/audit_log.jsonl DASHBOARD.md && git commit -m "Seed push-project focus list" && git push
```
Expected: validate reports 0 errors; commit pushed.

---

### Task 4: Rollout stage 1 — manual named run (gear 1)

**Files:** none here (the run creates a PR in the target repo and a registry write-back commit)

- [ ] **Step 1: Invoke the skill on one owner-picked focus project, in-session**

In an interactive session: `/push-project <id>` (use the owner's pick from Task 3).
Expected: a `push/spec` branch and a PR titled `Spec: …` containing only `docs/SPEC.md`; a registry write-back commit; a notification line.

- [ ] **Step 2: Judge the spec PR against this checklist with the owner**

- Goal restates the registry purpose (not re-invented)
- Every "Done looks like" item is checkable
- Current state cites real evidence, no invention
- Each Remaining-work chunk is one PR-sized capability with tests
- Non-goals present
Expected: owner either merges (approval) or edits the checklist in the PR. Spec quality issues get fixed by amending `SKILL.md`'s Gear 1 rules, not by hand-editing this one spec.

---

### Task 5: Rollout stage 2 — manual bare runs (selection + gear 2)

**Files:** none here

- [ ] **Step 1: After the owner merges the spec PR, invoke bare**

In an interactive session: `/push-project`
Expected: selection announces the pick and the registry's reasons; because the spec is now on main, gear 2 ships a `push/<chunk-slug>` PR with tests run and the chunk checked off in `SPEC.md`.

- [ ] **Step 2: Invoke bare again without merging**

Run: `/push-project`
Expected: the same project is **skipped** (open `push/` PR brake); the run either picks another focus project or reports "all focus projects are waiting on your review". This proves the loop parks itself.

- [ ] **Step 3: Owner reviews the gear-2 PR**

Merge bar: would you have merged this from a competent contractor? If not, fix `SKILL.md` (chunk expansion or verification rules) before enabling cron.

---

### Task 6: Enable the cron (only after Tasks 4–5 pass)

**Files:** none (Claude Code cron registration)

- [ ] **Step 1: Load the cron tool schema**

Use ToolSearch with query `select:CronCreate,CronList`.

- [ ] **Step 2: Create the job**

Create a cron job with schedule `0 7 * * 1-5` (weekday mornings, local time), prompt `/push-project`, running headless in `/Users/kanumadhok/Documents/Claude/Projects/project-registry` (a directory where the MCP server and registry CLI are both guaranteed present).
Expected: CronList shows the job with the correct schedule.

- [ ] **Step 3: Confirm the first scheduled run**

After the first weekday morning, check: a push notification arrived, and either a new `push/` PR exists or the "waiting on review" message was sent. If neither happened, inspect the cron session's transcript before touching the schedule.
