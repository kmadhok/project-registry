# Findings — registry and MCP layer

Findings from the `push-project` behavioral audit that belong to **this
codebase** — the registry data model, its storage layer, and the MCP/CLI
surfaces. These are defects and gaps in the tooling itself, independent of any
target repo and independent of the skill that happened to expose them.

**Source:** [`PUSH_PROJECT_AUDIT.md`](./PUSH_PROJECT_AUDIT.md), 2026-08-02.
Registry commit at audit write-up: `16dc531`.

> **Scope correction — the audit run made zero MCP calls.** Verified against the
> session transcript: every registry interaction went through `cli.py`, not the
> MCP server. The audit therefore observed **no MCP behavior at all**, and the
> findings below rest on reading the code, not on watching it run.
>
> This is why M3 surfaced: the CLI path is the one that was exercised, and its
> missing `approved` flag is what the run tripped over. The MCP path's silent
> pending-proposal behavior remains **unobserved**, which makes M3's fix more
> urgent rather than less.
>
> A subagent-driven observation
> ([`../docs/observations/`](./observations/)) will likely also run via the CLI —
> the server is registered with a relative root, so it does not resolve from an
> arbitrary cwd. Closing this gap needs a run started from the registry root.

---

## M1 — Round-tripping a project drops falsy fields

**Audit reference:** `PUSH_PROJECT_AUDIT.md:182-186` (finding 5).

**Observed.** `is_fork` was present in the sandbox YAML before a review and
absent after `record-review` rewrote the file.

**Verified 2026-08-02, and the audit's description needs one correction.** The
audit states `is_fork: true` was written and dropped. The YAML at `a02a51e`
actually contained `is_fork: false` (`git show a02a51e:registry/projects/push-sandbox-interview-prep.yaml`),
and it is absent from `a60918f` onward. The value matters because it changes
where the bug lives:

- `model.py:459-460` — `to_yaml_dict` emits the key **only when truthy**:
  ```python
  if self.is_fork:
      out["is_fork"] = True
  ```
- `model.py:406` — `parse` reads it with `bool(raw.get("is_fork", False))`.

So the loss is on **serialization**, not parsing, and it strikes fields written
at a **falsy** value, not at their default in general. `is_fork: true` would in
fact survive.

**Impact.** Any curated field explicitly set to a falsy value is silently
deleted by the next automated write. The field's *meaning* survives — a reader
re-parses it to `False` either way — so nothing breaks functionally. What is
lost is the distinction between **"deliberately set to false"** and **"never
considered."** For a registry whose entire premise is that curated intent is
authoritative and separable from observed evidence (`PURPOSE.md`), erasing the
record of a deliberate choice is the wrong default.

The audit calls it "harmless here" and it was. It will not stay harmless for a
field where the false case is the meaningful one.

**Scope.** Not unique to `is_fork`. The same `if self.<field>:` pattern governs
`purpose`, `desired_outcome`, `category`, `priority`, `effort`, `horizon`,
`blocked_by`, `repo`, `last_reviewed`, `next_action`, `accomplishments`,
`relationships`, and `tags` (`model.py:439-466`). Empty strings and empty lists
round-trip away identically.

**Fix, in preference order.**

1. Track whether a key was present in the source and re-emit on that basis
   (mirrors Pydantic's `exclude_unset`). Preserves author intent exactly.
2. Or: for booleans specifically, always emit. Cheap, narrow, fixes the
   observed case; leaves empty strings and lists unaddressed.

Option 1 is the correct shape given the curated/observed split this repo is
built around.

---

## M2 — Evidence briefs have no owner in the data model

**Audit reference:** `PUSH_PROJECT_AUDIT.md:85-91` (finding 1). This is the
registry half; see also
[`FINDINGS_REPO_HYGIENE.md#H3`](./FINDINGS_REPO_HYGIENE.md) (who produces the
content) and
[`FINDINGS_PUSH_PROJECT_SKILL.md#S2`](./FINDINGS_PUSH_PROJECT_SKILL.md) (how the
skill should behave when one is missing).

**Observed and verified 2026-08-02.** `data/understanding/` holds 95 JSON
briefs. `grep -rn "understanding" src/` matches only a docstring in
`__init__.py` and packaging metadata — **no code reads or writes this
directory.** The files arrived from an out-of-band Codex analysis run.

**Why this is a registry problem.** `CLAUDE.md` and `PURPOSE.md` define exactly
two data classes with two owners: `registry/` is curated human intent,
git-tracked and authoritative; `data/` is observed evidence written *only by*
`registry sync` and gitignored. `data/understanding/` sits in the `data/` tree
and satisfies neither rule — sync does not write it, and nothing else does
either. It is a third, undocumented category living in a directory whose stated
contract it violates.

Consequences that follow directly:

- **A new project never has a brief.** Import a repo today, and any consumer
  reading `data/understanding/<id>.json` finds nothing. This is guaranteed, not
  occasional.
- **Staleness is invisible.** `registry sync-status` reports when GitHub
  evidence was refreshed. There is no equivalent for briefs, so a brief written
  against a long-since-restructured repo reads exactly like a fresh one.
- **Deletion is unmanaged.** No code removes a brief when its project is
  removed.

**Fix.** Decide the category and make it explicit, either way:

- **Treat as observed evidence.** Give briefs a generator behind a registry
  command, cover them in `sync-status`, document them in `SCHEMA.md`. The
  generator can delegate the actual analysis to Codex — this is only about
  ownership of *where output lands and when it is refreshed.*
- **Or treat as an external artifact.** Move them out of `data/`, document the
  path as a convention consumers may check, and stop implying registry
  ownership by location.

Either resolves the contradiction. Leaving them in `data/` unowned does not.

---

## M3 — MCP and CLI diverge on review approval, undocumented

**Audit reference:** `PUSH_PROJECT_AUDIT.md:175-180` (finding 4). The skill-side
consequence is [`FINDINGS_PUSH_PROJECT_SKILL.md#S1`](./FINDINGS_PUSH_PROJECT_SKILL.md);
this entry covers the divergence itself.

**Observed and verified 2026-08-02.**

- **MCP** `record_project_review` takes an `approved` argument. Without it the
  tool files a pending proposal, applies nothing, **and still reports success**
  — per `SKILL.md:184-187`, the review is silently lost.
- **CLI** `registry record-review` (`cli.py:591`, `cli.py:743`) has **no such
  flag**. It applies directly.

**Why this is a tooling finding, not just a docs bug.** Two entry points to one
operation with different default safety semantics is a design decision that was
never written down. `CLAUDE.md` states CLI and MCP call the same query functions
and instructs keeping it that way — for this write path, they do not behave the
same.

The MCP behavior is the more dangerous of the two on its own terms: **silent
failure that reports success** is the one outcome an automated caller cannot
detect. An agent that omits the flag gets a success message and no review.

**Fix, in preference order.**

1. Make the MCP tool refuse rather than silently no-op — return an explicit
   "pending proposal filed, nothing applied" result the caller cannot mistake
   for success. Removes the trap regardless of documentation.
2. Document the divergence in `SCHEMA.md` and `SETUP.md`, and have the skill
   name which path its warning governs.

(1) is worth doing even if (2) happens, because the next consumer will not have
read the audit.

---

## Summary

| ID | Finding | Audit ref | Verified against |
|---|---|---|---|
| M1 | Falsy curated fields dropped on write | `:182-186` | `model.py:439-466`, git history of the sandbox YAML |
| M2 | `data/understanding/` has no producer or owner | `:85-91` | `grep -rn "understanding" src/`, 95 files present |
| M3 | MCP requires `approved`; CLI has no such flag | `:175-180` | `cli.py:591,743`, `SKILL.md:184-187` |

**Correction to the audit carried here:** M1's dropped value was `is_fork:
false`, not `true`, and the mechanism is truthy-only serialization rather than
default-value omission. The finding stands; its stated cause did not.
