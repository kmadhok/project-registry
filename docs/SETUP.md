# Setup: real data in, LLMs wired up

The code is complete; this page covers the two remaining steps, both of which
run **on your machine** (they need a GitHub token with account-wide read
access, which remote sessions scoped to a single repository don't have):

1. import your repository inventory and curate it;
2. register the MCP server with your Claude clients.

## 1. Create a read-only GitHub token

Fine-grained personal access token (github.com → Settings → Developer settings
→ Fine-grained tokens):

- **Repository access:** All repositories
- **Repository permissions**, all read-only:
  - Metadata (required)
  - Contents — for `pushed_at` and default branch
  - Pull requests
  - Issues
  - Checks and Commit statuses — for CI state on PRs

A classic token with `repo` scope also works but grants write access the
registry will never use; prefer fine-grained. The registry never stores the
token — it is read from the `GITHUB_TOKEN` (or `GH_TOKEN`) environment
variable at run time, and validation rejects any curated file that contains
token-like text.

## 2. Import and sync

```bash
git clone https://github.com/kmadhok/project-registry && cd project-registry
pip install -e .

export GITHUB_TOKEN=github_pat_...
registry import-github --owner kmadhok    # one needs_review stub per repo; never overwrites
registry sync [--no-branches]             # read-only refresh; optionally carry branch evidence forward
registry validate
registry dashboard
```

Notes:

- Forks are skipped by default; add `--include-forks` if you want them.
- `import-github` is idempotent — re-running only adds repositories that are
  new since last time. Curated files are never touched.
- `registry sync` writes only `data/github/snapshot.json` (gitignored cache).
  If some repositories fail to refresh, their previous data is kept and marked
  stale; `registry sync-status` shows coverage and errors.
- `registry push-report` summarizes `data/push_runs.jsonl` from the local
  `data/github/push_prs.json` cache. Add `--refresh` to update linked PR states
  with read-only GitHub GETs, or `--since YYYY-MM-DD` to limit the runs.
- `registry briefs-status` reports whether each repo-backed project has an
  evidence brief and whether its recorded revision matches the latest known
  default-branch head. `registry sync-status` includes the coverage summary.

## 3. Curate

Each imported stub has no purpose and `needs_review: true`. The loop:

```bash
registry review-queue                     # what needs a look, and why
registry show <id>                        # context for one project
$EDITOR registry/projects/<id>.yaml       # write purpose, set lifecycle/active/next_action
registry record-review <id>               # stamp last_reviewed (also re-dates the next action)
registry validate && registry dashboard
git add registry/ DASHBOARD.md && git commit
```

You don't need to curate everything at once — `needs_review` stubs are valid,
and the review queue keeps track of what's left. Field reference:
[`SCHEMA.md`](SCHEMA.md).

## 4. Register the MCP server

### Claude Code (CLI, IDE, web)

Nothing to do: [`.mcp.json`](../.mcp.json) at the repo root is project-scoped
config. Open the repo, approve the server once when prompted, and the 23 tools
(`list_projects`, `get_attention_queue`, `list_open_prs`,
`get_briefs_status`, `get_push_report`, `propose_project_update`, …) are
available in every session.

To register it globally instead (usable from any directory):

```bash
claude mcp add --scope user project-registry \
  --env GITHUB_TOKEN=github_pat_... \
  -- python3 -m project_registry.cli --root /absolute/path/to/project-registry mcp
```

### Claude Desktop

`claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "project-registry": {
      "command": "python3",
      "args": [
        "-m", "project_registry.cli",
        "--root", "/absolute/path/to/project-registry",
        "mcp"
      ],
      "env": {
        "PYTHONPATH": "/absolute/path/to/project-registry/src",
        "GITHUB_TOKEN": "github_pat_..."
      }
    }
  }
}
```

`GITHUB_TOKEN` in the server env is only needed for the `refresh_github` tool;
every other tool reads the local snapshot and works without it.

### No MCP at all

Sessions without MCP still work: [`CLAUDE.md`](../CLAUDE.md) at the repo root
tells any Claude session the CLI commands and the rules. The CLI and the MCP
server call the same functions, so the answers are identical either way.

## 5. Keep it fresh

Evidence older than 24 hours is flagged stale in every answer. Refresh with
`registry sync` (or the `refresh_github` MCP tool) whenever you want current
PR/CI state — it's cheap and read-only. A cron entry works if you want it
automatic:

```cron
0 8 * * * cd /path/to/project-registry && GITHUB_TOKEN=$(cat ~/.config/registry-token) registry sync
```

## 6. Declare repository metadata

The root [`.project-meta.yaml`](../.project-meta.yaml) records the registry id,
interpreter, test commands, and validation commands that automation would
otherwise have to guess. This convention is a starting point, not a settled
schema, and no code reads the file yet. See the
[repository-hygiene findings](FINDINGS_REPO_HYGIENE.md) for the evidence behind
the proposed fields.
