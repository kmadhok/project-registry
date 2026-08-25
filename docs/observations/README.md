# Observations

## Documents

- [`build-observe`](../../.claude/skills/build-observe/SKILL.md) runs:
  `<date>-build-sandbox-<name>.md`
- Legacy `push-project-observe` runs: `<date>-<sandbox>.md`
- Audits of real autonomous runs: `<date>-shadow-run-<short-run-id>-audit.md`
  (facts from the journal, GitHub, and independent re-execution; conclusions
  go to ADRs and `docs/RUNBOOK.md`)

**Facts only.** Session ids, PR URLs, commit SHAs, which registry path ran.
No analysis — the transcripts named in each file hold the full record, and a
cheap model parses them on demand.

This is deliberately separate from:

- [`PUSH_PROJECT_AUDIT.md`](../PUSH_PROJECT_AUDIT.md) — the hand-written
  2026-08-02 audit, which mixes facts and interpretation
- [`FINDINGS_PUSH_PROJECT_SKILL.md`](../FINDINGS_PUSH_PROJECT_SKILL.md) —
  conclusions drawn from observations

Every tool call in a transcript carries `attributionSkill`, so filtering to just
the skill's actions is a field check, not an inference:

```bash
jq -r 'select(.type=="assistant") | .timestamp as $t | .attributionSkill as $s
  | .message.content[]? | select(.type=="tool_use")
  | "\($t)\t\($s // "-")\t\(.name)\t\(.input|tostring[0:200])"' <transcript>
```

Subagent work is **not** in the parent transcript. It lives in
`<session-id>/subagents/agent-*.jsonl` and must be unioned in for a complete
picture.

## Fault injection

Run the implemented fault scenarios from the repository root:

```bash
bash scripts/faults/run-all.sh
```

Set `FAULT_TMP` to choose the scratch parent. Every scenario operates only in
a fresh directory beneath that parent. The runner prints `PASS`, `FAIL`, or
`SKIP` for all F1–F24 scenarios and exits non-zero if any implemented scenario
fails. `SKIP` denotes a documented stub that has not yet been staged.

Record a manual or rollout run in `docs/observations/<date>-faults.md`. Keep the
record factual and use this table:

| F# | Staged | Expected | Observed | Pass |
|---|---|---|---|---|
| F1 |  |  |  |  |
