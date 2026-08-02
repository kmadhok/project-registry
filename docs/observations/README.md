# Observations

One file per `push-project-observe` run: `<date>-<sandbox>.md`.

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
