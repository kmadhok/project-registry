---
name: build-observe
description: This skill should be used when the owner invokes "/build-observe <owner/repo> [--keep]" to exercise the autonomous builder against a disposable mirror in shadow and build modes while proving that no real project was touched.
---

# build-observe

Run the autonomous builder only against a disposable GitHub mirror, capture
facts in the repository's observation format, and tear the mirror down. Treat
every safety assertion as a hard gate: abort immediately when one fails.

Accept exactly `/build-observe <owner/repo> [--keep]`. Reject
`kmadhok/project-registry` and any source whose repository name already starts
with `build-sandbox-`. Derive `<name>` from the source repository's exact final
path component. Set the sandbox project id and repository name to
`build-sandbox-<name>`.

## Invariants

- Apply the rule “never run bare” to `/push-project`; invoke it only with the
  sandbox project id.
- Never merge, close, edit, or otherwise mutate a PR outside the sandbox.
- Never target `kmadhok/project-registry` with the builder.
- Never construct or guess a registered repository name. Read project ids from
  `registry build-queue --json`, then read the exact `repo` and
  `automation.mode` fields with `registry show <project-id> --json` when the
  queue response does not contain those fields.
- Abort on a failed baseline, validation, queue, selection, guard, provenance,
  or post-run comparison check.
- Run all scratch clones under a fresh temporary directory. Do not reuse a
  prior builder worktree.

## 0. Capture the safety baseline

Run `.venv/bin/registry build-queue --json`. From that response, enumerate all
project ids, resolve their exact records with
`.venv/bin/registry show <project-id> --json`, and retain only projects whose
`automation.mode` is `build` or `shadow`. Never derive a repository name from
a project id.

For every retained repository and for `kmadhok/project-registry`, record:

1. The count of all PRs whose `headRefName` starts with `push/`.
2. Every open PR's number, URL, and head branch.

Use `gh pr list --repo <exact-repo> --state all` for the count and a separate
`--state open` query for the open-PR inventory. Save the structured baseline in
the temporary directory for the final byte-for-byte semantic comparison.

## 1. Create the mirror

Create a fresh temporary directory. Mirror-push the exact source repository:

```bash
git clone --mirror https://github.com/<owner>/<repo>.git <tmp>/source.git
gh repo create kmadhok/build-sandbox-<name> --private
git -C <tmp>/source.git push --mirror https://github.com/kmadhok/build-sandbox-<name>.git
```

Record the source default-branch SHA and confirm the sandbox default branch has
the same SHA. Abort and delete the sandbox repository if mirror verification
fails.

## 2. Register the sandbox paused

Write `registry/projects/build-sandbox-<name>.yaml` directly with this complete
shape, substituting only facts named below:

```yaml
id: build-sandbox-<name>
name: build-sandbox-<name>
purpose: Disposable mirror of <owner/repo> used to observe the autonomous builder without applying changes to a real project.
desired_outcome: The shadow and build runs complete with their expected PR, review, merge, tag, digest, and safety outcomes recorded.
lifecycle: incubating
active: false
category: tooling
repo: kmadhok/build-sandbox-<name>
visibility: private
tags:
  - sandbox
  - builder
  - disposable
brief:
  done_criteria:
    - A shadow run leaves its reviewed PR open and a build run merges a reviewed PR, creates a checkpoint tag, and writes a digest.
  non_goals:
    - Advancing or merging work in the source repository or any other registered project.
  constraints:
    - Run the builder only by the exact sandbox project name with --force-named.
automation:
  mode: shadow
  paused: true
  allow:
    - dependencies
    - ci
    - generated_data
```

Do not add `next_action`. Run `.venv/bin/registry validate` and require zero
errors. Run `.venv/bin/registry build-queue --json` and require the sandbox to
appear with state `paused`.

Run the safety selection check `.venv/bin/registry build start --host mac
--json`. This registry command is the sole exception to the “never run bare”
builder invariant above. Assert that its returned candidate is not the sandbox;
if it returns the sandbox, stop immediately and do not run `/push-project`.
If the check leases another candidate, finish that lease immediately with
outcome `stopped` before proceeding, and record this fact.

Commit only the new sandbox YAML. Do not push the registry commit as part of
registration.

## 3. Run the named chain

Launch an Agent subagent and instruct it to invoke exactly:

```text
/push-project build-sandbox-<name> --force-named
```

Run it once while the policy is `shadow`. Require a PR to be opened, the
reviewer verdict to be recorded in its body, and the PR to remain unmerged.
Require the lease to have `dry_run: true` and the guard record for a denied
merge to name rule `shadow_mode`.

After the shadow run has finished and released its lease, flip only
`automation.mode` through the owner-operated proposal workflow:

```bash
.venv/bin/registry propose build-sandbox-<name> \
  --set automation.mode=build \
  --rationale "build-observe owner-operated sandbox promotion" --json
.venv/bin/registry proposal-apply <proposal-id> --approve --json
```

Confirm the YAML now says `mode: build` and remains `paused: true`. Commit the
approved proposal changes. Launch a new Agent subagent with the same exact
named `/push-project ... --force-named` invocation. Require an approved PR to
merge, a checkpoint tag to exist, and the run digest to exist. Record the PR,
merge SHA, tag, digest, and registry run id.

## 4. Record facts

Write `docs/observations/<date>-build-sandbox-<name>.md` using the structure of
the existing observation files:

- A provenance table with parent session, both subagent transcripts, exact
  model ids, source and sandbox SHAs, PR URLs, merge SHA, checkpoint tag,
  digest, and registry commits.
- Run facts for shadow and build separately.
- Invariant checks, including paused queue state, bare-selection result,
  `dry_run`, shadow merge denial, named invocations, and PR scope.
- Deviations reported by either subagent.

Record facts only. Do not infer causes or recommendations in the observation.

## 5. Prove real repositories were untouched

Repeat Step 0 using a fresh `registry build-queue --json` response and fresh
`registry show --json` records. Compare the `push/` PR counts and complete open
PR inventories with the saved baseline for every real `build` or `shadow`
project and `kmadhok/project-registry`. Require no differences. Treat the
sandbox as the only allowed exclusion. Record the comparison in the invariant
table and abort teardown-on-success reporting if any real repository changed.

## 6. Tear down

Unless `--keep` was supplied:

1. Delete only `registry/projects/build-sandbox-<name>.yaml`.
2. Run `.venv/bin/registry validate` and require zero errors.
3. Commit only the teardown and observation changes.
4. Run `gh repo delete kmadhok/build-sandbox-<name> --yes`.
5. Confirm the repository no longer resolves.

With `--keep`, retain both the YAML and repository and record that explicit
operator choice in the observation. Never delete the source repository.
