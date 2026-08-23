# Builder agents

The autonomous builder separates planning from review with two Claude Code
project subagents. The orchestrator invokes each by name and supplies the inputs
in its prompt; the reviewer always starts with fresh context so it does not inherit
the implementer's assumptions.

## `build-planner`

The orchestrator invokes the `build-planner` subagent with the project brief,
current `docs/SPEC.md` (if present), verified clone facts, and the complete
`registry validate-spec` report. The planner uses read-only repository inspection
to return either:

- the complete updated SPEC in a `markdown` fence, with its first unchecked item
  validating `ready`; or
- one JSON decision object with decision `needs_intent` or `roadmap_done` and a
  reason.

The planner preserves the standard SPEC headings and checked items. Every new or
amended unchecked item includes Acceptance, Tests, Size, Classes, and
Verified-missing evidence from a command run against the clone. It cannot invent
intent or propose never-class work: deployments, outbound messages, payments,
external mutations, secret access, force-pushes, default-branch commits, or
closing/editing issues or PRs the builder did not create.

## `build-reviewer`

The orchestrator invokes `build-reviewer` in fresh context after implementation.
It supplies the brief, exact SPEC item, branch-versus-main diff, before-and-after
test output, and `.project-meta.yaml` contract. The reviewer has read-only tools
and cannot edit, commit, or push.

The reviewer rejects forbidden paths, undeclared or disallowed policy classes,
personal-data violations, secrets, contradictions with constraints/non-goals,
non-plan chunks without test changes, and chunks that fail to tick their SPEC
checkbox in the same diff. It may request changes once for fixable quality issues.
Approval requires test evidence that demonstrates every acceptance criterion.

The reviewer returns only JSON conforming to this schema:

```json
{
  "type": "object",
  "properties": {
    "verdict": {"enum": ["approve", "request_changes", "reject"]},
    "reasons": {"type": "array", "items": {"type": "string"}},
    "risk_flags": {"type": "array", "items": {"type": "string"}},
    "classes_seen": {
      "type": "array",
      "items": {
        "enum": ["dependencies", "ci", "generated_data", "public_api", "migrations", "personal_data", "plan", "contract", "none"]
      }
    }
  },
  "required": ["verdict", "reasons", "risk_flags", "classes_seen"],
  "additionalProperties": false,
  "allOf": [
    {
      "if": {"properties": {"verdict": {"const": "approve"}}},
      "else": {"properties": {"reasons": {"minItems": 1}}}
    }
  ]
}
```

For `request_changes` or `reject`, `reasons` must contain at least one string.
Only `approve` permits the orchestrator to merge.
