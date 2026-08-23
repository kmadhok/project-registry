> **Retired 2026-08-22:** The cloud Routine is disabled. The PC scheduled run is primary; Mac runs are interactive. The remainder of this page is retained for history.

# Running push-project as a cloud Routine

The `push-project` skill was built to run locally. Routines run it on
Anthropic-managed infrastructure instead, so it keeps working with the laptop
closed. This page is the setup, and the reasons behind each field.

The one thing that makes cloud runs different: **a cloud container cannot see
this Mac.** The registry — curated intent, evidence briefs, the CLI — has to
arrive as a cloned repository, which is why the skill now lives at
`.claude/skills/push-project/SKILL.md` in this repo and detects which
environment it is in.

## Prerequisite: GitHub access for cloud sessions

Run `/web-setup` in the CLI once. This authorizes Claude Code on the web to
clone repositories. Without it the Routine has nothing to work on.

Note that this is separate from installing the Claude GitHub App — the App is
only needed for GitHub *event* triggers, which this Routine does not use.

## Create the Routine

At [claude.ai/code/routines](https://claude.ai/code/routines) → **New routine**.

### Name

`push-project — weekday portfolio push`

### Prompt

The prompt must be self-contained: a Routine run has no conversation history
and nobody to ask.

```
Run the push-project skill (bare invocation, no named project).

The skill is committed in the project-registry repository at
.claude/skills/push-project/SKILL.md — read it in full first and follow it
exactly, including its invariants.

You are in the cloud environment the skill describes: no .venv, no Codex
adapter, and the target repositories are already checked out beside the
registry rather than needing a clone. Implement chunks yourself, to the same
standard the skill requires of delegated work.

Ship at most one PR. If every focus project already has an open push/ PR
awaiting review, that is a successful run: do nothing and say so.
```

### Repositories

As of 2026-08-01, add these five:

- `kmadhok/project-registry` — required; it carries the skill, the curated
  intent, and the evidence briefs
- `kmadhok/interview-prep`
- `kmadhok/AI-News-Aggregator`
- `kmadhok/ai_engineering_markets`
- `kmadhok/underwriting-` (the trailing hyphen is part of the name)

The last four are the current focus list — a repo the Routine cannot clone is
a repo it cannot push.

Keep this list in sync with the focus list (`lifecycle: next` or `now`).
Adding a project to the registry does not make it reachable from the cloud;
adding it here does.

### Environment

Create a cloud environment with this setup script, so the registry CLI and
its MCP server can start:

```bash
cd project-registry && pip install -e . && pip install pytest
```

Without this the run fails at the first registry command: the checked-in
`.mcp.json` invokes `python3 -m project_registry.cli`, which needs PyYAML.
The result is cached, so it does not re-run every session.

**Network access:** the Default (Trusted) allowlist is enough. The Routine
talks to GitHub and PyPI, both already allowed.

### Trigger

**Schedule → Weekdays**, morning. Times are entered in local time and
converted automatically; runs may start a few minutes late due to stagger,
which does not matter for this work.

### Connectors

Remove every connector. This Routine needs none, and connectors included in a
Routine can be used without asking during a run — the registry MCP arrives
through the repo's own `.mcp.json`, not through a connector.

## What a run does

Identical to the local behavior, because it is the same skill file:

1. Picks one focus project from the registry (skipping any with an open
   `push/` PR, and never `project-registry` itself)
2. Gear 1 if the project has no `docs/SPEC.md` — drafts one, opens a PR, stops
3. Gear 2 otherwise — ships the first unchecked chunk from the spec
4. Records the run in the registry and reports what is waiting on you

At most one PR per run. If everything is blocked on review, the run does
nothing, which is the intended brake.

## Limits worth knowing

- Routines draw on subscription usage like any session, plus a daily cap on
  runs per account. Current consumption is at
  [claude.ai/settings/usage](https://claude.ai/settings/usage).
- Minimum schedule interval is one hour — irrelevant here, but it rules out
  tighter loops.
- Routines are in research preview; behavior and limits may change.
- A green run status means the session started and exited cleanly. It does
  **not** mean the work succeeded — open the run to see what actually
  happened.

## Writing back from the cloud

The skill records each run in the registry with `record-review` and commits
the result. If `main` is protected, that commit goes to a `push/` branch
instead, and the registry update needs merging like any other change. Claude
pushes to `claude/`-prefixed branches without restriction; other branches are
checked first and rejected if protected or owned by someone else.

## Keeping local and cloud in step

The skill exists in two places: `~/.claude/skills/push-project/SKILL.md` (what
local sessions load) and `.claude/skills/push-project/SKILL.md` (what cloud
runs load). They must stay identical. After editing either:

```bash
diff ~/.claude/skills/push-project/SKILL.md \
     .claude/skills/push-project/SKILL.md
```

An empty diff means both environments run the same logic. A non-empty diff
means the cloud and your laptop disagree about what the skill does.
