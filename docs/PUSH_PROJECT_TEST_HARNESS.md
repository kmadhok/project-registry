# push-project test harness — repeatable procedure

> **Superseded as a procedure.** The
> `push-project-observe` skill (`~/.claude/skills/push-project-observe/SKILL.md`)
> now automates every phase below — invoke it as
> `/push-project-observe <owner/repo>` instead of following this by hand.
>
> This document is kept as the **rationale**: why each step exists and which
> trap it avoids. Read it when changing the skill, not when running it.

How to observe `push-project`'s real behavior without spending approval signal
on a live repo. This is the 2026-08-02 audit run
([`PUSH_PROJECT_AUDIT.md`](./PUSH_PROJECT_AUDIT.md)) reduced to a procedure that
can be re-run after any `SKILL.md` change.

**What this is for.** `push-project` runs unattended on a schedule. Reading
`SKILL.md` tells you what it is supposed to do; only a run tells you what it
does. This procedure makes that observation cheap and safe enough to repeat.

---

## The design constraint everything follows from

Merging a push-project PR **is** the approval signal — `SKILL.md:104-105`,
"Merging this PR approves the spec". And `Step 4` write-backs land in
`data/audit_log.jsonl`, which is append-only; a repo-side `git revert` does not
unwind them.

So testing with auto-merge requires a repo **where merging means nothing**.
That single fact determines the whole setup. The owner must not be the approval
bottleneck, and the only way to have both is a disposable target.

Two properties the target must have:

1. **Reachable on purpose** — the skill can run against it by name.
2. **Unreachable by accident** — no bare run, and no scheduled Routine
   (`docs/ROUTINE_SETUP.md`), can ever select it.

---

## Phase 0 — Baseline before touching anything

Record `push/` PR counts on the real focus repos. This is what proves, later,
that no real repo was touched — and it is worthless if captured after the fact.

```bash
# Resolve real repo names from the registry — they do NOT match project ids
.venv/bin/registry list --lifecycle now --lifecycle next
.venv/bin/registry show <id>            # read the `repo` field for each

for r in <repo1> <repo2> <repo3> <repo4>; do
  echo -n "$r: "
  gh pr list --repo "$r" --state all --json headRefName \
    --jq '[.[] | select(.headRefName | startswith("push/"))] | length'
done
```

**Gotcha, learned the hard way.** The first attempt guessed `kmadhok/<project-id>`
and two of four 404'd — real names use underscores, capitals, and a trailing
hyphen. Read `repo` from the registry; never construct it.

Baseline on 2026-08-02 was `1, 1, 1, 0`, plus `0` on `kmadhok/project-registry`
(invariant 6).

---

## Phase 1 — Build the sandbox

### 1.1 Copy a real repo

**GitHub refuses same-owner forks outright** — `gh repo fork --fork-name` does
not work around it. Mirror-push instead:

```bash
git clone --mirror git@github.com:kmadhok/<source>.git /tmp/src.git
gh repo create kmadhok/push-sandbox-<name> --private
git -C /tmp/src.git push --mirror git@github.com:kmadhok/push-sandbox-<name>.git
```

This is arguably better than a fork: an independent copy with full history and
no fork relationship to the live repo. Note the consequence — `is_fork` is
**false**, not true. The 2026-08-02 plan assumed a fork and had to be corrected
mid-run.

**Pick a source with real code.** An empty repo exercises gear 1 cleanly but
produces thin chunks, so gear 2 has nothing substantive to ship.

### 1.2 Verify it is a genuine gear-1 target

```bash
gh api repos/kmadhok/push-sandbox-<name>/contents/docs/SPEC.md   # must 404
```

Absent `docs/SPEC.md` → gear 1 (`SKILL.md:94`). If the source already has one,
gear 1 is unreachable and the chain starts at gear 2.

### 1.3 Register it inert

Hand-write `registry/projects/push-sandbox-<name>.yaml`. Filename must match
`id` (`SCHEMA.md:3`). A direct write is correct here — the proposal workflow
governs agent edits to *existing* curated fields, and `propose` cannot create a
project.

```yaml
id: push-sandbox-<name>
name: push-sandbox-<name>
purpose: Disposable copy of <source> used to observe push-project behavior
  without consuming approval signal on a live repository. Not real work.
lifecycle: incubating
active: false
category: tooling
repo: kmadhok/push-sandbox-<name>
visibility: private
tags:
  - sandbox
  - push-project
  - disposable
notes: 'Created <date> as a push-project test target. Delete when the audit is complete.'
```

Every field choice is load-bearing:

| Choice | Why |
|---|---|
| `lifecycle: incubating` | Avoids the `desired_outcome` requirement that `now` imposes (`validation.py:210`) |
| `active: false` | Avoids the `next_action` requirement that `active` imposes (`validation.py:158`) |
| **No `next_action`** | Adding one creates an obligation — `next_action_unreviewed` errors even on inactive projects (`validation.py:191-197`) — *and* omitting it keeps the sandbox out of the attention queue (`queries.py:427-428`) |
| No unknown keys | `model.py:357-361` raises `RegistryError` on any unrecognized field, which **breaks the load for the entire registry**, not just this file |
| `tags` as a list | A bare string is a hard parse error (`model.py:133-140`) |
| Plain `notes` | Scanned by the `credential_material` regex (`validation.py:392`) |

### 1.4 Prove both safety mechanisms

Do not skip this. It is the check that makes auto-merge defensible.

```bash
.venv/bin/registry validate                                    # exit 0
.venv/bin/registry list --lifecycle now --lifecycle next       # sandbox ABSENT
.venv/bin/registry show push-sandbox-<name>                    # reachable by name
.venv/bin/registry attention | grep -c push-sandbox            # 0
```

Two independent mechanisms must both hold: `list_projects(now/next)` never
returns an incubating project, and `get_attention_queue` omits any project with
no `next_action`. Either alone would be enough; requiring both is why the
guarantee holds.

### 1.5 Commit clean

```bash
python3 -m pytest && .venv/bin/registry dashboard
git add registry/projects/ DASHBOARD.md && git commit
```

Going into the experiment with a dirty registry makes the post-run diff
unreadable.

---

## Phase 2 — Run the chain

Always **named**: `/push-project push-sandbox-<name>`. Never bare. A bare run
consults the focus list and could reach a real repo.

```
gear 1 → PR #1 → merge → gear 2 → PR #2 → merge
```

The gear flip is automatic: once `docs/SPEC.md` is on `main`, the same
invocation routes to gear 2 (`SKILL.md:94`).

**Two deviations to expect and tolerate** — both consequences of a brand-new
project, both findings rather than bugs to patch mid-run:

- **No evidence brief.** `SKILL.md:86` reads `data/understanding/<id>.json`;
  nothing generates these. Proceed on code alone.
- **No GitHub snapshot entry.** A new repo post-dates the last `registry sync`,
  so `get_project` returns `"github": null`. Degrades; never fails.

**Merge without reading.** That is the entire point of the sandbox. Reviewing
reintroduces the bottleneck this design exists to remove.

---

## Phase 3 — Observe, independently

The run's own claims are not evidence. Re-verify from a **fresh clone**, not the
skill's workdir.

```bash
git clone git@github.com:kmadhok/push-sandbox-<name>.git /tmp/verify
# Re-run 2-3 numbers the spec cites, using scoped commands
# Re-run the test commands the PR body claims
```

The 2026-08-02 run's own scoped-count rule caught a `__pycache__`-inflated
count (10 → 9) *before* it shipped. Independent re-verification then confirmed 9
by a different method — the repo's own `run_eval.py --list`. Two independent
measures agreeing is what makes a number safe to cite.

**What to record** — the questions that produced every finding in the audit:

- Did gear detection route correctly, including past decoy spec files?
- Is every number in *Current state* reproducible with a scoped command?
- Was capability absence **proven** before a chunk claimed to build something?
- Are the chunks genuinely single-PR shippable (`SKILL.md:125`)?
- Did `Step 4` touch **only** `notes` (invariant 4)?
- **Where did it guess?** Test command, docs layout, conventions. These guesses
  are the direct input to the repo-standard work
  ([`FINDINGS_REPO_HYGIENE.md`](./FINDINGS_REPO_HYGIENE.md)).

**Verification must reason, not diff numbers.** `evals/` moved from "31 passed,
1 skipped" to "32 passed" — a skip becoming a pass because the shipped manifest
installed `reportlab`. A naive "same numbers or bust" check flags that as a
regression. Explain a changed count; do not match it.

---

## Phase 4 — Prove nothing real was touched

```bash
# Focus repos: identical to the Phase 0 baseline
for r in <repo1> <repo2> <repo3> <repo4>; do ...same command as Phase 0... done

# Invariant 6
gh pr list --repo kmadhok/project-registry --state all --json headRefName

# Invariant 4 — only `notes` + `last_reviewed` changed
git diff HEAD~2 -- registry/projects/push-sandbox-<name>.yaml
tail -3 data/audit_log.jsonl
```

Phase 0 without Phase 4 proves nothing, and Phase 4 without Phase 0 is
unfalsifiable. They are one check split across time.

---

## Phase 5 — Provenance

Record identifiers so the run can be cited from another session or model:

| Item | How to get it |
|---|---|
| Session id | The current Claude Code session id |
| Transcript | `~/.claude/projects/<slug>/<session-id>.jsonl` |
| Model | Exact model id, e.g. `claude-opus-5[1m]` |
| Codex report | `~/.claude/model-reports/codex-*.md` if delegation ran |
| Sandbox `main` SHA | after both merges |
| PR numbers + merge SHAs | |
| Registry commit | at write-up |

**Caveat worth repeating in the write-up:** if the session resumed after a
`/clear`, several session ids exist in the same project directory. Name the one
that did the work.

---

## Phase 6 — Teardown

```bash
rm registry/projects/push-sandbox-<name>.yaml
.venv/bin/registry validate && .venv/bin/registry dashboard
gh repo delete kmadhok/push-sandbox-<name>
```

The audit doc and audit-log entries **stay** — they are the record of what was
learned.

---

## What this procedure does NOT cover

Stated plainly, because the 2026-08-02 run's biggest weakness was presenting
happy-path coverage as thorough.

**Every run above is a success path.** All five of the skill's failure
behaviors (`SKILL.md:212-222`) went unexercised. Those tests — T1–T5 — are
specified in
[`FINDINGS_PUSH_PROJECT_SKILL.md#s4`](./FINDINGS_PUSH_PROJECT_SKILL.md), and
they reuse this same sandbox with one condition changed per run.

**Step 1 selection is structurally out of reach.** The inert registration that
makes the sandbox safe is exactly what prevents observing the attention-queue
ranking, never-touched ordering, and open-PR brake. Testing the brake (T4)
requires a bare invocation against the real focus list — a different, riskier
procedure that needs owner approval.

That trade is deliberate and worth restating: **the sandbox guarantees no real
repo is touched, at the cost of never testing the code that decides which repo
to touch.**

---

## Cost

The 2026-08-02 run: ~18 minutes wall-clock for both gears including
verification, plus setup. Gear 1 alone took ~20 tool calls, most spent on
discovery a repo standard would eliminate — finding the test command, locating
docs, proving capability absence.

That cost profile is itself a finding: it is the argument for
[`FINDINGS_REPO_HYGIENE.md`](./FINDINGS_REPO_HYGIENE.md).

---

## Automation — worth it only if

This is a **manual procedure**. Six phases, and re-running it after a
`SKILL.md` change means reconstructing the setup by hand.

A real harness would stage each condition, invoke the skill, and assert on the
resulting PR. `docs/IMPLEMENTATION_PLAN.md` and the untracked SDK-harness work
(`a072c8e`) point at that. Build it when `SKILL.md` changes often enough to need
regression coverage — not before. Right now the procedure has run once, and one
data point is not a corpus to automate against.
