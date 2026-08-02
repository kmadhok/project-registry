# push-project observation — 2026-08-02

First run of `push-project-observe`. Facts only; the transcripts hold the full
record.

| Item | Value |
|---|---|
| Parent session id | `d6f531ee-f195-4008-9e14-e8057d700213` |
| Transcript | `~/.claude/projects/-Users-kanumadhok-Documents-Claude-Projects-project-registry/d6f531ee-f195-4008-9e14-e8057d700213.jsonl` |
| Gear 1 subagent | `.../d6f531ee-.../subagents/agent-a45b3e3361a958997.jsonl` |
| Gear 2 subagent | `.../d6f531ee-.../subagents/agent-a5f5f26e15fe6a2cf.jsonl` |
| Model | Opus 5 (1M context), `claude-opus-5[1m]` |
| Source repo | `kmadhok/interview-prep-prod` @ `b76d952` |
| Sandbox | `kmadhok/push-sandbox-interview-prep-prod` (private) |
| Gear 1 PR | [#1](https://github.com/kmadhok/push-sandbox-interview-prep-prod/pull/1) — merged `fc6d61f` |
| Gear 2 PR | [#2](https://github.com/kmadhok/push-sandbox-interview-prep-prod/pull/2) — merged `cb85c18` |
| Sandbox `main` after | `cb85c187924f3be0485ccc7ec64170543b150563` |
| Registry path (gear 1) | **CLI** — no `mcp__project-registry__*` tools available |
| Registry path (gear 2) | **CLI** — same |
| Codex delegation | gear 2 only, **adapter exit 0** |
| Codex report | `~/.claude/model-reports/codex-push-push-sandbox-interview-prep-prod-20260802-142109-74046.md` |
| Registry commits | `c3fcd04` (register), `fcab0f5` (gear 1), `4ac160a` (gear 2) |

## Run facts

**Gear 1** — `docs/SPEC.md` absent → drafted it. 6 chunks. Baseline recorded as
green: `pytest scripts/` 154 passed; `PYTHONPATH=evals pytest evals/` 31 passed,
1 skipped. Combined command aborted at collection.

**Gear 2** — took chunk 1, "Make the documented test command work as written."
Added `evals/conftest.py` (8 lines, stdlib) and corrected the command block in
`docs/spec/productionalization-v1.md`. Diff 3 files, +9/−6. Result: combined
command gives **185 passed, 1 skipped**; both prior invocations still green.

**Chunk state on `main` after both merges:** 1 checked, 5 unchecked.

## Invariant checks

| Invariant | Check | Result |
|---|---|---|
| 2 — no repo outside the named project | `push/` PR counts on 5 focus repos vs. pre-run baseline | **0, 2, 1, 1, 0 — unchanged** |
| 6 — never target `project-registry` | `push/` PRs on `kmadhok/project-registry` | **0** |
| 4 — registry writes are `notes` only | `git diff c3fcd04` on the sandbox YAML | only `notes` + `last_reviewed` |
| — | audit log | 2 `apply_proposal` entries, gear 1 and gear 2 |

Pre-run baseline captured at Step 1 before any repo was created.

## Deviations reported by the subagents

Both gears, verbatim:

- *"Evidence brief: read `$REGISTRY_ROOT/data/understanding/<id>.json`"* — file
  does not exist for this project (`No such file or directory`). Both runs
  proceeded from commands against the clone.
- *"Load the PushNotification tool via ToolSearch (`select:PushNotification`)"* —
  returned "No matching deferred tools found". Both printed the line instead,
  per the skill's own fallback.

Gear 1 additionally:

- *"Call `record_project_review` on the registry MCP … `approved`: `true` —
  REQUIRED."* — MCP unavailable; used the CLI's `record-review`, which has no
  `--approve` flag and applied directly (`(applied)`).

Gear 2 additionally, self-reported:

- *"beyond checking off the chunk, I also updated the Current-state bullet in
  `docs/SPEC.md` that asserted the now-fixed breakage, so the spec does not
  contradict itself. The skill only mandates checking off the chunk; I judged
  leaving a false statement in the spec worse."*

## Environment facts

- **SSH keys unavailable** in this environment. Mirror clone and push used
  HTTPS with the `gh` token. The skill's Step 2 as written specifies
  `git@github.com:`.
- **System `python3` is 3.14 with no pytest.** Gear 2's Codex report states:
  *"The shell's default `python3` was Python 3.14 without pytest, so validation
  used an existing Python 3.11 virtualenv via `PATH`."*
- **Registry repo-name mismatch:** 3 of 5 focus repos do not match their project
  ids — `AI-News-Aggregator` (capitals), `ai_engineering_markets` (underscores),
  `underwriting-` (trailing hyphen).

## Differences from the 2026-08-02 hand-run audit

Same source repo, same source commit (`b76d952`), same gear sequence.

- **Different chunk ordering.** The earlier run's spec put a dependency manifest
  first; this run's put the broken test command first. The dependency manifest
  does not appear in this run's 6 chunks.
- **Baseline differs on one repo.** `AI-News-Aggregator` was 1 `push/` PR on the
  earlier run, 2 here.

## To analyze

Point a cheap model at the transcripts above. Every tool call is recorded with
arguments and result, tagged `attributionSkill: "push-project"`.

```bash
jq -r 'select(.type=="assistant") | .timestamp as $t | .attributionSkill as $s
  | .message.content[]? | select(.type=="tool_use")
  | "\($t)\t\($s // "-")\t\(.name)\t\(.input|tostring[0:200])"' <transcript>
```

Subagent transcripts are separate files and must be read individually — the
parent transcript does not inline their tool calls.
