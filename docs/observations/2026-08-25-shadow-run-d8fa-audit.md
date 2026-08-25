# Shadow run observation — 2026-08-25 — `20260825T120035Z-pc-d8fa`

Read-only audit of the first scheduled shadow run against a real focus repo.
Facts only; conclusions went into ADR-006 AD-10–AD-12, `docs/RUNBOOK.md`, and
the 2026-08-25 skill/guard changes.

| Item | Value |
|---|---|
| Run id | `20260825T120035Z-pc-d8fa` |
| Project / repo | `interview-prep` / `kmadhok/interview-prep` |
| Mode | `automation.mode: shadow` (`candidate_selected.detail.dry_run: true`) |
| Journal lines | `data/build/runs.jsonl` 56–92 as of registry `main` `c46cac8` |
| Journal host label | `pc` |
| Paths in journal | `/home/ubuntu/build-work/<run-id>` (guard_denied detail) — not the WSL PC path |
| Host reachability at audit | Windows PC: Tailscale last seen 10 d earlier; Linux instance `100.64.146.103` refused SSH as `ubuntu` (tailnet policy) |
| Duration | 12:00:35Z → 12:52:06Z (51.5 min) |
| Chunks started | 6: `bootstrap_contract`, `plan`, `3`, `4`, `6`, `plan2` |
| Outcome | `run_finished` `shadow_completed` |
| Merges / tags | 0 `merged` events; `git ls-remote --tags` on the target: 0 |
| Writeback phase 1 | registry commit `c46cac8` (`DASHBOARD.md`, `data/build/runs.jsonl`, `data/build/state.json`) |
| Writeback phase 2 | no `writeback_confirmed` event and no "confirm writeback" commit on `main` (prior runs `f90f`, `b126` have both) |
| Digest | `data/build/digests/20260825T120035Z-pc-d8fa.md` absent from `main`; `.gitignore:9 build/` matched the path (`git check-ignore -v`) |
| `state.json` after | `interview-prep`: `last_outcome: shadow_completed`, `chunks_merged_total: 0`, `consecutive_failures: 0`, `paused_reason: null` |
| Registry curated files in writeback | none |
| Proposals / audit entries dated 2026-08-25 | none |
| `build-report` journal check | 92 lines, 92 valid, 0 malformed |

## Pull requests

| PR | Chunk | Head | Base merge-base | Commits ahead of `main` | Journal verdict | GitHub reviews |
|---|---|---|---|---|---|---|
| #11 | `bootstrap_contract` | `12f3cca` | `6797e2d` | 1 | `approve`, 1 risk flag | Codex connector bot only (3 inline: network, `Application Profile.md`, `Work Artifacts/`) |
| #12 | `plan` | `71be1da` | `6797e2d` | 1 | `approve` | bot only (4 inline) |
| #13 | `3` | `70a9eb8` | `6797e2d` | 2 (contains #12) | `approve`, `classes_seen: none` | bot only (1 inline) |
| #14 | `4` | `ac61e67` | `6797e2d` | 3 (contains #12, #13) | `approve` | bot only (3 inline) |
| #15 | `6` | `a613f19` | `6797e2d` | 5 (contains #12–#14 + `5a5d675`) | `approve` after an unjournaled `request_changes` | bot only (3 inline, on `5a5d675`) |
| #16 | `plan2` | `36ed928` | `6797e2d` | 6 (contains #12–#15) | `approve` | bot only (4 inline) |

- Ancestry (`git merge-base --is-ancestor`): #12 ⊂ #13 ⊂ #14 ⊂ #15 ⊂ #16; #11 independent.
- `git merge-tree --write-tree origin/main <head>`: all six clean against `f0f23bf`; GitHub `mergeable: MERGEABLE`, `mergeStateStatus: CLEAN` for all six.
- #16's SPEC "Verified-missing" lines cite "clone at `a613f19`" (the #15 head).
- PR #7 (`cursor/job-agent-steal-list-fd65`, draft) untouched: `updatedAt 2026-07-31T01:51:00Z`.
- Reviewer verdict JSON is not present on any PR (journal only).

## Journal sequence per chunk

```
bootstrap_contract: chunk_started > verify_passed > [guard_denied non_push_branch] > [guard_denied guard_error] > pr_opened > review_verdict:approve > chunk_skipped > contract_bootstrapped
plan:               chunk_started > verify_passed(note: plan-only) > pr_opened > review_verdict:approve > chunk_skipped
3:                  chunk_started > verify_passed > pr_opened > review_verdict:approve > chunk_skipped
4:                  chunk_started > verify_passed > pr_opened > review_verdict:approve > chunk_skipped
6:                  chunk_started > verify_passed > pr_opened > verify_passed(note: addressed request_changes) > review_verdict:approve > chunk_skipped
plan2:              chunk_started > verify_passed(note: plan-only) > pr_opened > review_verdict:approve > chunk_skipped
```

- No `verify_passed --detail baseline=green` event (Phase 3) in the run.
- No `review_verdict` with `verdict: request_changes` in the run; `run_finished.detail.summary` says "1 approve-after-request_changes".

## Guard denials (journal lines 60–61) and local reproduction

Reproduced with the `origin/main` `scripts/build-guard.py` and a disposable lease (`run_id` = this run, target clone on the run's branch):

| Journaled command | Rule | Reproduced | Passes when |
|---|---|---|---|
| `cd <workdir> && git push -u origin "push/<run>-bootstrap_contract-bootstrap-build-contract" 2>&1` | `non_push_branch` | yes (exit 2) | `2>&1` removed → exit 0 |
| `cd <workdir> && gh pr create --head "push/<run>-…" --title "…" --body "$(cat <<'EOF' … EOF )"` | `guard_error: unclosed shell quote or escape` | yes (exit 2) | `--body-file <path>` → exit 0 |

PR #11 was created at 12:05:31Z, after both denials; the run finished `shadow_completed`.

## Independent verification of the PR code

From `git archive` exports, `python 3.11` venv, `pytest -q scripts/drip_runner scripts/ci`:

| Tree | Result |
|---|---|
| `origin/main` (`f0f23bf`) | 97 passed |
| #13 head | 97 passed (branch body claims 92→97; `main` already had 97 before the run's merge-base moved) |
| #14 head | 103 passed |
| #16 head (cumulative) | 108 passed |

Executed checks on `scripts/drip_runner/pipeline_health.py` at #16 head:

- `report(text, heartbeat=hb)` with default `now=None` → `AttributeError: 'NoneType' object has no attribute 'strftime'`.
- All-tripped heartbeat + `last_alert` 4 h earlier → last line `No threshold breaches.`
- `drafts_awaiting_send(test fixture)` → 2, counting the fixture row `Capgemini — Applied + outreach sent`.
- No `sys.stdout.reconfigure` in `pipeline_health.py`; `watchdog.py:231` has one.

`registry validate-contract` on #11's `.project-meta.yaml`: `ok: true, runnable: true, findings: []`.
`registry validate-spec interview-prep` on #12's SPEC: `next_ready_index: 3`; on #16's SPEC: `next_ready_index: 5`.
