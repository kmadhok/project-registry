# push-project harness: enforcing the loop's rules in code

**Date:** 2026-08-01
**Status:** Approved design, deliberately not yet implemented — see *When to build*
**Owner:** Kanu Madhok
**Supersedes nothing.** The skill (`docs/superpowers/specs/2026-08-01-push-project-skill-design.md`)
remains the definition of *what* a run does. This spec covers *what enforces it*.

## Problem

The skill works: two spec PRs and one implementation PR, all reviewed and
merged or open. But every rule in it is prose. A run that skims the skill
still looks like a successful run — it will report a green status, and the
only way to catch a violation is to read the diff.

That was acceptable while a human watched every run. Under a schedule, nobody
watches. The failure mode is not dramatic: it is a PR on the wrong branch, a
second PR in one run, or a registry write that sets a field the owner reserved
for themselves. Each is individually small and individually invisible.

## What code can and cannot fix

Durability has three independent failure modes, and only one of them is a
programming problem:

| Failure | Fixed by | Notes |
|---|---|---|
| The run never fires | Any scheduler | Routines already solve this |
| The run breaks its own rules | **Code** | This spec |
| The run does poor work | Nothing structural | PR review is the permanent control |

Naming the third one honestly matters: no harness makes spec quality or
implementation quality deterministic. The harness makes *violations*
impossible, not *bad judgment* impossible.

## Architecture: a deterministic shell around a model core

The harness is a Python program that owns control flow and calls the model
only where judgment is genuinely required.

```
pick_project()          code    registry query, focus filter, ranking
brake()                 code    skip projects with an open push/ PR
gear_check()            code    does docs/SPEC.md exist
draft_spec()            MODEL   writing an honest spec
first_unchecked()       code    parse the checklist
implement_chunk()       MODEL   writing the code
verify()                code    run tests, compare to baseline
open_pr()               code    branch naming, single-PR rule
write_back()            code    exact record_review call shape
```

Every rule that was actually violated during development becomes code:

- The write-back that silently no-opped (missing `approved: true`) becomes one
  function with a unit test, not a sentence in a skill file.
- Branch naming (`push/…`) becomes the only branch the harness will create.
- "At most one PR per run" becomes a counter the model cannot increment.
- "Never write lifecycle/active/priority/next_action" becomes a rejected call,
  not a request the model may reinterpret.

## Enforcement mechanism

The Agent SDK provides the primitive this needs: **`can_use_tool`**, an async
callback consulted before any tool call, which can return
`PermissionResultDeny`. The harness uses it as a hard boundary:

```python
async def gate(tool_name, input_data, context):
    if tool_name == "Bash":
        cmd = input_data.get("command", "")
        if is_git_push(cmd) and not pushes_to_push_branch(cmd):
            return PermissionResultDeny(
                message="Only push/ branches may be pushed.", interrupt=True)
        if is_force_push(cmd):
            return PermissionResultDeny(message="Force-push is never allowed.",
                                        interrupt=True)
        if opens_pr(cmd) and run_state.prs_opened >= 1:
            return PermissionResultDeny(
                message="One PR per run; this run already opened one.",
                interrupt=True)
    return PermissionResultAllow(updated_input=input_data)
```

The model can still *attempt* anything; it cannot *complete* a forbidden
action. That is the difference between an instruction and an invariant.

Supporting SDK features the harness uses:

- `permission_mode="dontAsk"` with an explicit `allowed_tools` list — nothing
  is approved that was not listed, and there is no human to prompt.
- `cwd` per phase, so the model works in the checkout it should.
- `output_format` (JSON schema) for the phases where the harness needs a
  decision back, not prose — e.g. gear-2's "is this chunk already obsolete?".
- `mcp_servers` pointing at the registry's own `.mcp.json`, so context loading
  is unchanged.
- `system_prompt` carrying the skill text, so the *guidance* stays in one
  place; the harness enforces only what must be enforced.

## What stays in the model's hands

Deliberately not automated, because encoding them would produce worse output:

- **Writing the spec.** Reading a repo and deciding what "done" means is the
  judgment the whole system exists to apply.
- **Implementing a chunk.** Same.
- **Deciding a chunk is obsolete.** The harness asks; the model answers.

## Scope

**In scope:** selection, brake, gear check, branch/PR discipline, single-PR
rule, registry write-back shape and field allow-list, test-baseline
comparison, run logging.

**Out of scope:** replacing the skill file (it becomes the system prompt);
choosing where the harness runs (see below); anything about spec or code
quality; any GitHub mutation beyond branch push and PR open.

## Deployment is a one-line difference

The harness is the same program regardless of trigger:

- **Routine (API trigger):** POST to the routine's `/fire` endpoint.
- **Local launchd / PC systemd:** `python -m push_harness` on a timer.
- **GitHub Actions:** scheduled workflow.

Because this choice is trivially reversible, it does not need deciding now.
Note the harness needs the registry, which today lives only on the Mac; the
Routine path solves that by cloning the repo, and a local path has it already.

## Testing

The point of the harness is that its rules are testable. Requirement-driven,
not implementation-driven:

- *Selection:* focus list empty → no run; all blocked → no run, reports
  waiting; `project-registry` never selected; ranking is deterministic given
  fixed inputs.
- *Brake:* an open `push/` PR blocks; a non-`push/` PR does not (regression —
  interview-prep's PR #7 must not block).
- *Gear:* missing spec → gear 1; present → gear 2; empty repo → gear 1.
- *Enforcement (the important ones):* pushing a non-`push/` branch is denied;
  force-push denied; a second PR in one run denied; a write-back with a field
  other than `notes` denied; a write-back without `approved: true` fails
  loudly rather than silently no-opping.
- *Verification:* red baseline is reported, not silently fixed; new failures
  abort the run.

Each enforcement test asserts a *denial*, which is what distinguishes this
from the current prose rules.

## When to build

**Not yet — after 3–5 unattended Routine runs.** The two defects found so far
were mine, caught in review, not drift by an unattended run. Building now
means guessing which rules need enforcement; building after real runs means
knowing. Those transcripts are the requirements document for this harness,
and they will likely make it smaller than it looks here.

Trigger to start: the first unattended run that breaks a rule, or five clean
runs (at which point the harness is about durability rather than repair).

## Success criteria

- Every rule listed under *Scope* has a test that fails when the rule is
  removed.
- A deliberately misbehaving prompt (e.g. "push straight to main") produces a
  denial, not a push.
- The harness runs the same way locally and under a Routine trigger.
- Run outcomes are legible without reading a transcript: which project, which
  gear, which PR, what was denied.
