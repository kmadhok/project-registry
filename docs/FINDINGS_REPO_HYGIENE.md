# Findings — repository hygiene

Findings from the `push-project` behavioral audit that are **not** defects in
the skill or the registry. They are missing conventions in the *target* repos.
Any agent — push-project, Codex, a future routine, a human returning after six
months — pays the same cost until the repo declares these facts itself.

**Source:** [`PUSH_PROJECT_AUDIT.md`](./PUSH_PROJECT_AUDIT.md), runs 1 and 2
against `kmadhok/push-sandbox-interview-prep` (a mirror of
`kmadhok/interview-prep-prod` @ `b76d952`), 2026-08-02.

The audit's own ranking of what a standard would have saved is at
`PUSH_PROJECT_AUDIT.md:189-207`, ordered by cost actually observed rather than
by guess.

---

## H1 — No declared test command, and the obvious guess is wrong

**Audit reference:** `PUSH_PROJECT_AUDIT.md:94-106` (finding 2), reconfirmed as
surface 5 of the test matrix at `PUSH_PROJECT_AUDIT.md:224`.

**Observed.** The skill instructs "run the repo's test suite" without saying how
to locate it. The obvious invocation — bare `pytest` at the repo root — **fails
collection** in this repo:

```
ModuleNotFoundError: No module named 'common'
```

The commands that actually work:

| Command | Result |
|---|---|
| `pytest scripts/` | 154 passed |
| `PYTHONPATH=evals pytest evals/` | 31 passed, 1 skipped (later 32 passed) |
| `pytest` (bare, repo root) | collection error |

The first is documented **only inside a *Commands* block** in
`docs/spec/productionalization-v1.md` — a file the run read by luck, not because
any instruction pointed at it.

**Why this is hygiene, not a skill bug.** The failure mode is not "push-project
guessed badly." It is that the repo does not state a fact only the repo knows.
Every consumer re-derives it, and the cheap derivation is wrong.

**Severity.** The audit calls this the single largest cost across both runs and
the highest-value item by a wide margin (`PUSH_PROJECT_AUDIT.md:193-195`). The
danger is not slowness: an agent treating the bare-`pytest` failure as its red
baseline would have **reported a working repo as broken**, and in gear 2 that
propagates into a PR description.

**Fix.** Declare the test command in a machine-readable marker committed to the
repo. One line removes the guess entirely.

---

## H2 — No declared interpreter

**Audit reference:** `PUSH_PROJECT_AUDIT.md:107-112` (finding 3).

**Observed.** No interpreter on the machine could run the target's tests:

- system `python3` → 3.14, no `pytest`
- `python3.11` → no `pytest`
- `/usr/bin/python3` → no `pytest`

Establishing *any* green baseline required borrowing **another project's
virtualenv**. The skill offers no guidance for this, and the audit notes a cloud
routine would hit it harder — there is no neighbouring venv to borrow.

**Status — partly fixed for the sandbox.** Run 2's shipped chunk was precisely
this: pin the environment via `requirements-dev.txt`
(`PUSH_PROJECT_AUDIT.md:122-146`). The post-merge matrix then rebuilt a
throwaway Python 3.11.15 venv **from the manifest alone, no borrowed
environment**, and reproduced 154 passed on `scripts/`
(`PUSH_PROJECT_AUDIT.md:211-229`). The pattern is proven; it is unapplied
everywhere else.

**Fix.** Every repo declares its interpreter version and a dependency manifest
sufficient to run its own tests from a clean checkout.

---

## H3 — Evidence briefs have no producer (hygiene half)

**Audit reference:** `PUSH_PROJECT_AUDIT.md:85-91` (finding 1). This finding
splits three ways; the other two halves are tracked as
[`FINDINGS_REGISTRY_MCP.md#M2`](./FINDINGS_REGISTRY_MCP.md) and
[`FINDINGS_PUSH_PROJECT_SKILL.md#S2`](./FINDINGS_PUSH_PROJECT_SKILL.md).

**Observed.** `SKILL.md:86` instructs an unconditional read of
`data/understanding/<id>.json`. **Verified 2026-08-02:** 95 such files exist;
`grep -rn "understanding" src/` returns only a docstring in `__init__.py` and
packaging metadata. **No code path writes them.** They came from an out-of-band
Codex analysis pass.

**The hygiene half.** The content is per-repo knowledge — what the project is,
how it is laid out, what it depends on. Producing it is a repo-facing job, and
the owner already has the mechanism: the prior Codex pass that generated the 95.

**Fix.** Turn that one-shot pass into a re-runnable skill on a chosen cadence,
so a newly registered project acquires a brief without manual intervention.
Where the output lands, and how staleness is detected, is a registry concern
(M2), not a hygiene one.

---

## Proposed marker file

The audit's ranked recommendation (`PUSH_PROJECT_AUDIT.md:191-199`), collapsed
into one artifact per repo. Names are a starting point, not a settled schema.
The adopted schema-v1 contract and validation command are documented in [SETUP.md](SETUP.md#6-declare-repository-metadata).

```yaml
# .project-meta.yaml — committed to each target repo
registry_id: push-sandbox-interview-prep   # H4: confirm identity
interpreter: "3.11"                        # H2
test:
  - "pytest scripts/"                      # H1: declared, not guessed
  - "PYTHONPATH=evals pytest evals/"
```

**H4 — registry-id marker.** Ranked third by the audit
(`PUSH_PROJECT_AUDIT.md:198-199`). A run's belief about which repo it is in
currently rests on the registry's `repo` field alone, with nothing on the repo
side to confirm it. Cheap to add, and it makes invariants 2 and 6 checkable from
inside the checkout rather than only after the fact.

---

## What a standard would *not* have fixed

Recorded because it bounds the claim (`PUSH_PROJECT_AUDIT.md:205-207`).

The three dependency traps in run 2 — `watchdog` being a local module rather
than the PyPI package, `reportlab` being runtime-only, and the baseline having
run without `reportlab` installed — were **not** derivable from any marker file.
They took roughly eight verification commands against the actual code.

> A standard removes mechanical guessing; it does not remove the need to verify
> before delegating.

Full detail: [`FINDINGS_PUSH_PROJECT_SKILL.md#the-load-bearing-finding`](./FINDINGS_PUSH_PROJECT_SKILL.md).

---

## Summary

| ID | Finding | Audit ref | Status |
|---|---|---|---|
| H1 | No declared test command; bare `pytest` fails | `:94-106`, `:224` | Open — highest value |
| H2 | No declared interpreter | `:107-112` | Pattern proven in sandbox, unapplied elsewhere |
| H3 | Evidence briefs have no producer | `:85-91` | Open — needs a scheduled skill |
| H4 | No registry-id marker in repo | `:198-199` | Open — lowest cost |
