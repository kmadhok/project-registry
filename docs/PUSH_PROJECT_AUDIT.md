# push-project behavioral audit

What the skill actually does, observed against a disposable target rather than
inferred from `SKILL.md`.

## Provenance — for referencing this run from another session or model

| Item | Value |
|---|---|
| Date | 2026-08-02 |
| Claude Code session id | `f26eed63-43dc-4d98-afcf-ef80d0f219b1` |
| Transcript | `~/.claude/projects/-Users-kanumadhok-Documents-Claude-Projects-project-registry/f26eed63-43dc-4d98-afcf-ef80d0f219b1.jsonl` |
| Model | Opus 5 (1M context), `claude-opus-5[1m]` |
| Codex delegation report | `~/.claude/model-reports/codex-push-sandbox-interview-prep-20260802-112521-58789.md` |
| Registry commit at write-up | `16dc531ee7df796472179bd1cc3bd2c250d8e2de` |
| Sandbox repo | `kmadhok/push-sandbox-interview-prep` (private) |
| Sandbox source | `kmadhok/interview-prep-prod` @ `b76d952` |
| Sandbox `main` after both runs | `70e1fc04c891493077b8a289606718b7f10072b4` |
| PRs | [#1 spec](https://github.com/kmadhok/push-sandbox-interview-prep/pull/1) (merged `2f585f0`), [#2 chunk](https://github.com/kmadhok/push-sandbox-interview-prep/pull/2) (merged `70e1fc0`) |

Note: this session resumed after a `/clear`, so earlier session ids exist in the
same project directory. `f26eed63…` is the one that performed every action
described here.

**Why this exists.** Merging a push-project PR is the approval signal the whole
design runs on ("Merging this PR approves the spec"), and `Step 4` write-backs
land in an append-only audit log that a repo-side `git revert` does not unwind.
So observing the skill with auto-merge required a repo where merging means
nothing.

**Setup.** `kmadhok/push-sandbox-interview-prep`, a mirror-push copy of the
live `kmadhok/interview-prep-prod` at commit `b76d952`. Registered deliberately
inert — `incubating`, `active: false`, no `next_action` — so two independent
mechanisms keep it off the focus list: `list_projects(now/next)` never returns
it, and `get_attention_queue` omits projects with no next action. Reachable
only by named invocation.

**Not observed, by design:** `Step 1` selection — the attention-queue ranking,
the never-touched ordering, and the open-PR brake. That is the deliberate price
of guaranteeing no real repo could be touched.

---

## Run 1 — Gear 1 (spec drafting)

PR: `kmadhok/push-sandbox-interview-prep#1` — merged.

### What worked

**Gear detection was correct and non-obvious.** The repo contains
`docs/spec/productionalization-v1.md` and `docs/intent/productionalization.md`,
but no `docs/SPEC.md`. The gear check keys on the exact path, so it correctly
routed to gear 1 despite substantial existing spec material. A looser check
("does a spec exist?") would have mis-routed.

**The capability-absence rule did real work.** `SKILL.md` requires proving a
capability is missing before writing a chunk that builds it. Applied here, it
prevented at least two bad chunks:

- The repo already has a complete eval harness — 9 behavior directories each
  with `contract.md` *and* `verify.py`, plus `trace_step.py` and
  `render_run_report.py`. A chunk saying "build an eval harness" would have
  been rejected on sight.
- `infra/` and `/onboard` were confirmed genuinely absent — `verify_setup`
  appears in **no Python file**, only in spec and plan prose. Those became
  chunks legitimately.

**The scoped-count rule caught a real error mid-run.** An initial count of eval
behavior directories returned **10**; a `__pycache__` directory was inflating
it. Re-counting by the defensible measure — directories containing `verify.py`
— gave **9**, which is what shipped. This is exactly the failure the rule
("`ls <dir>/<glob> | wc -l`, not a repo-wide `find`") exists to prevent, and it
fired on a real number that was already written down.

**Independent re-verification passed.** From a *fresh* clone, all four sampled
claims reproduced: 9 verifiers, 18 skills, `infra/` absent, 0 dependency
manifests. The two test claims reproduced exactly — `pytest scripts/` → 154
passed, bare `pytest` → collection error.

**Invariant 4 held.** `record-review` set only `notes` (plus the automatic
`last_reviewed`). No `lifecycle`, `active`, `priority`, or `next_action`.

### Findings — gaps in the skill, not the repo

**1. The evidence brief does not exist for new projects, and nothing generates
one.** `SKILL.md:86` instructs an unconditional read of
`data/understanding/<id>.json`. No code path in `src/` writes these files — the
95 existing ones came from an out-of-band analysis run. A newly registered
project will *always* lack one. The instruction has no stated fallback; the run
proceeded on code alone, at some cost in context. Either the skill should mark
the read optional, or brief generation needs an owner.

**2. Test-command discovery is unguided and the obvious guess is wrong.** The
skill says "run the repo's test suite" without saying how to find it. The
obvious invocation — bare `pytest` at the repo root — **fails collection** here
(`ModuleNotFoundError: No module named 'common'`). The working commands are
`pytest scripts/` (154 passed) and `PYTHONPATH=evals pytest evals/` (31 passed,
1 skipped), and the first is documented only inside a *Commands* block in
`docs/spec/productionalization-v1.md` — a file the skill reads by luck, not by
instruction.

An agent that treated the bare-`pytest` failure as a red baseline would have
reported a broken repo. This is the single strongest argument for the
machine-readable project marker: one declared test command removes the guess
entirely.

**3. No interpreter on the machine could run the target's tests.** System
`python3` is 3.14 with no `pytest`; 3.11 and `/usr/bin/python3` likewise. The
baseline required borrowing *another project's* virtualenv. The skill has no
guidance for this, and a cloud routine would hit it harder. Worth noting the
skill still produced a correct green baseline — but only because the operator
went looking.

### Cost note

Gear 1 took ~20 tool calls, most spent on discovery that a repo standard would
make unnecessary: finding the test command, locating docs, proving capability
absence. The spec-writing itself was a small fraction.

---

## Run 2 — Gear 2 (shipping a chunk)

PR: `kmadhok/push-sandbox-interview-prep#2` — merged. Chunk: pin the
environment.

### What worked

**The gear flip was automatic.** With `docs/SPEC.md` merged to `main`, the same
named invocation routed to gear 2 and took the first unchecked `- [ ]` item
with no ambiguity. The checklist genuinely functions as the priority queue.

**Codex delegation succeeded (adapter exit 0).** The chunk was mechanical —
manifest files and a docs block — so routing policy sent it to Codex rather
than inline. The returned diff was correct on the first pass and required no
fixes. Exit 4 (not logged in) never fired, so that abort path remains
unobserved.

**The chunk was ticked in the same diff as the code**, as `SKILL.md:177`
requires. Verified on `main` after merge: exactly 1 checked chunk.

**Verification caught what mattered and was cheap.** The acceptance criterion —
"runs green from a clean checkout with no borrowed virtualenv" — was tested
literally, by building a throwaway 3.11 venv from only the new manifest.
`scripts/` returned 154 passed, matching baseline.

### The finding that justified the whole exercise

**A precise task spec is what made delegation safe, and the precision came from
facts the *orchestrator* verified — not from the worker.** Three traps were
identified before delegating, and each would have produced a plausible-looking
but wrong manifest:

1. `watchdog` is imported in this repo but is a **local module**
   (`scripts/drip_runner/watchdog.py`), not the PyPI package. A naive
   import-scan would have added a dependency that shadows local code.
2. `reportlab` is a **runtime** dep (PDF export only); the `scripts/` suite
   never imports it. Filing it as a test dep would have been wrong.
3. The green baseline itself ran on a machine where `reportlab` was **not
   installed** — which is how the runtime/test split was established at all.

None of these are discoverable from the chunk's one-sentence text. They came
from ~8 verification commands run before writing the spec. This is the concrete
argument for the standard: the marker file would have carried the test command,
but *this* class of fact still requires the orchestrator to look.

**A side effect worth noting because it looks like a regression and isn't:**
`evals/` moved from "31 passed, 1 skipped" to "32 passed". The skipped test
required `reportlab`; the manifest now installs it. Verification has to
distinguish a skip becoming a pass from a masked failure — a naive
"same numbers or bust" check would have flagged this wrongly.

### Findings — gaps confirmed

**4. `record-review` via the CLI has no `approved` flag.** `SKILL.md:184-187`
warns at length that `approved: true` is REQUIRED or the review is silently
lost. That applies to the **MCP** tool. The CLI path (`registry record-review`)
applies directly with no such flag. An agent following the skill literally on
the CLI path will look for a flag that does not exist. The skill should say
which path the warning governs.

**5. Round-tripping drops schema defaults.** `is_fork: true` was written into
the sandbox YAML, then silently removed when `record-review` rewrote the file
(it serializes out at its default). Harmless here, but it means curated fields
equal to their default cannot survive an automated review write.

---

## What this says about the repo standard

Ranked by how much each would have saved, based on what was actually spent:

1. **A declared test command.** The single largest cost in both runs, and the
   one with a wrong obvious answer (bare `pytest` fails here). Highest value by
   a wide margin.
2. **A declared interpreter.** No system interpreter could run this repo's
   tests; the first run needed a borrowed venv to get any baseline at all.
3. **A registry-id marker in the repo.** Would let a run confirm it is in the
   repo it believes it is in, which currently rests on the `repo` field alone.

Design docs and a spec location matter less than expected — the skill found
`docs/spec/` and `docs/intent/` unaided and correctly treated them as authority
rather than overwriting them.

**What a standard would NOT have fixed:** the three dependency traps above.
Those needed real inspection of the code. A standard removes mechanical
guessing; it does not remove the need to verify before delegating.

---

## Full test matrix — 2026-08-02, post-merge

Every runnable surface, executed against a **fresh clone of sandbox `main`
(`70e1fc0`)** using a throwaway Python 3.11.15 venv built from the merged
`requirements-dev.txt` alone — no borrowed environment. This is the claim the
shipped chunk makes, tested literally.

| # | Surface | Command | Result |
|---|---|---|---|
| 1 | project-registry suite | `.venv/bin/python -m pytest` | **213 passed** |
| 2 | project-registry rules | `registry validate` | **0 errors**, 3 suggestions, exit 0 |
| 3 | sandbox helpers | `python -m pytest scripts/` | **154 passed** |
| 4 | sandbox evals | `PYTHONPATH=evals python -m pytest evals/` | **32 passed** |
| 5 | sandbox root (bare) | `python -m pytest` | **collection error** — expected, see below |
| 6 | drip_runner subtree | `python -m pytest scripts/drip_runner/` | **92 passed** |
| 7 | eval harness listing | `python evals/run_eval.py --list` | **9 skills** |
| 8 | eval harness tests | `python -m pytest evals/test_run_eval.py` | **26 passed** |
| 9 | trace verifier tests | `pytest evals/test_verify_behavior_traces.py` | **6 passed** |
| 10 | personal-refs guard (SC6) | `pytest scripts/test_no_personal_refs.py` | **1 passed** |

**Surface 5 fails by design.** `ModuleNotFoundError: No module named 'common'`
is the known defect the *second* spec chunk exists to fix. It is recorded here
as a passing observation — the repo behaves exactly as `docs/SPEC.md` says it
does — not as a regression.

**Surface 7 independently confirms a spec number.** The repo's own tooling
reports 9 eval'd skills, matching the `9` written into *Current state* by a
different method (`ls`-based count of directories containing `verify.py`). Two
independent measures agreeing is what makes that number safe to cite.

**One methodology correction worth recording.** An attempt to run
`run_eval.py classify --workspace .` errored with "expected exactly one fixture
role … found 0". That was a wrong entry point, not a defect: the harness expects
a synthetic workspace, which `evals/test_run_eval.py` constructs itself
(surface 8). Real verifier coverage comes from surfaces 4/8/9, not from manual
`run_eval.py` invocation against a repo root.

### Post-run invariant checks

| Invariant | Check | Result |
|---|---|---|
| 2 — no repo outside the named project | `push/` PR counts on the 4 focus repos vs. pre-run baseline | **1, 1, 1, 0 — unchanged** |
| 4 — registry writes are `notes` only | `git diff` on the sandbox YAML | only `notes` + `last_reviewed` |
| 6 — never target `project-registry` | `push/` PRs on `kmadhok/project-registry` | **0** |
| — | audit log | 2 `apply_proposal` entries, gear 1 and gear 2 |
