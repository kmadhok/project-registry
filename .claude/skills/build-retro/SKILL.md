---
name: build-retro
description: Weekly retrospective of autonomous build runs — reads the journal, digests, and owner inbox, records what flowed and what stalled in docs/observations, and files brief/policy proposals for the owner; `/build-retro [--since YYYY-MM-DD]`.
---

# build-retro

Turn a window of build runs into two things: a factual observation file and proposals the owner can approve with one command. Never apply proposals, never edit registry YAML, never touch a target repository. Fresh context is the point — do not reuse a build session.

Default window: since the previous retro file in `docs/observations/*-retro.md`, else the last 7 days. `--since YYYY-MM-DD` overrides.

Runs on any host with the registry checkout on `main` — scheduled on the build host through `scripts/run-retro.sh`, or by hand on the Mac. If `data/build/lease.json` exists, stop immediately: a build is running and the journal is mid-write.

## Gather (read-only)

1. `registry build-report --since <date> --json` — runs by outcome, host, and project; chunks merged, skipped, rejected; guard denials; paused projects; review verdicts (accepted / unaccepted / probed) and second opinions (ok / unavailable, findings confirmed / refuted / out of scope).
2. Every `data/build/digests/<run-id>.md` whose run id falls in the window. A missing digest is itself a finding (record the run id).
3. `registry owner-inbox --json` and `registry build-queue --json`.
4. `registry sync` when `GITHUB_TOKEN` is set, then `registry prs --json`; and per active project `gh pr list --repo <owner/repo> --state closed --search 'head:push/' --json number,title,mergedAt,closedAt` to see builder PRs the owner closed without merging, and `git log --oneline -30` of `main` for reverts of `checkpoint/` commits.

## Judge

For each project with at least one run in the window, one line each:

- **Flow** — chunks merged per run, and the trend against the previous retro.
- **Stalls** — for every run that did not finish `completed`/`budget_exhausted`, the outcome and its cause class: infrastructure (Codex sandbox, session quota, host offline), policy (`blocked_by_policy`, `needs_intent`), or quality (`reject`, revert, owner-closed PR).
- **Reviewer misses** — merged chunks later reverted, redone, or contradicted by a following chunk; cite both run ids. Also: approvals recorded without probes, and second opinions `unavailable` for the whole window (an infrastructure finding, not a reviewer one).
- **Brief drift** — done criteria now met (retire them) or unreachable under the current `automation.allow` (either allow the class or drop the criterion).
- **Waiting** — projects `waiting_owner` for more than a week (`registry build-queue --state waiting_owner`): the inbox item has been ignored; say so, do not re-file it.

## Record

Write `docs/observations/<date>-retro.md`:

```markdown
# Build retro — <date> (window <since> → <date>)

| Project | Run | Outcome | Merged | Stalled on | Follow-up |
|---|---|---|---|---|---|

## What changed because of this

- <judgement> — run <id> — proposal <id or "none">
```

Facts only in the table; judgements only in the closing list, each tied to a run id.

## Propose (never apply)

File exactly one proposal per judgement that implies a curated change; skip anything already pending in `registry owner-inbox`:

- brief: `registry propose <id> --set brief.done_criteria='[...]' --rationale "retro <date>: <one line>"`
- policy: `registry propose <id> --set automation.allow=personal_data --rationale "..."`, or `--set automation.budget.chunks_per_run=<n>`
- repeated infrastructure failure: `registry propose <id> --set automation.paused=true --rationale "..."`

## Deliver

1. `registry validate` (0 errors required), then commit the retro and its proposals to `main`: `git add docs/observations data/proposals data/audit_log.jsonl && git commit -m "retro: <date>" && git push origin main`. On non-fast-forward, `git pull --rebase` once and push again; if it still fails, leave the commit local and say so — the next `/inbox` on the Mac pulls it.
2. `registry notify --inbox` so the retro's proposals reach the owner in the same channel as run digests (it prints the message when no topic is configured).
3. Print the observation file path last.
