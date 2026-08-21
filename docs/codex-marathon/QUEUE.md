# Codex marathon — task queue (2026-08-21)

Work this top to bottom. A task is done only when every acceptance line holds
and its validation command passes. Tick `- [x]` when done; write `- [~]` and a
one-line reason when blocked after two attempts. Never reorder or delete tasks.
You may append tasks under "Discovered" if you find real, bounded work.

Conventions used below:
- `PY` = `.venv/bin/python` (Python 3.11; do not use system `python3`).
- `REG` = `.venv/bin/registry` (equivalent: `PYTHONPATH=src PY -m project_registry.cli`).
- Full gate = `PY -m pytest -q` green AND `REG validate` exit 0.
- Baseline at queue creation: **141 passed**, validate clean, `main` = `64c1394`.

---

## T0 — Setup and branch (P0)

- [x] `git fetch origin && git checkout -b codex/marathon-2026-08-21 origin/main`
- [x] Commit the five untracked docs **verbatim, no edits**:
      `docs/BRANCH_CAPTURE_SPEC.md docs/FINDINGS_PUSH_PROJECT_SKILL.md docs/FINDINGS_REGISTRY_MCP.md docs/FINDINGS_REPO_HYGIENE.md docs/PUSH_PROJECT_TEST_HARNESS.md`
      plus `docs/codex-marathon/` (this queue, PROMPT.md, and a new PROGRESS.md),
      `scripts/codex-marathon.sh`, and the modified `.gitignore` (ignores `.codex-marathon/` and `.marathon-stop`).
      Message: `marathon(T0): commit audit findings, branch-capture spec, marathon queue`.
- [x] Leave `2026-08-02-121320-local-command-caveat…txt` at repo root untouched and uncommitted (owner's stray transcript; not yours to move). Likewise leave any untracked `data/proposals/*.json` alone — other sessions file proposals there; they are not part of this branch.
- [x] Push the branch; open ONE draft PR titled `Codex marathon 2026-08-21` with body = the task list (ids + titles + status). Record the PR URL in PROGRESS.md. All later tasks update this PR body; never open a second PR.
- Validation: `git status --porcelain | grep -v '^?? 2026-08-02' | grep -v '^?? data/proposals/'` is empty; `gh pr view --json url` succeeds.

## T1a — Branch capture: client + snapshot model (P0)

Spec: `docs/BRANCH_CAPTURE_SPEC.md` (Rev 3). Read ALL 674 lines before touching code. Sections: §4.1 data model, §4.2 GraphQL client (read-only verb allowlist — §4.2.2 is the design; do not weaken `ALLOWED_METHOD` semantics, replace with the allowlist exactly as specified), §4.2.3 query.

- [x] **Contract re-check first** (spec §7): run the `gh api graphql` probe for `kmadhok/interview-prep` with `first:2`. Confirm `committedDate` non-null on every node. If the shape differs from §4.2.3, STOP this task, record the actual shape in PROGRESS.md, mark `[~]`, and continue with T2.
- [x] Implement `Branch` dataclass + snapshot fields (§4.1) with `branches_fetched_at`, `branches_partial`, `branches_skipped`, tolerant parsing.
- [x] Implement `graphql()` transport in `github/client.py` per §4.2 — POST allowed only through `graphql()`, which refuses non-`query` operations; the HTTP-verb test in §6 must prove no mutation path exists.
- [x] Unit tests per §6 for model + client (network-free; use `tests/conftest.py` `NOW`). Include the "REST would have produced silent zero" regression: a node missing `committedDate` counts toward `branches_skipped`, never silently passes.
- Validation: full gate. Commit `marathon(T1a): branch capture — graphql client and snapshot model`.

## T1b — Branch capture: sync + signals + CLI + dashboard (P0, depends T1a)

- [x] `github/sync.py` per §4.3: one paginated `refs` read per repo, `previous` threading, per-repo error handling into `SyncResult.partial_errors`, `--no-branches` (or the spec's named flag) for cost control.
- [x] `signals.py` per §4.4: `stale_branches` rule — ONE item per repo, counts not names, `_signal_ref` rendering, threshold constant `stale_branch_days` (default 90 until T1c says otherwise), rule description registered so `registry rules` lists it.
- [x] `cli.py` per §4.5 (`show` line, sync flag) and `dashboard.py` per §4.6. Surface `branches_partial` visibly in `show` (spec §9 Q4 — implement the warning; that question is answered "yes, at least the warning").
- [x] MCP: if the MCP server exposes attention/show tools that wrap the same query functions, they must pick the new data up with no code fork — verify with a test in `tests/test_mcp.py`.
- [x] Tests per §6 for sync/signals/cli (monkeypatch-based CLI test as the spec declares).
- Validation: full gate; `REG rules | grep -i stale_branches`. Commit `marathon(T1b): branch capture — sync, stale_branches rule, cli, dashboard`.

## T1c — Branch capture: live smoke, threshold from data, open questions (P0, depends T1b)

- [x] Smoke against the real repo, read-only: `GITHUB_TOKEN=$(gh auth token) REG sync --repo kmadhok/interview-prep`. Expect `branches_fetched=true`, `branches_partial=false`, ~800 branches, ~9 GraphQL requests, seconds not minutes. Record the actual numbers in PROGRESS.md. Do not commit `data/github/` (gitignored).
- [x] Histogram branch ages from the snapshot (spec §9 Q1). Pick `stale_branch_days` from the distribution; if the data does not clearly argue for a change, keep 90 and say so. Record the histogram and the decision.
- [x] `REG attention` shows exactly one `stale_branches` item for `kmadhok/interview-prep`; `REG show interview-prep` renders the branch line.
- [x] Append a `## Rev 4 — implementation notes` section to `docs/BRANCH_CAPTURE_SPEC.md` answering each §9 question with the decision taken (Q1 data-driven, Q2 counts kept, Q3 kept with the `show` warning, Q4 warning implemented, Q5 allowlist implemented + test name), and listing any deviation from §4 with the reason. Keep the checklist in §5 true.
- [x] Update `docs/SCHEMA.md` / `docs/PURPOSE.md` only where they describe observed data or attention rules and are now stale (e.g. the "stale branches" promise in PURPOSE is now implemented).
- Validation: full gate. Commit `marathon(T1c): branch capture — smoke results, threshold decision, spec notes`.

## T2 — M1: falsy curated fields must round-trip (P1)

Source: `docs/FINDINGS_REGISTRY_MCP.md#M1`. `model.py:439-466` emits keys only when truthy; `is_fork: false`, `[]`, `""` vanish on the next automated write.

- [x] Implement fix option 1: track which keys were present in the source YAML and re-emit on that basis (Pydantic `exclude_unset` shape). Keys never present stay absent; keys present at a falsy value survive. Defaults for new objects: unchanged output.
- [x] Tests (requirement-driven): `is_fork: false` survives `record-review`; `tags: []` survives; `next_action: ""` survives; a file that never had `is_fork` does not gain it; a proposal apply does not add unset keys. Run the whole registry through load → save → load and assert byte-stable YAML for every `registry/projects/*.yaml` (if any file is NOT stable today, report which keys and why in PROGRESS — do not "fix" curated files).
- Validation: full gate. Commit `marathon(T2): preserve explicitly-set falsy curated fields on write`.

## T3 — M3: MCP review without `approved` must not read as success (P1)

Source: `docs/FINDINGS_REGISTRY_MCP.md#M3`. `mcp/server.py:337-349` passes `approved=False` by default; the tool files a pending proposal and reports success.

- [x] Make `record_project_review` (and `apply_approved_project_update` if it has the same trap) return an unmistakable non-success when `approved` is absent/false: MCP `isError: true` (or the server's equivalent error envelope) with text stating "pending proposal <id> filed, NOTHING applied; call again with approved=true". Do not change the CLI's direct-apply behavior.
- [x] Document the divergence (MCP requires explicit approval; CLI applies directly) in `docs/SCHEMA.md` or `docs/SETUP.md` — wherever write paths are described — and in the `CLAUDE.md` cheatsheet line for `record-review` (one sentence).
- [x] Tests: MCP call without `approved` → error envelope + proposal exists + project unchanged; with `approved=true` → applied; CLI path unchanged.
- Validation: full gate. Commit `marathon(T3): mcp record_project_review refuses silently-pending reviews`.

## T4 — `registry push-report` (P1)

Source: memory note "optional/future: `registry push-report` command (journal × push/ PR states → merge-rate report)". Data: `data/push_runs.jsonl` (fields `ts, host, project, gear, outcome, pr`; `project/gear/pr` may be null for `parked`/`empty_focus`).

- [x] Add a query function (in `queries.py` or a new `push_runs.py`) that parses the journal tolerantly (bad line → counted + reported, never crashes) and aggregates: runs by host, by outcome, by project, by gear; PRs by state (`open|merged|closed|unknown`).
- [x] PR state resolution: through the existing GET-only client (`GET /repos/{o}/{r}/pulls/{n}`), cached under `data/github/push_prs.json` with a `fetched_at`. `--refresh` fetches; without it, report from cache; never-fetched = `unknown`. Unknown is never counted as merged (same "unknown ≠ passing" philosophy as the rest of the repo).
- [x] CLI `registry push-report [--json] [--refresh] [--since YYYY-MM-DD]`; MCP tool `get_push_report` calling the same function. Add both to `CLAUDE.md` cheatsheet and `README`/`docs/SETUP.md` where commands are listed.
- [x] Tests: fixture journal with every outcome kind + a malformed line; merge-rate math; cache miss → unknown; `--since` filter; CLI and MCP produce the same numbers.
- Validation: full gate; `REG push-report` runs on the real journal without network. Commit `marathon(T4): registry push-report`.

## T5 — M2 (visibility half): evidence-brief coverage and staleness (P2)

Source: `docs/FINDINGS_REGISTRY_MCP.md#M2`. `data/understanding/*.json` (briefs) have `analyzed_at` and `revision`; nothing in `src/` reads them. Do NOT build a generator — only make presence/staleness observable.

- [x] `registry briefs-status [--json]`: for every project with a `repo`, report brief present/absent, `analyzed_at`, age in days, and whether `revision` matches the snapshot's current default-branch head when known (stale/current/unknown). Summary line: N present / M missing / K stale.
- [x] Fold a one-line brief summary into `registry sync-status` output (present/missing/stale counts) so staleness is no longer invisible.
- [x] Document the directory, the fields read, and the ownership decision-still-open in `docs/SCHEMA.md` (observed section) — state plainly that briefs are produced out-of-band today.
- [x] Tests: missing dir, missing brief, malformed JSON, stale vs current revision, age computation with `NOW`.
- Validation: full gate. Commit `marathon(T5): briefs-status and sync-status brief coverage`.

## T6 — push-project SKILL.md fixes S1/S2/S3 (P2)

Source: `docs/FINDINGS_PUSH_PROJECT_SKILL.md` S1, S2, S3. Edit ONLY the repo-tracked copy `.claude/skills/push-project/SKILL.md` (canonical; last changed at `731fd59`). Do not touch `~/.claude/`.

- [x] S1: the `approved` warning (around line 223) must name the surface — MCP requires `approved: true`; CLI `registry record-review` has no flag and applies directly. Two sentences, inline.
- [x] S2: make the evidence-brief read conditional with the stated fallback (README, `CLAUDE.md`/`AGENTS.md`, `docs/`, `git log`).
- [x] S3: add the test-command search order (marker file → `CLAUDE.md`/`AGENTS.md` → `Makefile`/`pyproject.toml`/`package.json` → docs) and require distinguishing a *collection* error from a *test* failure before reporting a red baseline.
- [x] Add the "verify before delegating" sentence to the gear-2 delegation step (the load-bearing finding) and "explain a changed test count, don't just match it" to the verification step.
- [x] Minimal diff: no restructuring, no tone changes, keep every existing rule. `wc -l` delta should be small (≤ +25 lines).
- Validation: `git diff --stat .claude/skills/push-project/SKILL.md` shows only that file; the four edits are present (grep for `record-review`, `fallback`, `collection`, `verify` in the changed hunks). Commit `marathon(T6): push-project skill — S1/S2/S3 and delegation-verification wording`.

## T7 — Dogfood the repo-hygiene marker (P2)

Source: `docs/FINDINGS_REPO_HYGIENE.md` "Proposed marker file" (H1/H2/H4).

- [x] Add `.project-meta.yaml` to THIS repo: `registry_id: project-registry`, `interpreter: "3.11"`, `test: ["PY -m pytest -q"]` (literal `.venv/bin/python -m pytest -q` and the PYTHONPATH fallback form), `validate: ["registry validate"]`.
- [x] Document the convention in `docs/SETUP.md` (or a short `docs/PROJECT_META.md`) as "a starting point, not a settled schema", linking the findings doc. No code reads it yet — say so.
- Validation: `PY -c "import yaml; yaml.safe_load(open('.project-meta.yaml'))"`. Commit `marathon(T7): add .project-meta.yaml marker and document the convention`.

## T8 — Docs drift sweep (P2)

- [x] Compare every command in `CLAUDE.md` cheatsheet, `README.md`, `docs/SETUP.md`, `docs/SCHEMA.md` against `REG --help` and each subcommand's `--help` (including new ones from T1/T4/T5). Fix wrong flags, missing commands, stale counts ("21-tool MCP server" etc. — count the real tools in `mcp/server.py`).
- [x] Check `docs/IMPLEMENTATION_PLAN.md` layout block against the real `src/` tree; update only factual drift.
- [x] Do NOT hand-edit `DASHBOARD.md` (generated) and do not touch `registry/`.
- Validation: full gate. Commit `marathon(T8): docs drift — commands, flags, counts`.

## T9 — Requirement-driven test-gap sweep (P2)

Method from the repo owner's testing rule: extract scenarios from requirements BEFORE writing tests; each requirement → ≥1 test; happy path, edge cases, error handling, state transitions.

- [x] For each story in `docs/USER_STORIES.md` and each rule in `docs/SCHEMA.md`, list its acceptance criteria and find the test that covers each (`grep` test names/asserts). Write the matrix to `docs/codex-marathon/TEST_MATRIX.md` (criterion → test id or GAP).
- [x] Fill GAPs with tests that assert the *requirement* (not current implementation details). If a GAP test fails because the code is wrong, fix the code in a separate commit and say so in PROGRESS; if it reveals a design question, leave the test marked `xfail` with a reason and record it — do not silently adjust the assertion to pass.
- [x] Stop when two passes over the matrix add nothing new.
- Validation: full gate. Commits `marathon(T9): tests for <story ids>` (several allowed).

## T10 — Adversarial self-review of the whole branch (P1, run last before T11)

- [x] Fresh eyes: `git diff origin/main...HEAD --stat`, then review each task's commits as a hostile reviewer. For each: correctness, silent-failure paths (the repo's recurring theme), read-only GitHub invariant (`grep -rn "POST\|PATCH\|DELETE\|PUT" src/` — only the `graphql()` allowlist may reference POST), secrets (`REG validate` already scans; also grep for `gho_`, `ghp_`, `sk-`), test quality (do tests assert requirements or mirror code?), docs consistency.
- [x] Fix real defects with commits `marathon(T10): fix — <what>`. Record each finding + disposition (fixed / not a defect / deferred with reason) in PROGRESS.md. Don't churn style.
- [x] Re-run the T1c smoke once more after all fixes; re-record numbers.
- Validation: full gate; `git log origin/main..HEAD --oneline` reads as a clean per-task history.

## T11 — Final report and stop (P0)

- [ ] Update the PR body: per-task status table, test count before/after (141 → N), smoke numbers, decisions taken (threshold, open questions), blocked items with reasons, anything the owner must decide or do by hand (e.g. merge order, ~/.claude skill copy is stale vs repo copy).
- [ ] Write `## FINAL REPORT` at the end of PROGRESS.md with the same content.
- [ ] `touch .marathon-stop` in the repo root (uncommitted) so the runner exits.
- Validation: PR body updated (`gh pr view --json body | grep -c 'T1'` ≥ 1); `.marathon-stop` exists.

---

## Discovered (append here; same format; bounded, verifiable, in-scope only)

(empty)
