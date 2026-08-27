# Autonomous build guard

The build guard is a Claude Code `PreToolUse` hook that limits destructive or
out-of-contract actions during an autonomous build run. It evaluates Bash and
project-registry MCP tool calls before they execute. A denial exits with status
2 and prints a one-line `build-guard: <rule_id>` reason to stderr.

The guard is active only while `data/build/lease.json` exists, including while
its status is `finalize_pending`. Without a lease,
it is inert and allows every tool call. Once a lease exists, malformed hook
input, malformed lease data, shell tokenization errors, branch-resolution
failures, and unexpected runtime errors fail closed.

Compound shell commands are split on `&&`, `||`, `;`, `|`, and newlines, and
every segment is checked. A preceding `cd` and git's `-C` option are honored
when deciding whether a command operates in the registry or target clone.
Shell redirection tokens are stripped from every segment before evaluation.
Heredoc bodies are removed before splitting and tokenization, including
heredocs inside `$( )`, so content written through tools such as `cat`, `tee`,
or `dd` is treated as data. Bodies fed to an interpreter or shell (`python`,
`node`, `bash`, `eval`, and their supported variants) are still scanned for
indirect GitHub or registry write commands.

## Rules

| Rule ID | Enforced boundary |
|---|---|
| `direct_write_scope` | File-writing tools cannot write inside the registry or to contract-forbidden paths while a lease exists. |
| `git_parse` | Git invocations whose global options do not yield a subcommand fail closed. |
| `push_remote` | Pushes use `origin` (or the leased target repository URL outside the registry). |
| `finalize_only` | A finalize-pending lease permits registry write-back but no further target push or PR creation/merge. |
| `force_push` | Blocks force flags and `+` refspecs on `git push`. |
| `tag_push_scope` | Target-repository tag pushes are limited to one explicit `checkpoint/<run_id>-*` tag; `--tags` and `--follow-tags` are forbidden. |
| `non_push_branch` | Target-repository branch pushes must use `push/<run_id>-*`. |
| `registry_push_branch` | Registry-repository pushes may target only `main`. |
| `branch_delete` | Branch deletion is limited to run branches and reconciliation targets. |
| `registry_commit_scope` | Registry commits may stage only `data/build/**`, `data/proposals/**` (builder-filed `needs_intent` proposals), and `DASHBOARD.md`. |
| `one_pr_per_chunk` | A PR cannot be created while the lease records another open PR. |
| `wrong_repo` | Explicit GitHub repository arguments must name the leased repository. |
| `pr_head_branch` | New PR heads must use `push/<run_id>-*`. |
| `merge_method` | Builder PRs may be merged only with squash. |
| `merge_admin` | Administrative merge bypasses are forbidden. |
| `unverified_merge` | A merge requires a known, unmerged lease PR with passed verification and an approve verdict. |
| `shadow_mode` | PR merges are forbidden while `lease.dry_run` is true. |
| `foreign_pr` | Close/edit/reopen/lock/ready/review operations are limited to lease PRs. |
| `pr_close_policy` | A lease PR may close only during reconciliation or after rejection, requested changes, or failed verification. |
| `issue_mutation` | GitHub issue mutations are forbidden. |
| `repo_mutation` | GitHub repository mutations are forbidden. |
| `api_mutation` | `gh api` is GET-only and cannot supply mutation fields or input. |
| `release_mutation` | GitHub release mutations are forbidden. |
| `secret_mutation` | GitHub secret and variable commands are forbidden. |
| `registry_write_tool` | Direct registry write CLI commands and MCP review/apply tools are forbidden. |
| `indirect_invocation` | Inline interpreter/shell code, heredocs, and command wrappers cannot conceal GitHub or registry write commands. |
| `intent_only_proposal` | CLI and MCP proposals may change only `brief.*` fields. |
| `breaker_bypass` | `registry build resume` cannot bypass the circuit breaker. |
| `secret_read` | Secret-looking files and contract-forbidden paths cannot be read. |
| `secret_env` | Secret-looking environment variables cannot be echoed or filtered from environment dumps. |
| `forbidden_path_stage` | Forbidden paths cannot be explicitly staged with `git add` or `git commit -a`. |
| `registry_destroy` | Recursive forced removal cannot target the registry root, `registry/`, or `data/`. |
| `branch_resolution` | A branch-dependent command is denied if its current branch cannot be resolved. |
| `guard_parse` | Structurally incomplete commands are denied rather than guessed. |
| `guard_error` | Malformed JSON, invalid leases, and unexpected errors fail closed. |

Every policy denial is also appended best-effort to `data/build/runs.jsonl` as a
`guard_denied` event. Logging failure does not weaken the denial.

The guard can inspect the command line and inline code, but it cannot inspect
the behavior of scripts or files invoked by otherwise ordinary commands. Such
files may themselves push or mutate GitHub state. Reconciliation therefore
compares actual GitHub state with the build journal and records any divergence.

## Test a command by hand

Use a disposable project root with a test lease; do not point this at a live
build lease. For example, from that disposable root:

```bash
echo '{"session_id":"manual","cwd":"/tmp/target-clone","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"git push origin main"}}' | CLAUDE_PROJECT_DIR=. python3 scripts/build-guard.py
echo $?
```

Exit status 0 means allowed. Exit status 2 means blocked, with the matching rule
ID on stderr. Automated coverage lives in `tests/test_build_guard.py`.
