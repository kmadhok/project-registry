# Findings — the `push-project` skill

Findings from the behavioral audit that belong to the skill itself
(`~/.claude/skills/push-project/SKILL.md`) — what its instructions say, what
they omit, and what the two runs proved about its design.

**Source:** [`PUSH_PROJECT_AUDIT.md`](./PUSH_PROJECT_AUDIT.md), 2026-08-02.
Two runs against `kmadhok/push-sandbox-interview-prep`: gear 1 (PR #1, merged
`2f585f0`) and gear 2 (PR #2, merged `70e1fc0`).

**Headline.** Only one finding here is a pure defect in the skill. Three of the
audit's five findings live elsewhere —
[repo hygiene](./FINDINGS_REPO_HYGIENE.md) and
[registry/MCP](./FINDINGS_REGISTRY_MCP.md). The skill's core mechanics — gear
routing, the capability-absence rule, scoped counting, the checklist-as-queue —
all worked, several of them catching errors that would otherwise have shipped.

---

## What the runs proved works

Recorded because a findings document that lists only defects misrepresents the
result.

**Gear detection is correctly strict** (`PUSH_PROJECT_AUDIT.md:50-54`). The
target contains `docs/spec/productionalization-v1.md` and
`docs/intent/productionalization.md` but no `docs/SPEC.md`. The check keys on
the exact path, so it routed to gear 1 despite substantial existing spec
material. A looser check — "does a spec exist?" — would have mis-routed. The
strictness that looks like a limitation is the feature.

**The capability-absence rule prevented two bad chunks**
(`PUSH_PROJECT_AUDIT.md:56-66`). It requires proving a capability is missing
before writing a chunk that builds it. The repo already had a complete eval
harness — 9 behavior directories each with `contract.md` *and* `verify.py`, plus
`trace_step.py` and `render_run_report.py` — so "build an eval harness" would
have been rejected on sight. Conversely `infra/` and `/onboard` were confirmed
genuinely absent (`verify_setup` appears in **no Python file**, only in prose),
and became chunks legitimately.

**The scoped-count rule caught a live error** (`PUSH_PROJECT_AUDIT.md:68-73`).
An initial count of eval behavior directories returned **10**; a `__pycache__`
directory was inflating it. Re-counting by the defensible measure —
directories containing `verify.py` — gave **9**, which shipped. The rule
(`ls <dir>/<glob> | wc -l`, not a repo-wide `find`) fired on a number already
written down. Post-merge, surface 7 of the test matrix confirmed **9** by a
wholly independent method: the repo's own `run_eval.py --list`
(`PUSH_PROJECT_AUDIT.md:236-239`).

**The gear flip is automatic** (`PUSH_PROJECT_AUDIT.md:130-133`). With
`docs/SPEC.md` merged, the same named invocation routed to gear 2 and took the
first unchecked `- [ ]` item with no ambiguity. The checklist genuinely
functions as the priority queue — the design's central bet, and it held.

**Invariant 4 held** (`PUSH_PROJECT_AUDIT.md:80-81`, `:253`). `record-review`
set only `notes` plus the automatic `last_reviewed`. No `lifecycle`, `active`,
`priority`, or `next_action` — the boundary `CLAUDE.md` rule 2 exists to
protect.

**Independent re-verification passed** (`PUSH_PROJECT_AUDIT.md:75-78`). From a
*fresh* clone, all four sampled claims reproduced.

---

## S1 — The `approved` warning names the wrong path

**Audit reference:** `PUSH_PROJECT_AUDIT.md:175-180` (finding 4). Tooling side:
[`FINDINGS_REGISTRY_MCP.md#M3`](./FINDINGS_REGISTRY_MCP.md).

**This is the only pure skill defect in the audit.**

**Observed and verified 2026-08-02.** `SKILL.md:184-187` warns at length:

> `approved`: `true` — REQUIRED. Without it the tool files a pending proposal,
> applies nothing, and still reports success; the review is silently lost.

That is accurate **for the MCP tool**. Step 4 does say "call
`record_project_review` on the registry MCP," so a careful reader can infer the
scope — but the warning is written as an unqualified property of recording a
review, and the CLI path (`registry record-review`, `cli.py:591`) has **no such
flag**. It applies directly.

**Failure mode.** An agent on the CLI path searches for a flag that does not
exist. Best case it wastes calls on `--help`; worst case it concludes the write
failed and retries or aborts a run that in fact succeeded. The skill's own
`CLAUDE.md` cheatsheet shows the CLI form as a first-class fallback for when the
MCP server is unavailable, so this path is expected, not exotic.

**Fix.** State which surface the warning governs, and give the CLI equivalent
inline. Two sentences.

---

## S2 — The evidence-brief read has no fallback

**Audit reference:** `PUSH_PROJECT_AUDIT.md:85-91` (finding 1). Producer:
[`FINDINGS_REPO_HYGIENE.md#H3`](./FINDINGS_REPO_HYGIENE.md). Ownership:
[`FINDINGS_REGISTRY_MCP.md#M2`](./FINDINGS_REGISTRY_MCP.md).

**Observed.** `SKILL.md:86` instructs an unconditional read:

> Evidence brief: read `$REGISTRY_ROOT/data/understanding/<id>.json`.

Verified: 95 such files exist; no code path in `src/` writes them. A newly
registered project will **always** lack one.

**The skill-side defect is narrow.** Even after the producer and ownership
questions are settled, an instruction to read a file that may not exist needs to
say what to do when it does not. The run proceeded on code alone — the right
call — but arrived there by improvisation, at some cost in context.

**Fix.** Mark the read conditional and state the fallback explicitly: absent
brief → proceed from README, `CLAUDE.md`/`AGENTS.md`, `docs/`, and `git log`,
which Step 2 already requires anyway.

---

## S3 — Test-command discovery is unguided (skill's share)

**Audit reference:** `PUSH_PROJECT_AUDIT.md:94-106` (finding 2). Primary owner:
[`FINDINGS_REPO_HYGIENE.md#H1`](./FINDINGS_REPO_HYGIENE.md).

**The bulk of this is not the skill's problem** — the repo should declare its
test command. But the skill has a residual share worth separating.

`SKILL.md` says "run the repo's test suite" and `Step 3` step 6 requires "run
the full test suite (no new failures)" without defining how to locate it or what
to do when the obvious invocation fails. In this repo bare `pytest` fails
collection, and an agent treating that as its red baseline reports a working
repo as broken — inside a PR description, in gear 2.

**Fix, once a marker file exists** (see H1): read the declared command. **Until
then:** state a search order (marker → `CLAUDE.md`/`AGENTS.md` → `Makefile` /
`pyproject.toml` / `package.json` → docs) and require that a *collection* error
be distinguished from a *test* failure before it is reported as a red baseline.
Those are different facts and only one of them is about the code.

---

## The load-bearing finding

**Audit reference:** `PUSH_PROJECT_AUDIT.md:147-165`, titled by the audit itself
"the finding that justified the whole exercise."

Gear 2's chunk was mechanical — manifest files and a docs block — so routing
policy sent it to Codex rather than inline. The adapter returned exit 0 and the
diff was correct on the first pass, requiring no fixes.

**But the delegation was only safe because the orchestrator had already
verified three facts that the task text did not contain**, each of which would
otherwise have produced a plausible-looking and wrong manifest:

1. **`watchdog` is a local module**, not the PyPI package — it lives at
   `scripts/drip_runner/watchdog.py`. A naive import-scan would have added a
   dependency that **shadows local code**.
2. **`reportlab` is a runtime dep** (PDF export only); the `scripts/` suite
   never imports it. Filing it as a test dep would have been wrong.
3. **The green baseline itself ran on a machine without `reportlab`
   installed** — which is *how* the runtime/test split was established at all.

None are derivable from the chunk's one-sentence text. They came from roughly
eight verification commands run **before** writing the task spec.

> A repo standard removes mechanical guessing. It does not remove the need to
> verify before delegating.

**What this means for the skill.** Gear 2's delegation step should state that
the orchestrator verifies the facts a worker will need *before* writing the task
spec, and that the verification is not optional when the chunk touches
dependencies, imports, or environment. Codex was capable here; the spec is what
made it safe.

### Corollary — verification must reason, not diff numbers

`PUSH_PROJECT_AUDIT.md:167-171`. After the chunk merged, `evals/` moved from
"31 passed, 1 skipped" to "32 passed". The skipped test required `reportlab`;
the manifest now installs it. A naive "same numbers or bust" check would have
flagged this as a regression. The skill's verification step should require
explaining a changed count, not matching it.

---

## S4 — Every failure path is untested

**Audit reference:** `PUSH_PROJECT_AUDIT.md:38-40`, `:134-137`, `:136-137`.

**This is the largest open item in the audit**, and it belongs to the skill
rather than to hygiene or the registry: these are the skill's own branches.

**The problem.** Both observed runs succeeded. `SKILL.md:212-222` defines a
failure-handling table with five distinct behaviors, and **not one of them
executed.** The audit therefore establishes that the skill handles the case
where nothing needs handling.

**Why that is weak evidence, specifically.** The asymmetry matters more than the
coverage gap. A broken *success* path is visible — no PR, or an obviously bad
one. A broken *failure* path is invisible: it produces a PR that looks exactly
like a good one. Tests pass, chunk ticked, description reads fine. There is no
signal not to merge it.

This is sharper because the skill runs **unattended** on a weekday schedule
(`docs/ROUTINE_SETUP.md`). The question these paths answer is not "does it
work" — that is settled — but "when something is wrong at 6am, does it stop
cleanly or does it act and look fine doing it."

### The five paths, and what each one risks

| # | Path | `SKILL.md` | Why it did not fire | Risk if the instruction is not honored |
|---|---|---|---|---|
| 1 | Codex adapter exit 4 → abort, no silent fallback | `:168`, `:218` | Codex was logged in, exit 0 | Falls back to inline implementation without saying so; routing policy bypassed invisibly |
| 2 | Red baseline → flag in PR body, do not fix | `:159`, `:219` | Baseline was green (154 passed) | "Helpfully" repairs a pre-existing failure; unrelated fixes land mixed into the chunk |
| 3 | Chunk already done / obsolete → spec-amendment PR | `:154-156`, `:221` | Chunk was genuinely undone (0 manifests) | Rebuilds existing work; duplicate or conflicting implementation |
| 4 | Open `push/*` PR → skip the project | `:61`, `:75-77` | Sandbox had no prior PRs | Brake does not fire; unreviewed PRs stack on one repo, each built on the last's assumptions |
| 5 | Verification fails twice → delete branch, outcome "aborted" | `:171-174`, `:220` | Codex was correct first pass | A second bad attempt ships instead of aborting |

**Path 4 is a special case** and the only one that cannot be tested on the
sandbox: the brake lives in Step 1 selection, which the sandbox design
deliberately puts out of reach. The target was registered inert (`incubating`,
`active: false`, no `next_action`) precisely so no selection path could reach
it. Testing the brake requires a bare invocation against the real focus list.

Post-run invariant checks did pass (`PUSH_PROJECT_AUDIT.md:248-255`) — focus-repo
PR counts unchanged (1, 1, 1, 0), zero `push/` PRs on `kmadhok/project-registry`,
two `apply_proposal` audit entries. Those bound the **blast radius**. They do not
test any of the five behaviors above.

### How to test them

Each test is a **run under a changed condition**, not a file. Setup, invoke,
observe, restore. All but #4 use the sandbox
(`kmadhok/push-sandbox-interview-prep`), which exists for exactly this.

**T1 — Codex logout.**
Move `~/.codex/auth.json` aside. Run `/push-project push-sandbox-interview-prep`.
Restore afterwards.
*Pass:* aborts, Step 4 records outcome "aborted", notifies naming the adapter.
*Fail:* implements inline without mentioning the adapter.

**T2 — Red baseline.**
Break one assertion under `scripts/` on the sandbox's `main`. Run. Read the PR
body.
*Pass:* the failure is flagged in the PR body and left untouched; the diff
contains only chunk work.
*Fail:* the diff repairs it.

**T3 — Already-done chunk.**
Implement the next unchecked chunk by hand, push to `main`, leave its `- [ ]`
unchecked. Run.
*Pass:* opens `push/spec-amend` ticking the box, reason in the body, no code.
*Fail:* rebuilds the work.

**T4 — Open-PR brake.** *(needs owner approval — touches a real repo)*
Open a throwaway `push/brake-test` PR on one focus repo. Run **bare**
`/push-project`. Close the PR afterwards.
*Pass:* that project is skipped; another candidate is selected.
*Fail:* it selects the parked project anyway.

**T5 — Two-strike abort.** Hardest to stage honestly, since it requires
verification to fail twice on real work. Lowest priority; deprioritize until
T1–T4 are done.

**Priority: T1 and T4 first.** Those two decide whether an unattended run
degrades gracefully. T1 governs whether delegation policy holds when the worker
is unavailable; T4 governs whether runs stack up while the owner is behind on
reviews.

### Where results go, and when `SKILL.md` changes

Results are observations — they belong in
[`PUSH_PROJECT_AUDIT.md`](./PUSH_PROJECT_AUDIT.md) as a second run section,
alongside the gear-1 and gear-2 records.

**`SKILL.md` changes only when a test fails.** It is instructions to the skill,
not a test suite. A passing test confirms the instruction already works and
warrants no edit. A failing test means an instruction is wrong, missing, or too
weak to bind — that is when it gets rewritten.

S1 and S2 above are already-justified `SKILL.md` edits, independent of these
tests.

**Limitation worth stating.** These are five manual runs. Nothing here is
automated, and re-running them after a future `SKILL.md` change means
reconstructing the setup by hand. A harness that stages each condition, invokes
the skill, and asserts on the resulting PR is a separate build — worth it only
if `SKILL.md` changes often enough to need regression coverage.

---

## Summary

| ID | Finding | Audit ref | Type |
|---|---|---|---|
| S1 | `approved` warning does not name the MCP path | `:175-180` | Defect — only pure skill bug |
| S2 | Evidence-brief read has no stated fallback | `:85-91` | Gap |
| S3 | Test-command discovery unguided; collection ≠ failure | `:94-106` | Gap — mostly H1's |
| **S4** | **All five failure paths untested; both runs were happy-path** | `:38-40`, `:134-137` | **Coverage gap — largest open item** |
| — | Delegation safety comes from orchestrator verification | `:147-165` | Design lesson to encode |

**Next action:** run T1 (Codex logout) and T4 (open-PR brake) per S4. T4 needs
owner approval — it puts a throwaway PR on a real focus repo.
