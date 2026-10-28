# Runbook — operating the autonomous builder

What to run, where it runs, how to read a run, and how to recover one. Intent
lives in `CLAUDE.md`; enforcement decisions in `docs/ADRs/ADR-006-autonomous-builder.md`;
the step-by-step orchestration in `.claude/skills/push-project/SKILL.md`.

## Hosts

| Label in journal | Machine | Registry checkout | Role |
|---|---|---|---|
| `mac` | Kanu's MacBook | `/Users/kanumadhok/Documents/Claude/Projects/project-registry` | interactive runs (`/push-project [id]`) and audits |
| `pc` | Windows PC, WSL Ubuntu (`learnmsds`) | `/home/learnmsds/Github/project-registry` | scheduled runs via Task Scheduler → `scripts/run-build.sh`; **offline since mid-August 2026** |
| hostname-derived | Linux build instance (`ubuntu`, tailnet `instance-20250830-043814`) | `/home/ubuntu/project-registry` | scheduled runs 2026-08-24 to 2026-08-28, then failing silently; **retired 2026-10-15** (stands down via `data/build/active_host`); set `BUILD_HOST_LABEL` there so the journal label is honest |
| `kayak` | Windows PC `kayak` (on 24/7), WSL2 Ubuntu (`kanum`, systemd, linger) | `/home/kanum/project-registry` | **active build host since 2026-10-15**; systemd user units from `scripts/systemd/user/` |

`registry build env --json` resolves `host_kind` (drives CLI/Codex paths) and
`host` (the journal label: `BUILD_HOST_LABEL`, else `mac` on macOS, else the
short hostname). Runs before 2026-08-25 labelled every Linux host `pc`;
treat that label as "not the Mac" for those runs.

Runtime facts that bite:

- `~/bin` and `~/.local/bin` are not on the non-interactive `PATH`; the
  launcher exports them. Codex on Linux needs `codex-code-mode-host` beside
  the binary or every file edit fails.
- Codex must run in the foreground. A backgrounded Codex ends the turn and
  the run dies mid-chunk (this happened twice in 2026-08).
- The reviewer's second opinion is `codex review --base main` (codex-cli
  0.150 on the Mac has it; confirm `codex review --help` on a new Linux host).
  When it fails the skill journals `second_opinion status=unavailable` and
  continues with the single reviewer — a missing second opinion never stops a
  run, but a week of `unavailable` is a retro finding.
- `data/build/lease.json` TTL is 3 h; an expired lease is reconciled by the
  next `build start`.
- The Mac venv is an *editable* install pointing at the main checkout's
  `src/`. `.venv/bin/registry` therefore runs whatever branch that checkout
  has out — from a worktree, or with the main checkout on an old branch, it
  can silently lack `build` commands. In a worktree always run
  `PYTHONPATH=src .venv/bin/python -m project_registry.cli …` and
  `PYTHONPATH=src bash scripts/faults/run-all.sh`; before an interactive Mac
  run, `git -C <main checkout> switch main`.

## Launching

```bash
scripts/run-build.sh                 # bare run: registry picks the ready project
scripts/run-build.sh <project-id>    # named run
scripts/run-build.sh --dry-run       # print the command without running
```

Logs land in `../build-work/logs/<utc>-<host>.log`. Target clones live in
`../build-work/<run-id>/` and are deleted at the end of a clean run.
While a launch runs, pull, sync, and Claude output is also copied to
`<log>.tail`; the heartbeat reads it and the launcher deletes it afterwards.

The launcher `git pull`s its own checkout, so a pull can rewrite
`run-build.sh` while it is running. The whole script body lives in `main()`,
which bash parses in full before executing, and `main "$@"; exit` stops bash
from reading the rewritten file afterwards. Keep any new code inside `main()`.

The launcher refreshes GitHub evidence (`registry sync`) after `git pull` and
before the run, but only when `GITHUB_TOKEN` is in the scheduler's
environment; a missing token or a failed sync is logged and never blocks the
build. Put the token in the scheduler entry's environment (a `GITHUB_TOKEN=…`
line above the crontab entry, or `Environment=` in a systemd unit) — never in
the repository. `registry sync-status` shows whether it worked.

Before scheduling on a new host: `git pull --ff-only`, `registry validate`
(0 errors), `registry build env --json` (check `codex_bin` is not null),
`registry build-queue` (see what a bare run would pick).

`data/build/active_host` names the single scheduled host (one label, e.g.
`kayak`). Leases live in each checkout, so without it two scheduled hosts can
build the same project at once. A host whose label differs stands down: the
launcher exits 0 after `git pull` without running Claude or the heartbeat,
`registry build start` journals `stopped` (`detail.reason=inactive_host`), and
`notify --health` sends nothing. The Mac (`mac`) is interactive and exempt. Delete the file to allow any host; change it
to move the builder.

On kayak (no sudo; linger on) the units are user units:

```bash
mkdir -p ~/.config/systemd/user && cp scripts/systemd/user/*.service scripts/systemd/registry-*.timer ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now registry-build.timer registry-watchdog.timer
```

`~/.config/registry-build.env` there holds `BUILD_HOST_LABEL=kayak` and
`GITHUB_TOKEN`; Claude, Codex, and gh use the host's own logins.

The VPS systemd units live in `scripts/systemd/registry-build.service` and
`registry-build.timer`: weekdays at 07:00 America/Chicago, with a three-hour
service timeout. These files are the source of truth for the host units;
shared secrets and host settings belong in
`/home/ubuntu/.config/registry-build.env`.

## Reading a run

- `registry build-health` — did the builder run when expected? Status, last
  run, weekdays missed, next expected run, and brakes; add `--json` for data.
- `registry build-report [--since <date>]` — totals: runs by outcome/host,
  chunks started/merged/skipped, guard denials, reverts.
- `data/build/digests/<run-id>.md` — one page per run: outcome, merged chunks
  with revert commands, rejections/skips, guard denials, projects needing intent,
  and — when the run finished with `--spec` — an `## Outcome` line scoring the
  brief's done criteria (`criteria: 2/5 met · 2 blocked (generated_data) …`).
  `registry outcome-status <id> <clone>/docs/SPEC.md` prints the same table
  for any clone.
- `data/build/runs.jsonl` — the journal; `grep <run-id>` gives the full
  sequence. Expected per chunk: `chunk_started → verify_passed → pr_opened →
  review_verdict → (merged | chunk_skipped | chunk_rejected)`. A
  `request_changes` round shows as a second `verify_passed` **after** its own
  `review_verdict`. Anything else is a bug in the skill.
- `DASHBOARD.md` — regenerated by every writeback.
- `registry build-queue` — `waiting_owner` means the last run stopped on
  `needs_intent` or `blocked_by_policy` and the owner has not acted since; the
  inbox item names the releasing command. `registry build-readiness <id>`
  prints the `waiting on` line with the classes, date, and run id.

Shadow-run PRs stay open on `push/<run-id>-*` branches. The owner merges or
closes them by hand; the builder never touches them again. Runs before
2026-08-25 stacked chunks on each other (later PRs contain earlier ones) —
merge those bottom-up or squash the top one.

## Notifications

Every run ends with `registry notify --run <run-id>`: the first line is
`<project> · <outcome> · merged <n>`, then up to five merged PR links, any
rejections/denials, and the owner inbox (`registry owner-inbox`). Delivery is
an ntfy topic — install the ntfy app on the phone/Mac, subscribe to a private
topic name, and put it in the build host's environment as
`REGISTRY_NTFY_TOPIC` (or in `data/build/notify.json`, gitignored). Test with
`registry notify --run <any past run> --dry-run`. The topic name is a shared
secret: never commit it.

`registry notify --health` sends a heartbeat: the journal-derived health
headline, the next expected run, and the owner inbox. The launcher sends it
at the end of every launch, including a failed pull or builder command, and
the independent watchdog timer sends it again at 09:00 America/Chicago.
With no topic configured it prints the message with `sent=False`.

Health uses UTC weekdays, an expected start of 12:00 UTC, and a two-hour
grace period. **OK** means no weekday window has been missed and the last run
did not fail; **WARN** means the builder ran on schedule but the last run
ended in a failure outcome (`notify.FAILURE_OUTCOMES`: `aborted`,
`contract_broken`, `crashed`, `baseline_red`, `codex_unavailable`, …), or it
has no finish and no lease is held — the headline then appends the run's
summary from its `run_finished` detail; **LATE** means one window has been
missed; **STALLED** means two or more; **NEVER** means there are no started
runs in the journal. LATE and STALLED outrank WARN. Human-wait outcomes
(`needs_intent`, `blocked_by_policy`) stay OK; the inbox carries them. A run
without a finish shows `crashed?`. STOP and held leases appear in the
headline, as does a preflight refusal logged after the last run. The UTC
health window stays fixed even when the Chicago timers shift for daylight
saving.

The launcher calls `notify --health --exit-code <code> --log-tail <log>.tail`.
The body always shows `builder exit: <code>` when an exit code is passed; the
last five non-blank lines of the tail (each cut to 160 characters) appear
under `log tail:` only when the exit code is non-zero or the status is not
OK. A non-zero exit raises priority to at least 4 and swaps the ✅ tag for
⚠️. Those lines go to the ntfy topic; drop `--log-tail` from
`scripts/run-build.sh` to stop sending them. The 09:00 watchdog passes
neither option, so its body has no exit or tail lines.

### Watchdog

The watchdog runs independently of the build timer, so it can report a dead
build timer or a launcher that exits before sending its heartbeat. Install
it from the registry checkout on the VPS:

```bash
sudo cp scripts/systemd/registry-watchdog.* /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now registry-watchdog.timer
```

Residual risk: if the host itself is down, nothing fires — the absence of
the 09:00 message is the signal.

## Shadow gate — when a project may switch to `build`

`registry build-readiness <id> --shadow-gate [--min-runs 5]` evaluates the
journal and fails closed. All of:

| Check | Meaning |
|---|---|
| `shadow_runs` | at least `min-runs` runs finished `shadow_completed` |
| `guard_denials` | zero `guard_denied` events on the project — a denial caused by the skill is a skill bug, fix it and re-enter shadow |
| `verdict_on_every_pr` | every `pr_opened` chunk has a `review_verdict` |
| `no_failure_outcomes` | no `crashed`, `registry_dirty`, `aborted`, `baseline_red`, `contract_broken`, `codex_unavailable` |
| `digest_present` | a digest file exists for every shadow run |
| `not_paused` | the circuit breaker is clear |

Plus two owner checks the gate cannot do: read every shadow PR — any PR the
reviewer approved that you would have rejected is a blocking finding — and
judge the planner's SPEC rewrite quality hard (it is the template for
everything after). Then `registry propose <id> --set automation.mode=build
--rationale "..."` and `proposal-apply --approve`.

Pause or revert to shadow when: a revert lands, two `crashed` runs in a week,
or any guard bypass. `touch data/build/STOP` halts the next run;
`automation.paused: true` (by proposal) pauses one project.

## Recovery

| `build start` says | Cause | Do |
|---|---|---|
| `finalize_pending` | previous run committed nothing or the push failed after `build finish` | `registry build writeback <run-id>` (dashboard, registry commit, push, confirm, confirm commit — idempotent; `pushed: false` means the remote is still unreachable), then start again |
| `registry_dirty` | uncommitted changes outside `data/build/`, `data/proposals/`, `DASHBOARD.md`, or not on `main` | commit or stash by hand; never let a run clean it |
| `lease_held` | another run is active (or died < 3 h ago) | wait, or if the process is gone: wait for TTL, then start again → `reconciling` |
| `reconciling` + `actions` | expired lease from a crashed run | execute the listed `close_pr` / `delete_branch` actions, `registry build reconcile --done`, start again |
| `stopped` | `data/build/STOP` exists | remove it when ready |
| `no_candidate` | nothing `ready`: briefs incomplete, paused, waiting on an owner decision (`waiting_owner`), or modes unset | `registry build-queue` explains each project; `registry owner-inbox` holds the command that releases a waiting one |

Other situations:

- **Run aborted with a guard denial.** Read the `guard_denied` event's
  `command`. If the guard was right, the skill or plan is wrong. If the guard
  was wrong, reproduce it with a disposable lease (see `docs/BUILD_GUARD.md`),
  fix `scripts/build-guard.py` with a test, and only then rerun.
- **Digest missing for a run.** It was written on the build host but not
  committed (`.gitignore` bug fixed 2026-08-25). Recover it from that host's
  `data/build/digests/` and commit it with `build: digest <run-id>`.
- **Project paused** (`state.json` `paused_reason`): investigate, then
  `registry build resume <id>`.
- **Journal disagrees with GitHub** (PR merged but no `merged` event, tag
  missing): the next `build start` reconciles; if it does not, record what
  you found in `docs/observations/` and fix by hand — never edit
  `runs.jsonl` by hand.

## Owner loop

The phone notification says whether anything needs you. When it does, run
`/inbox` on the Mac (`.claude/skills/inbox/SKILL.md`): it walks
`registry owner-inbox` one item at a time — shows the proposal diff or the
failure digest, asks approve / reject / resume / skip, runs the registry
command, commits, pushes. Nothing else is required of the owner day to day.
Phone-side approve/reject buttons are the next step: `docs/ideas/owner-loop.md`.

A decision that makes a project `ready` should not wait for tomorrow's cron.
`/inbox` ends by running `registry request-run <id>` for each project it
released; that publishes `{"action":"run","project":"<id>"}` to a second,
private ntfy *command* topic (`data/build/notify.json` `command_topic`, or
`REGISTRY_NTFY_COMMAND_TOPIC`; unconfigured means "print and do nothing").
On the build host `scripts/decision-listener.sh` subscribes to that topic,
validates the project id against the registry, and runs
`scripts/run-build.sh <id>` — a named run through the normal lease,
eligibility, and guard. Anyone holding the command topic can trigger a run,
nothing more; pick a long random name and never commit it. Install it as a
user service:

```ini
# ~/.config/systemd/user/registry-listener.service
[Unit]
Description=project-registry decision listener
After=network-online.target

[Service]
WorkingDirectory=/home/ubuntu/project-registry
Environment=BUILD_HOST_LABEL=vps-e7eb4af7
Environment=GITHUB_TOKEN=<token>
ExecStart=/home/ubuntu/project-registry/scripts/decision-listener.sh
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
```

`systemctl --user enable --now registry-listener` and `loginctl enable-linger
ubuntu` so it survives logout. Log: `../build-work/logs/listener.log`.

The `PreToolUse` hook in `.claude/settings.json` runs the guard only when a
lease or the guard script exists at `$CLAUDE_PROJECT_DIR`; a checkout on a
branch without `scripts/build-guard.py` (or a nested worktree whose hook
points at such a checkout) would otherwise fail closed on every tool call.

## Weekly retro

`scripts/run-retro.sh` runs `/build-retro` (`.claude/skills/build-retro/SKILL.md`)
unattended: it refuses while a build lease exists, pulls `main`, refreshes
evidence when `GITHUB_TOKEN` is set, and logs to
`../build-work/logs/<utc>-retro-<host>.log`. The skill reads the window's
runs, writes `docs/observations/<date>-retro.md`, files proposals for brief
and policy changes, commits both to `main`, and sends them through
`registry notify --inbox`. Approve or reject each proposal from `/inbox`;
that is the whole owner loop.

Schedule it on the build host one hour after the week's first build, so the
retro sees that run:

```cron
GITHUB_TOKEN=<token>
BUILD_HOST_LABEL=vps-e7eb4af7
0 12 * * *   /home/ubuntu/project-registry/scripts/run-build.sh
0 13 * * 1   /home/ubuntu/project-registry/scripts/run-retro.sh
```

`scripts/run-retro.sh --dry-run` prints the command without running it. A
manual `/build-retro [--since YYYY-MM-DD]` on the Mac still works when the
main checkout is on `main`.

## Observations

Every audited run or fault exercise gets a page in `docs/observations/`
(`<date>-<what>.md`): what happened, what the journal said, what was wrong,
what changed because of it. The 2026-08-25 audit of run
`20260825T120035Z-pc-d8fa` is the reference example.
