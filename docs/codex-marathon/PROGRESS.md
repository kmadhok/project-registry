# Codex marathon progress

[2026-08-21T19:54:30Z] START T0 attempt=1 base=64c1394e145f6cabd0dfd0be2fa162aac5dddf28
[2026-08-21T19:54:30Z] CHECKPOINT T0 fetched origin and created codex/marathon-2026-08-21 from origin/main at 64c1394e145f6cabd0dfd0be2fa162aac5dddf28
[2026-08-21T19:54:49Z] CHECKPOINT T0 verified the required seed set, executable runner, and ignore rules; protected untracked transcript and proposal remain untouched
[2026-08-21T19:55:41Z] CHECKPOINT T0 pushed the branch and opened draft PR https://github.com/kmadhok/project-registry/pull/4
[2026-08-21T19:55:41Z] CHECKPOINT T0 full gate passed: pytest collected and passed 213 cases; registry validate returned 0 errors and 3 suggestions
[2026-08-21T19:55:41Z] CHECKPOINT T0 queue baseline says 141 passed, but the recorded origin/main base collects 213 cases; T0 changed no tests, so this is a baseline-count discrepancy rather than a test-count change caused by the task
[2026-08-21T19:55:41Z] DONE T0 commits=6164f49 tests=213 passed
[2026-08-21T19:56:23Z] START T1a attempt=1 base=f3cfd55a5bc016c09ed73c77884128bdd5e83eaa
[2026-08-21T19:57:27Z] CHECKPOINT T1a read all 674 lines of BRANCH_CAPTURE_SPEC.md plus snapshot.py, client.py, sync.py, tests/conftest.py, and the existing client invariant in test_sync.py before code edits
[2026-08-21T19:57:27Z] CHECKPOINT T1a live GraphQL first:2 probe matched Rev 3 and returned non-null committedDate on both nodes; totalCount is now 1251 (up from the spec's 802 and above its 1000-node default ceiling)
[2026-08-21T19:57:27Z] CHECKPOINT T1a requirement scenarios enumerated before tests: Branch full/null/unicode round-trips; old RepoState defaults; malformed/future dates; GraphQL read openings; mutation/subscription/comment-obfuscated refusal before transport; GraphQL errors; cursor pagination and ceiling partial; malformed branch node including missing committedDate is skipped and counted
[2026-08-21T19:57:27Z] CHECKPOINT T1a spec deviation to carry into Rev 4: §4.2.3 permits missing committedDate as an unknown date, but QUEUE T1a explicitly requires such a node to increment branches_skipped so the REST-style silent-zero regression cannot recur; the stricter queue requirement wins
[2026-08-21T19:59:31Z] CHECKPOINT T1a Branch/RepoState snapshot fields, guarded GraphQL transport, paginated refs client, and tolerant GraphQL-node parser are implemented; targeted snapshot/client/sync suites pass 46 cases
[2026-08-21T20:00:24Z] CHECKPOINT T1a parser helpers assigned to §4.3 landed early so the queue's missing-committedDate regression has a causal test in T1a; T1b will wire them into sync
[2026-08-21T20:00:24Z] CHECKPOINT T1a the implemented client completed a live one-page read: nodes=100, partial=true, total=1251, missing_dates=0
[2026-08-21T20:00:24Z] CHECKPOINT T1a full gate passed: 233 tests passed (20 more than the measured T0 baseline, all from 2 new requirement-driven test modules); registry validate returned 0 errors and 3 suggestions
[2026-08-21T20:00:37Z] DONE T1a commits=cb4ea73 tests=233 passed
[2026-08-21T20:01:58Z] START T1b attempt=1 base=f5745e495615bb5ecc7e5c01f8ab8b852e62db8c
[2026-08-21T20:02:11Z] CHECKPOINT T1b read all 674 spec lines and inspected sync, signals, CLI, queries, dashboard, MCP handlers, fixtures, and existing sync/signal/CLI/query/MCP tests before code edits
[2026-08-21T20:02:11Z] CHECKPOINT T1b requirement scenarios enumerated before tests: full/sorted fetch and PR mapping; partial ceiling; GraphQL failure carry-forward; disabled carry-forward; malformed skip count; later success clears error; threshold boundary/future/unknown/default/open-PR exclusions; one repo item and group summary; mixed-item sorting; CLI no-branches/show warning/JSON; query, dashboard, and MCP references never render #None
[2026-08-21T20:03:35Z] CHECKPOINT T1b sync now threads previous state, captures or carries branches with partial-error accounting, and exposes cost control on CLI/MCP; shared classifier/rule/reference rendering is wired through CLI, queries, dashboard, and MCP; modules compile and the 20 T1a snapshot/client tests still pass
[2026-08-21T20:05:31Z] CHECKPOINT T1b requirement tests added across sync, signals, queries, CLI/dashboard, and MCP; 144 targeted cases pass and git diff --check is clean
[2026-08-21T20:05:57Z] CHECKPOINT T1b full gate passed: pytest collected and passed 251 cases; registry validate returned 0 errors and 3 suggestions; the 18-test increase from T1a is the requirement coverage added for sync failure/partial/opt-out states, branch classification, and CLI/dashboard/MCP rendering
[2026-08-21T20:05:57Z] CHECKPOINT T1b registry rules lists stale_branches and sync --help exposes both --no-branches and --no-details
[2026-08-21T20:06:18Z] DONE T1b commits=b20d090 tests=251 passed
[2026-08-21T20:07:00Z] START T1c attempt=1 base=c07fb763f5077768f461bd5bd50bfa5cf3b2f62a
[2026-08-21T20:07:00Z] CHECKPOINT T1c read PURPOSE.md and SCHEMA.md plus the already-reviewed full branch spec and implementation surfaces before edits; T1a's live totalCount=1251 proves the 1000-branch default ceiling would make the required smoke partial, so Q4 will use a branch-specific 5000-node ceiling plus the T1b visible warning while leaving REST pagination at 10 pages
[2026-08-21T20:08:52Z] CHECKPOINT T1c read-only live sync succeeded: 1251 branches, branches_fetched=true, branches_partial=false, branches_skipped=0, 1 open-PR head, 13 inferred GraphQL pages at 100 nodes/page, started 20:07:47.421Z and completed 20:08:52.417Z (64.996s); the old ~9-page/~6s estimate is stale because the repo grew from 802 to 1251 branches
[2026-08-21T20:09:20Z] CHECKPOINT T1c branch-age histogram at the completed_at timestamp: 0-6d=161, 7-13d=167, 14-29d=374, 30-44d=342, 45-59d=202, 60-74d=4, 75-89d=1, 90+d=0 (all branches; eligible differs only by excluding 2 branches total). Threshold counts among 1249 eligible branches: >=45d 207, >=60d 5, >=70d 1, >=90d 0
[2026-08-21T20:09:20Z] CHECKPOINT T1c threshold decision: use 60 days because the observed distribution has a sharp cliff after day 59 (202 branches at 45-59d but only 5 at 60+d), producing a bounded triage signal while 90 days would falsely make the implemented rule look inactive on this repository
[2026-08-21T20:10:08Z] CHECKPOINT T1c live cached queries after the threshold change show exactly one stale_branches attention item with 5 stale branches (3 pc/, 1 claude/, 1 feat/), oldest 75d; registry show interview-prep renders 1251 branches (5 stale, 1 open-PR heads)
[2026-08-21T20:10:08Z] CHECKPOINT T1c Rev 4 answers all five open questions and records implementation deviations; SCHEMA and PURPOSE now describe observed branch evidence and the implemented attention rule
[2026-08-21T20:10:08Z] CHECKPOINT T1c full gate passed: pytest collected and passed 253 cases; registry validate returned 0 errors and 3 suggestions; the 2-test increase from T1b covers the data-derived 60-day default and the branch-specific pagination ceiling without changing REST pagination
[2026-08-21T20:10:25Z] DONE T1c commits=fb333c0 tests=253 passed
