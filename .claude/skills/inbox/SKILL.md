---
name: inbox
description: Walk the owner inbox one item at a time — approve or reject builder proposals, resume paused projects, answer open decisions — and clear it; `/inbox [--dry-run]`.
---

# inbox

Everything that needs the owner, one decision at a time. Read-only until the owner picks an action; every applied change goes through the registry's own commands (proposal-apply, proposal-reject, build resume, propose), never through direct YAML edits.

## Load

1. `registry owner-inbox --json`. If `count` is 0, say "Nothing needs you." and stop.
2. Group items by project and keep the registry order (proposals first, then leases, intent gaps, paused projects, projects waiting on a decision, failed runs).
3. `registry build-queue --state waiting_owner` lists every project parked on an owner decision; each one has an item here. A waiting project is never selected by a scheduled run — clearing its item is what releases it.

## For each item

Present one item, then ask one question with the AskUserQuestion tool. Never ask about two items at once.

| kind | Show before asking | Options |
|---|---|---|
| `proposal_pending` | `registry proposal-show <id>` — the exact before/after and rationale | **Approve** (`registry proposal-apply <id> --approve`) · **Reject** (`registry proposal-reject <id>`) · **Skip** |
| `needs_intent` (brief gaps) | the missing brief fields and the project's purpose/desired_outcome | **Answer now** → ask for each missing field in plain words, then `registry propose <id> --set brief.<field>='<value>' --rationale "owner answered via /inbox <date>"` and apply it · **Skip** |
| `needs_intent` (a run stopped `needs_intent` and no proposal is pending) | the digest's Summary — the builder stopped for a decision but filed nothing | **Answer now** → the same `propose brief.*` path as above · **Acknowledge** (`registry record-review <id>` — counts as an owner action and re-arms one run) · **Skip** |
| `paused` | the paused reason, `paused_at`, and the last run's digest path | **Resume** (`registry build resume <id>`) · **Keep paused** |
| `run_failed` | the digest's Summary and Guard denials sections | **Acknowledged** (`registry build resume <id>` if paused; otherwise nothing to run — the item clears after the next run) · **Open digest** (print it) · **Skip** |
| `blocked_by_policy` | the change classes named, the run's digest Summary, and the SPEC items they block | **Allow** — run the item's `action` verbatim with a real `--rationale` (it already lists the classes the project allows today; `--set automation.allow=` replaces the whole list), then `proposal-apply --approve` · **Decline** (`registry record-review <id>`: the builder gets exactly one more attempt and waits again — to stop it for good, pause the project or narrow the brief instead) · **Skip** |
| `finalize_pending` | the run id and what the lease says | **Confirm** (only after `git -C <registry> log origin/main` shows that run's write-back commit): `registry build finish <run> --confirm-writeback` · **Skip** |

Run the chosen command, print its last lines, and move on. On any error, show it and offer the same options again once; then skip.

`--dry-run`: show every item and the command each option would run; execute nothing.

## Finish

1. `registry dashboard`, then `registry validate` (0 errors required).
2. Commit what changed: `git add registry data/proposals data/audit_log.jsonl DASHBOARD.md && git commit -m "inbox: <n> item(s) decided <date>" && git push origin main`.
3. Release what you unblocked: run `registry build-queue --json` and, for every project that was `waiting_owner` when the session started and is `ready` now, run `registry request-run <id> --reason "inbox <date>"`. Unconfigured means it prints the command and does nothing; configured means the build host starts that run within a minute instead of at the next cron.
4. Print the remaining `registry owner-inbox` (should be empty) and, when a notify topic is configured, `registry notify --inbox` so the phone shows the cleared state.
