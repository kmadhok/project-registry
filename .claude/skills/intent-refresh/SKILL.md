---
name: intent-refresh
description: This skill should be used when the user asks to "refresh project intent", "update project briefs", "review stale briefs", or run "/intent-refresh [project-id]". It gathers repository evidence and files owner-reviewable brief proposals without applying them.
---

# intent-refresh

Keep project briefs current by proposing evidence-backed changes. Treat the brief as the owner's approval surface: create proposals only, never apply them and never edit registry YAML.

## Select targets

1. For `/intent-refresh <project-id>`, select only the named project.
2. For a bare invocation, run:

   ```bash
   registry brief-status --json
   ```

   Select every incomplete or stale project whose `automation_mode` is not `off`. When a project has no explicit automation mode, select it only when its lifecycle is `now` or `next`.
3. Use the MCP equivalent `get_brief_status` with `{"incomplete": true}` and/or `{"stale": true}` when MCP is available. Combine and deduplicate the returned project ids.

## Gather evidence per project

1. Read the registry entry with exactly one of:

   ```bash
   registry show <project-id> --json
   ```

   Or call `get_project` with `{"project_id": "<project-id>"}`.
2. Read the target repository's `README`, `CLAUDE.md`, `AGENTS.md`, and `docs/SPEC.md` when present. Use a shallow clone or read the files with `gh api`; do not infer missing content.
3. List merged builder work since `brief.reviewed`:

   ```bash
   gh pr list --repo <owner/repo> --state merged --search 'head:push/ merged:>=<reviewed-date>' --json number,title,url,mergedAt,headRefName
   ```

   When `brief.reviewed` is absent, list merged PRs whose `headRefName` begins with `push/` and use only evidence relevant to the current repository state.
4. Record the file paths read and the relevant merged PR URLs. Reference secret-bearing filenames only; never read, print, copy, or quote secret values.

## Draft the brief update

Ground every proposed value in the registry entry, repository files, or merged PR evidence.

- Draft at most five concrete, checkable `brief.done_criteria` items.
- Draft `brief.non_goals` and `brief.constraints` only when the evidence states them.
- Draft `brief.open_decisions` only for questions that require the owner; give each new question `status: open`.
- Draft `desired_outcome` only when it is missing and the evidence supports a specific outcome.
- Preserve answered decisions unless evidence clearly makes them obsolete; proposals remain owner-reviewed.
- Never invent the project's domain, priority, lifecycle, or product direction.
- When evidence is insufficient, propose only `brief.open_decisions` containing the specific owner question. Do not stamp `brief.reviewed` in that case.

## File one proposal per project

Combine all supported changes for one project into one command. Use comma-separated list values for the CLI list fields and JSON only for open decisions:

```bash
registry propose <project-id> \
  --set brief.done_criteria='criterion one,criterion two' \
  --set brief.non_goals='non-goal one,non-goal two' \
  --set brief.constraints='constraint one,constraint two' \
  --set brief.open_decisions='[{"question":"Owner question?","status":"open"}]' \
  --set brief.reviewed=<YYYY-MM-DD> \
  --rationale 'Evidence: <file paths>; <merged PR URLs>'
```

Add `--set desired_outcome='<supported outcome>'` when it is missing. Omit any unsupported or unchanged field. For insufficient evidence, use the same command with only `--set brief.open_decisions='<json>'` and the evidence rationale.

Use the MCP equivalent `propose_project_update` with:

```json
{
  "project_id": "<project-id>",
  "changes": {
    "brief.done_criteria": ["criterion one", "criterion two"],
    "brief.non_goals": ["non-goal one"],
    "brief.constraints": ["constraint one"],
    "brief.open_decisions": [{"question": "Owner question?", "status": "open"}],
    "brief.reviewed": "<YYYY-MM-DD>",
    "desired_outcome": "<supported outcome, only when missing>"
  },
  "rationale": "Evidence: <file paths>; <merged PR URLs>"
}
```

Omit unsupported keys. Call `propose_project_update` exactly once per selected project.

## Show results and stop

Print every proposal id, then show its exact diff:

```bash
registry proposal-show <proposal-id>
```

Use the MCP equivalent `get_project_update_proposal` with `{"proposal_id": "<proposal-id>"}`. Stop after displaying the diffs.

## Hard rules

- Never run `proposal-apply` or `record-review`, and never edit any file under `registry/`.
- Never set `lifecycle`, `active`, `priority`, `automation.*`, or any other field outside `purpose`, `desired_outcome`, and `brief.*`; normally leave `purpose` unchanged because the owner authored it.
- Never quote secrets.
- Create no more than one proposal per project per run.
- Propose questions only when evidence cannot support an intent statement; leave the answer to the owner.
