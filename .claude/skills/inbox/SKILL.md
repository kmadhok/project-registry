---
name: inbox
description: Walk the owner inbox one item at a time — approve or reject builder proposals, resume paused projects, answer open decisions — and clear it; `/inbox [--dry-run]`.
---

# inbox

Everything that needs the owner, one decision at a time. Read-only until the owner picks an action; every applied change goes through the registry's own commands (proposal-apply, proposal-reject, build resume, propose), never through direct YAML edits.

## Load

1. `registry owner-inbox --json`. If `count` is 0, say "Nothing needs you." and stop.
2. Group items by project and keep the registry order (proposals first, then leases, intent gaps, paused projects, failed runs, blocked classes).

## For each item

Present one item, then ask one question with the AskUserQuestion tool. Never ask about two items at once.

| kind | Show before asking | Options |
|---|---|---|
| `proposal_pending` | `registry proposal-show <id>` — the exact before/after and rationale | **Approve** (`registry proposal-apply <id> --approve`) · **Reject** (`registry proposal-reject <id>`) · **Skip** |
| `needs_intent` | the missing brief fields and the project's purpose/desired_outcome | **Answer now** → ask for each missing field in plain words, then `registry propose <id> --set brief.<field>='<value>' --rationale "owner answered via /inbox <date>"` and apply it · **Skip** |
| `paused` | the paused reason, `paused_at`, and the last run's digest path | **Resume** (`registry build resume <id>`) · **Keep paused** |
| `run_failed` | the digest's Summary and Guard denials sections | **Acknowledged** (`registry build resume <id>` if paused; otherwise nothing to run — the item clears after the next run) · **Open digest** (print it) · **Skip** |
| `blocked_by_policy` | the change classes named and what SPEC items they block | **Allow** (`registry propose <id> --set automation.allow=<classes> --rationale ...` then apply) · **Skip** |
| `finalize_pending` | the run id and what the lease says | **Confirm** (only after `git -C <registry> log origin/main` shows that run's write-back commit): `registry build finish <run> --confirm-writeback` · **Skip** |

Run the chosen command, print its last lines, and move on. On any error, show it and offer the same options again once; then skip.

`--dry-run`: show every item and the command each option would run; execute nothing.

## Finish

1. `registry dashboard`, then `registry validate` (0 errors required).
2. Commit what changed: `git add registry data/proposals data/audit_log.jsonl DASHBOARD.md && git commit -m "inbox: <n> item(s) decided <date>" && git push origin main`.
3. Print the remaining `registry owner-inbox` (should be empty) and, when a notify topic is configured, `registry notify --run <latest run id>` so the phone shows the cleared state.
