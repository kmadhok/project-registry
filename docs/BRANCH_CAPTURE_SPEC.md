# Spec: Branch capture in the observed GitHub layer

Status: **Revision 3 — GraphQL data source; resolves the Rev-2 blocker**
Owners: code change to `project-registry` itself (not registry data)
Related: `docs/PURPOSE.md` (operating model), `docs/SCHEMA.md` (curated schema — unchanged)

## Revision history

| Rev | Change |
|---|---|
| 1 | Initial draft. Review verdict: **needs-rework** (2 blockers, 7 majors). |
| 2 | Fixes: truncation probe + `BranchListTruncated` (B1); `previous` threading + in-function branch error handling + `SyncResult.partial_errors` (B2, M3); repo-level item construction without `candidate.age_days` (M5); in-repo-only PR-head mapping (M4); tolerant element parsing + `branches_skipped` (M6); shared `classify_branches` helper (M7); `_signal_ref` helper for `#None` rendering (M8, minors); `branches_fetched_at`; `open_pr_numbers` list; `>=` boundary; reworded rule description; consistent example numbers; declared `tests/test_snapshot.py`; monkeypatch-based CLI test; back-compat wording; curl contract check in §7. |
| 3 | **Blocker fix:** the Rev-2 §4.2 response contract was verified against the live API and is **false** — REST `/branches` returns no commit date, so `committed_at` would always be `None` and the rule would silently emit zero items (§1.1). Data source moves to the **GraphQL `refs` connection**, which returns name + SHA + `committedDate` + protection + associated PRs in the same page count (measured: 802 branches, 9 pages, 6.3s, 9 rate-limit points). Consequences: new `graphql()` transport requiring **POST**, which collides with the `ALLOWED_METHOD = "GET"` invariant — resolved by a read-only-verb allowlist, not by weakening the constraint (§4.2); truncation probe replaced by `pageInfo.hasNextPage` (§4.2); PR-head mapping simplified (§4.3); `branches_partial` replaces silent truncation. |

## 1. Background

The registry treats a repository as the unit of observation, but repositories
are not always the unit of intention. `kmadhok/interview-prep` has **802 remote
branches** (measured 2026-08-02; 801 an hour earlier — the count moves): ~11
carry distinct intentions (`push/spec`, `productionalized_version`,
`worktree-two-orchestrator-split`, `pc/*`, `feat/verify-emails-pipeline`, …) and
the rest are transient agent-checkpoint noise (`claude/relaxed-noether-*`). Today:

- The GitHub snapshot records only `default_branch`; no branch list is fetched.
- `PURPOSE.md:24` promises "stale branches" among the live work needing
  attention, but no code implements that signal.
- Branch intention surfaces in the context layer only by accident — via an open
  PR title, or a human pasting a URL into `notes:` during review.

This spec adds **observation** of branches: a complete, timestamped branch list
per repository in the GitHub snapshot, plus a repo-level staleness signal. It
does **not** add any curated branch intent — deciding which branches matter
remains a human decision in the curated layer.

### 1.1 Why Rev 2 could not ship (the finding that drove Rev 3)

Rev 2 §4.2 asserted that each REST `/repos/{o}/{r}/branches` element carries
`commit.commit.committer.date`, and honestly labelled it "an assumption to be
verified during implementation." It was verified. It is false:

```
GET /repos/kmadhok/interview-prep/branches?per_page=2   (authenticated)
  element keys      : ["commit", "name", "protected"]
  element["commit"] : {"sha": …, "url": …}      ← no nested commit object, no date
```

The `url` is a *link to* the commit resource, not the commit body. Same shape on
public repos, so this is not a permissions artifact.

The failure this produces is silent, which is what makes it a blocker rather
than a bug. Trace Rev 2's own stale predicate: it requires
`committed_at is not None`. With the REST source that is *always* `None`, so
every branch fails the test, `counts.stale == 0`, `_stale_branches` returns
`None`, and the queue gets zero items — while `branches_fetched` is `True` (the
fetch really did succeed), `branches_skipped` is `0` (the tolerant parser treats
a missing date as acceptable), and every Rev-2 §6 test still passes because they
construct `Branch` objects with dates already filled in. The result is
indistinguishable from "this repo has no stale branches."

That is exactly the "unknown must never read as known" failure Goal 5 exists to
prevent, entering through the one door Rev 2 left unguarded.

**Recovering the dates from REST costs one call per branch** (`commit.url`, or
`/commits?sha={branch}&per_page=1`). Measured: 0.54s per branch sequentially →
**~431s (7 minutes) and 811 rate-limit units for one repository.** That breaks
the Goal-3 cost promise and would have reopened the §3 non-goal boundary.

**GraphQL returns the dates for free.** The `refs` connection carries name, SHA,
`committedDate`, protection, and associated PRs in one node. Measured against
the real repository:

| Approach | Calls | Wall time | Rate-limit cost | Dates? |
|---|---|---|---|---|
| REST list only (Rev 2 plan) | 9 | ~2s | 9 core | **no** |
| REST list + per-branch commits | 811 | **~431s** | 811 core | yes |
| **GraphQL `refs` (Rev 3)** | **9** | **6.3s** | **9 graphql** | **yes** |

Full-repository GraphQL run: `pages=9 branches=802 totalCount=802 elapsed=6.26s`,
`nodes missing date: 0`. Per-page `rateLimit.cost` is **1**.

So Rev 3 keeps the Rev-2 cost promise (one paginated read per repository) *and*
gets the field that makes the rule mean anything. The price is a new transport
verb, which §4.2 addresses head-on.

## 2. Goals

1. The snapshot records every remote branch: name, head SHA, last-commit time,
   protection flag, default-branch marker, and any open in-repo PR whose head it is.
2. Each branch list is timestamped independently of the repo entry, so
   carried-forward branch data never reads as freshly observed.
3. Sync is one extra paginated read per repository (GraphQL `refs`, ~9 requests
   and ~9 rate-limit points for the 802-branch worst case), skippable for cost
   control.
4. A new attention rule, `stale_branches`, emits **one item per repository**
   summarizing stale branches — never one item per branch.
5. The data is clearly observed: partial, truncated, or failed fetches are
   marked, and an unknown value never reads as a known one (the existing
   "unknown ≠ passing" philosophy, extended to branches).
6. All operating rules hold: **read-only** GitHub access, writes only under
   `data/`, zero curated-field changes, zero GitHub mutations.

Goal 6 is deliberately reworded from Rev 2's "GET-only." §4.2 explains why the
*intent* (no mutation is reachable) is preserved while the *mechanism* changes.

## 3. Non-goals (explicitly out of scope for this change)

- **Per-branch merge detection** — deferred, see §8.1. GraphQL makes this
  cheaper than Rev 2 assumed, but it is still additional scope.
- **Curating branch intentions** — whether `productionalized_version` or
  `worktree-two-orchestrator-split` deserve registry entries/relationships is a
  human decision in the curated layer; this change only makes the evidence visible.
- **Branch deletion or any GitHub mutation** — the client stays read-only,
  enforced by the verb allowlist and the HTTP-verb test (§4.2).
- **Migrating other resources to GraphQL** — PRs, issues, and repo metadata keep
  their existing REST paths. This spec adds one GraphQL query, not a rewrite.
- **Changes to `data/understanding/*` briefs** — a separate offline process; may
  consume the new snapshot fields later.

## 4. Design

### 4.1 Data model — `src/project_registry/github/snapshot.py`

New dataclass:

```python
@dataclass
class Branch:
    name: str
    head_sha: str
    committed_at: dt.datetime | None = None   # committer date of head commit; None = unknown
    protected: bool = False
    is_default: bool = False                  # name == RepoState.default_branch
    open_pr_numbers: list[int] = field(default_factory=list)
    # open in-repo PRs whose head ref == name. A list, not a scalar: stacked or
    # duplicate PRs can share one head ref. Empty = no open PR evidence.
```

`RepoState` additions (all defaulted → existing snapshots load unchanged):

```python
branches: list[Branch] = field(default_factory=list)
branches_fetched: bool = False          # True only when the list fetched completely
branches_fetched_at: dt.datetime | None = None   # when this branch list was actually observed
branches_skipped: int = 0               # malformed nodes dropped during parse (§4.2)
branches_partial: bool = False          # pagination ceiling hit; list is incomplete
branches_error: str | None = None
```

`branches_partial` is new in Rev 3 and replaces Rev 2's probe-based
`BranchListTruncated` exception. Rationale in §4.2.

`Branch.from_dict`/`to_dict` are symmetric with `PullRequest`/`Issue` and
consume the **flat serialized form** (`committed_at` ISO or null,
`open_pr_numbers` list). Parsing the **nested GraphQL node shape** is a separate
function `branch_from_node` in `sync.py` (§4.3) — the two shapes must not be
confused.

Backward compatibility: `RepoState.from_dict` already reads every field through
`raw.get(...)` (snapshot.py:178-195), so a dict without the new keys loads with
defaults applied. Re-saving adds only the new defaulted keys; new files remain
readable by older parsers (unknown keys are ignored). This is semantic
preservation, not byte-identity.

### 4.2 Client — `src/project_registry/github/client.py`

#### 4.2.1 The verb collision, stated plainly

`client.py:22` declares `ALLOWED_METHOD = "GET"`, `get()` hard-wires it at
line 61, and `tests/test_sync.py:82-84` greps the module source and asserts
`{verbs found} <= {"GET"}`. The module docstring (client.py:1-8) explains why:
this is where MCP-006 ("no merge, delete, archive, visibility-change, or
issue-closing tools") is enforced *at the bottom of the stack rather than only
at the tool layer*.

GitHub's GraphQL endpoint requires **POST**. Verified — an authenticated GET to
`/graphql?query=…` returns HTTP 200 but **ignores the query and returns the
introspection schema**; only POST executes the query:

```
GET  /graphql?query={viewer{login}}  → 200  {"data":{"__schema":{…}}}   ← query ignored
POST /graphql  -d '{"query":"{viewer{login}}"}' → 200  {"data":{"viewer":{"login":"kmadhok"}}}
```

So there is no GET-shaped way to reach GraphQL. This is a real architectural
collision and must not be papered over.

#### 4.2.2 Resolution: allowlist read-only verbs, keep mutation unreachable

The invariant that matters is **"no code path can mutate GitHub state,"** not
literally "the string GET." A POST to `/graphql` carrying a `query` (never a
`mutation`) is a read. Rev 3 therefore replaces the single constant with an
explicit allowlist and a guarded GraphQL method:

```python
#: The only HTTP verbs this client is permitted to use. POST appears solely to
#: reach GitHub's GraphQL read endpoint, which rejects GET; `graphql()` is the
#: only caller and it refuses anything but a read operation.
ALLOWED_METHODS = frozenset({"GET", "POST"})
ALLOWED_METHOD = "GET"          # retained: REST default, and existing callers

GRAPHQL_PATH = "/graphql"


class GraphQLError(GitHubError):
    """A GraphQL response carried an `errors` array."""


def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
    """POST a read-only GraphQL query. Mutations are structurally refused."""
    if _is_mutation(query):
        raise GitHubError("graphql(): only read queries are permitted")
    ...
    request = urllib.request.Request(url, data=body, method="POST")
    ...
    payload = json.loads(...)
    if payload.get("errors"):
        raise GraphQLError(f"graphql: {payload['errors'][0].get('message')}")
    return payload.get("data") or {}


def _is_mutation(query: str) -> bool:
    """True unless the operation is unambiguously a read.

    Fails closed: strips leading whitespace/comments, then requires the document
    to start with `{`, `query`, or `fragment`. Anything else — `mutation`,
    `subscription`, or unparseable — is refused.
    """
```

`_is_mutation` **fails closed**: it allowlists read openings rather than
blocklisting the word `mutation`, so an obfuscated or novel operation is refused
by default rather than passed through.

**The verb test must be strengthened, not relaxed.** `tests/test_sync.py:82-84`
currently asserts `verbs <= {"GET"}`. Rev 3 replaces it with a stricter pair:

1. `verbs <= {"GET", "POST"}`, **and** every `method="POST"` occurrence in the
   module lies inside the `graphql` function body (assert by locating the
   function's source segment via `inspect.getsource`).
2. A behavioural test: `client.graphql('mutation { deleteRef(...) { __typename } }')`
   raises, and no request is issued (recording fake asserts zero calls). Also
   assert refusal for `subscription {...}` and for a leading-comment-obfuscated
   mutation.

This yields *stronger* enforcement than Rev 2: previously "no mutation" rested
on the verb alone; now it rests on the verb allowlist **plus** an operation-kind
check with a test proving mutations are rejected before transport.

**Rejected alternatives**, recorded so this is not relitigated:
- *Keep REST, fetch dates per branch* — 7 minutes and 811 units per repo (§1.1).
  Breaks Goal 3.
- *Keep REST, ship without dates* — the rule has no meaning; `pushed_at` is
  repo-level, so no per-branch age exists. This is the silent no-op of §1.1.
- *Bound the date fetch to small repos* — `interview-prep` is the motivating
  repo and would be exactly the one excluded.
- *Separate GraphQL client class* — duplicates auth/timeout/error handling; the
  guard belongs next to the invariant it protects.

#### 4.2.3 The branch query

```python
BRANCHES_QUERY = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    refs(refPrefix:"refs/heads/", first:100, after:$cursor) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        branchProtectionRule { id }
        target {
          ... on Commit {
            oid
            committedDate
            associatedPullRequests(first:10) {
              nodes { number state headRepository { nameWithOwner } }
            }
          }
        }
      }
    }
  }
}
"""

def list_branch_nodes(self, full_name: str) -> tuple[list[dict], bool, int]:
    """Return (nodes, partial, total_count).

    `partial` is True when the pagination ceiling was reached with
    `hasNextPage` still true — the list is incomplete and must never read as
    complete (Goal 5).
    """
```

Pagination uses `pageInfo.hasNextPage` / `endCursor`, bounded by `max_pages`.
**This removes Rev 2's probe hack entirely**: `paginate()` stopped silently at
the ceiling, so Rev 2 needed an extra call past the end to detect truncation.
GraphQL states `hasNextPage` explicitly, so truncation is a returned fact rather
than an inferred one — and `totalCount` gives an independent cross-check.

Ceiling headroom: `100 × max_pages(10) = 1000`. At 802 branches `interview-prep`
sits at **80%** and climbing with every agent checkpoint. §7 requires confirming
`branches_partial is False` on the smoke run, and §9.4 raises the ceiling
question.

**Verified node contract** (measured, not assumed — this is what Rev 2 lacked):
`name` (String!), `branchProtectionRule` (null when unprotected),
`target.oid`, `target.committedDate` (ISO-8601),
`target.associatedPullRequests.nodes[].{number,state,headRepository.nameWithOwner}`.

**Tolerant parse:** a node is malformed when `name` is empty, or `target` is
absent/lacks a non-empty `oid`. `target` is legitimately absent when a ref points
at a non-Commit object (the `... on Commit` inline fragment yields `null`) —
rare for `refs/heads/`, but handled rather than assumed away. Malformed nodes are
dropped and counted in `branches_skipped`: never a crash, and never a partial
list that looks complete.

### 4.3 Sync — `src/project_registry/github/sync.py`

Signatures (both change; callers are `sync()` and tests):

```python
def fetch_repo_state(
    client: GitHubClient,
    full_name: str,
    now: dt.datetime,
    with_details: bool = True,
    with_branches: bool = True,
    previous: RepoState | None = None,   # previous snapshot entry, for carry-forward
) -> RepoState:

def sync(
    registry, client, paths=None, repos=None, now=None,
    with_details=True, with_branches=True, write=True,
) -> SyncResult:
```

`sync()` already loads `previous = load_snapshot(paths)` (sync.py:83) and
`Snapshot.get` handles case-insensitive lookup (snapshot.py:234-240), so
threading is `previous.get(full_name)` into the call at sync.py:93.

**Branch fetch** (inside `fetch_repo_state`, after repo metadata and open PRs):

1. If `with_branches` is False: copy `previous.branches` (if any) and
   `previous.branches_fetched_at`, leave `branches_fetched=False`,
   `branches_error=None`, and return.
2. Call `client.list_branch_nodes(full_name)` → `(nodes, partial, total_count)`.
3. Build branches via `branch_from_node(node, default_branch) -> Branch | None`
   (separate from `Branch.from_dict`; parses the GraphQL shape, sets
   `is_default`, maps `branchProtectionRule is not None` → `protected`).
   `None` results increment `branches_skipped`.
4. Map open PR heads **from the node itself** — no separate correlation pass:

   ```python
   for pr in (target.get("associatedPullRequests") or {}).get("nodes") or []:
       if pr.get("state") != "OPEN":
           continue
       head_repo = (pr.get("headRepository") or {}).get("nameWithOwner")
       # Fork PRs carry the fork's nameWithOwner and are excluded. None appears
       # in degraded responses (deleted fork) and is treated as in-repo only
       # when the ref matched this repository's branch, which it did by
       # construction — the PR is attached to this ref.
       if head_repo is None or head_repo == full_name:
           branch.open_pr_numbers.append(int(pr["number"]))
   ```

   This is simpler and more accurate than Rev 2 §4.3.4, which correlated
   `state.pull_requests` head refs by name. `associatedPullRequests` is attached
   to the ref by GitHub, so a deleted head branch simply has no ref to attach to,
   and stacked PRs sharing a ref appear as multiple nodes. `state` is filtered
   explicitly because the field accepts no `states:` argument (verified: passing
   one is an `argumentNotAccepted` error).

   Note `associatedPullRequests(first:10)` truncates beyond 10 PRs on one ref.
   That is acceptable: the predicate only asks *whether any* open PR exists, and
   a non-empty list already disqualifies staleness.
5. Sort by name; set `branches_fetched = not partial`, `branches_partial = partial`,
   `branches_fetched_at = now`, `branches_error` = truncation message when partial
   else `None`.

**Failure semantics** — every branch outcome below is caught **inside**
`fetch_repo_state`; the repo itself stays in `repos_succeeded`, repo `stale`
is untouched, and fresh PR/issue data is retained. Partial branch failures are
reported through `SyncResult.partial_errors`, not the repo-failure path.

| Outcome | `branches_fetched` | `branches_partial` | `branches_fetched_at` | `branches_error` | `branches` | `branches_skipped` |
|---|---|---|---|---|---|---|
| Fetched fully | `True` | `False` | `now` | `None` | fresh, sorted | count of malformed dropped |
| Ceiling hit (`hasNextPage`) | `False` | `True` | `now` | `branch list exceeds N entries; truncated` | fresh partial list, sorted | count |
| `GraphQLError` / `GitHubError` | `False` | previous value | previous value | error string | `previous.branches` if any, else `[]` | retained |
| `with_branches=False` | `False` | previous value | previous value | `None` | `previous.branches` if any, else `[]` | retained |

The truncation row differs deliberately from Rev 2, which discarded the partial
list. Rev 3 **keeps** what was fetched (it is real observed data, and 800 of 1200
branches is useful evidence) while `branches_fetched=False` keeps the rule
silent, so the incomplete list can never produce a confidently wrong count.

`SyncResult` gains:

```python
partial_errors: list[dict] = field(default_factory=list)   # [{repo, error, status}]
```

- Populated by `sync()` after each successful fetch: if the fresh entry has
  `branches_error`, append `{"repo": full_name, "error": branches_error,
  "status": None}`.
- Included in `SyncResult.to_dict()` (sync.py:45) and surfaced in `coverage`
  (sync.py:34-43) as `repos_partial` (count). The repo remains counted in
  `repos_succeeded` — coverage "complete" is about repo-level refresh, which
  did succeed.

### 4.4 Signals — `src/project_registry/signals.py`

`SignalConfig` gains `stale_branch_days: int = 90`.

New shared classifier (used by the rule **and** by `cmd_show`, §4.5 — one
implementation of the predicate):

```python
@dataclass(frozen=True)
class BranchCounts:
    total: int
    stale: int
    open_pr_heads: int
    groups: tuple[tuple[str, int], ...]   # stale count per top-level label, desc
    oldest_days: int | None               # age of the oldest stale branch, days

def classify_branches(repo_state: RepoState, now: dt.datetime,
                      cfg: SignalConfig) -> BranchCounts: ...

def stale_branches(repo_state: RepoState, now: dt.datetime,
                   cfg: SignalConfig) -> list[Branch]: ...
```

Stale predicate per branch (a branch is stale when **all** hold):

```text
not is_default
and not open_pr_numbers            # no open in-repo PR evidence
and committed_at is not None       # unknown age is never stale
and age_days(committed_at, now) >= cfg.stale_branch_days   # >=, matching _stale_pr
```

`Branch.age_days(now)` mirrors `PullRequest.age_days` (clamps to ≥ 0, so a
future committer date reads as 0 days, never stale). Group labels:
`name.split("/", 1)[0] + "/"` when a slash exists, else the full name.

New rule in `ATTENTION_RULES` (data-driven; `describe_rules()` picks it up):

```
AttentionRule(
    "stale_branches",
    "Repository with branches untouched for N days (excluding default and open-PR heads)",
    SUGGESTION, urgency=35, applies_to="repo", check=_stale_branches,
)
```

`_stale_branches(repo_state, now, cfg)` returns `None` unless
`repo_state.branches_fetched` is True and `counts.stale >= 1`. Reason string,
top 3 groups with `+N more` when the shown sum < total:

> 783 stale branches (783 claude/, 1 feat/, +2 more), oldest 210d

**`build_attention_queue` changes** (signals.py:160-204):

- `AttentionItem.kind` becomes `"pull_request" | "issue" | "branch"` (docstring).
- `AttentionItem.number` becomes `int | None`, default `None` (signals.py:50).
- The rule loop (signals.py:178-201) currently hard-splits candidates into
  `pull_requests`/`issues`. It gains a repo-level pass: when
  `rule.applies_to == "repo"`, the candidate is the `RepoState` itself, and the
  item is **constructed for repo scope** — explicitly `number=None`,
  `kind="branch"`, `title=repo_state.full_name`, `url=repo_state.url`, and
  **`age_days=None`**. The existing `candidate.age_days(now)` line
  (signals.py:198) must not be reused: `RepoState` has no `age_days` (it has
  `activity_days`), and the reason string already carries the age.
- The sort key (signals.py:203) treats `number=None` as 0:
  `key=lambda i: (-i.urgency, i.repo, i.number or 0, i.rule_id)`.

### 4.5 CLI — `src/project_registry/cli.py`

- `cmd_sync`: add `--no-branches` → `sync(..., with_branches=not args.no_branches)`.
- `cmd_show`: when repo state exists and `branches_fetched`, append a display
  line using `classify_branches(state, now, SignalConfig())` (default config;
  `cmd_show` constructs it directly — no new config plumbing):

  > `github: … · 802 branches (783 stale, 3 open-PR heads)`

  When `branches_error` is set, append `(branches: <error>)` regardless of
  `branches_fetched`. JSON output needs no change — `RepoState.to_dict()`
  already flows through `cmd_show`'s `"github"` key.
- `cmd_attention` and the dashboard/`queries` surfaces must not render `#None`.
  Add one module-level helper in `queries.py`, imported by `cli.py` and
  `dashboard.py`:

  ```python
  def signal_ref(repo: str, number: int | None) -> str:
      return f"{repo}#{number}" if number is not None else repo
  ```

  Used at: `cli.py` `cmd_attention`; `queries.py:455` (key), `queries.py:458`
  (reason), `queries.py:470` (title); `dashboard.py:171-172` **and**
  `dashboard.py:189-190`. Fixing the three `queries.py` sites also fixes the MCP
  `get_attention_queue` response transitively.

### 4.6 Dashboard — `src/project_registry/dashboard.py`

No structural change, but **two** call sites need `signal_ref`, not one:

- `dashboard.py:189-190` (`_attention_section`) — renders `f"{item.repo}#{item.number}"`.
- `dashboard.py:171-172` (`_work_queue_section`) — builds its own link from
  `row['repo']`/`row['number']`. Rev 2 claimed this site needed no fix because
  strings come from `queries.py`; that holds for the row *title* but not for
  this independently constructed link. Both must use the helper.

## 5. Operating-rule compliance checklist

| Rule | Compliance |
|---|---|
| GitHub client read-only | REST paths unchanged (GET). One POST exists, reachable only via `graphql()`, which refuses non-read operations by fail-closed allowlist. Verb test strengthened: POST confined to `graphql`, plus a behavioural mutation-refusal test (§4.2.2) |
| No GitHub mutations | GraphQL document must open with `{`/`query`/`fragment`; `mutation`/`subscription`/unparseable are refused before transport |
| Writes only under `data/` | Branches live in `data/github/snapshot.json` only |
| Never overwrite curated fields | Zero changes to `registry/`, model, or proposals |
| Generated data is evidence, not priority | `stale_branches` is SUGGESTION severity; reason is observed counts, not instruction |
| Unknown ≠ known | `branches_fetched=False` silences the signal; `committed_at=None` never counts as stale; `branches_partial` marks incomplete lists; `branches_skipped` makes dropped nodes visible |
| No secrets | Branch names/SHAs/dates are metadata, not credentials |

## 6. Test plan (network-free, deterministic `NOW` in `tests/conftest.py:15`)

**New file `tests/test_snapshot.py`** (declared here — it does not exist today):
- `Branch` round-trip: `to_dict()` → `from_dict()` preserves every field,
  including `committed_at=None` and empty `open_pr_numbers`.
- `RepoState` with branches round-trips through `Snapshot.from_dict/to_dict`.
- Old snapshot without the new keys loads: `branches == []`,
  `branches_fetched is False`, `branches_fetched_at is None`,
  `branches_skipped == 0`, `branches_partial is False`. Re-saving adds only the
  defaulted keys (assert no other diff).
- Malformed date strings → `committed_at=None` via `parse_ts`; future date →
  `age_days` clamps to 0.
- Unicode branch names round-trip.

**`tests/test_client.py`** (verb invariant — extends the check at
`tests/test_sync.py:82-84`):
- `ALLOWED_METHODS == {"GET", "POST"}`; every `method="POST"` in the module
  source falls inside `graphql`'s source segment (`inspect.getsource`).
- `graphql("mutation { … }")` raises and issues **zero** requests (recording fake).
- Same for `subscription { … }`, for a leading-`#`-comment-obfuscated mutation,
  and for whitespace/newline-prefixed forms.
- `graphql("{ viewer { login } }")` and a `query(...)`-named document are permitted.
- A response containing `errors` raises `GraphQLError`.

**`tests/test_sync.py`** (extend the fake-client pattern; fake serves canned
GraphQL pages and records calls):
- Full fetch: nodes parsed, `is_default` set, `protected` from
  `branchProtectionRule`, sorted by name, `branches_fetched=True`,
  `branches_partial=False`, `branches_fetched_at=now`, `branches_error=None`.
- PR mapping: OPEN in-repo PR maps; `state="CLOSED"`/`"MERGED"` does **not**;
  fork PR (`headRepository.nameWithOwner` different) does **not**; null
  `headRepository` maps; two PRs on one ref → both numbers.
- Cursor pagination: two pages stitch in order; `endCursor` threaded.
- Ceiling: `hasNextPage` still true at `max_pages` → `branches_partial=True`,
  `branches_fetched=False`, truncation message in `branches_error`, **partial
  list retained**, repo **not** in `repos_failed`, `stale` untouched, entry in
  `partial_errors`.
- Exact-boundary: last page with `hasNextPage=False` → fetched, not partial.
- `GraphQLError` raised: previous branches retained, no repo failure,
  `partial_errors` populated.
- `with_branches=False`: `list_branch_nodes` never called; previous branches and
  `branches_fetched_at` retained.
- Malformed nodes (no `name`, `target=None`, empty `oid`): dropped,
  `branches_skipped` counted, rest parsed.
- `branches_error` cleared on a later successful fetch.

**`tests/test_signals.py`**:
- Threshold: branch exactly `stale_branch_days` old fires (`>=`); younger does not.
- Exclusions: default branch, any branch with non-empty `open_pr_numbers`,
  `committed_at=None` branch — none counted.
- Exactly one `AttentionItem` per repo (`kind="branch"`, `number=None`,
  `age_days=None`), reason carries group counts and oldest age.
- Grouping: `claude/x`, `claude/y`, `feat/z` → `("claude/", 2), ("feat/", 1)`;
  top-3 cap with `+N more` when groups exceed 3.
- `branches_fetched=False` → rule silent (covers both the error and the
  truncated-partial cases).
- Sort stability with mixed `number=None` and numbered items.
- `classify_branches`/`stale_branches` unit tests against `make_repo_state`
  (`tests/conftest.py:92`).

**`tests/test_cli.py`**:
- Monkeypatch `project_registry.cli.GitHubClient` with a recording fake
  (`cmd_sync` constructs `GitHubClient()` directly at cli.py:473 and cli.py:510 —
  monkeypatch the module-level name, not an injection point).
- `sync --no-branches` → call log shows no `list_branch_nodes`.
- `attention --json` → branch items have `"number": null`, `"kind": "branch"`,
  and no `#None` in any rendered string (assert `"#None" not in output`).
- `show` displays the branch summary line for a `branches_fetched` state and the
  error suffix when `branches_error` is set.

**`tests/test_mcp.py`**:
- Attention-tool response still parses with a branch item present; assert the
  `signal_ref` shape (no `#None`) in a work-queue response.

**`tests/test_queries.py`** / dashboard:
- A branch item flows through `build_work_queue` without `#None` in key, reason,
  or title; both dashboard sites render the bare repo name.

## 7. Verification

- `python3 -m pytest` — all suites green, no network.
- `registry validate` — clean (unchanged semantics).
- **Contract re-check** (the step that caught the Rev-2 blocker; re-run because
  the schema can change):

  ```bash
  gh api graphql -f query='{repository(owner:"kmadhok",name:"interview-prep"){
    refs(refPrefix:"refs/heads/",first:2){totalCount pageInfo{hasNextPage}
      nodes{name branchProtectionRule{id}
        target{... on Commit{oid committedDate
          associatedPullRequests(first:5){nodes{number state headRepository{nameWithOwner}}}}}}}}}'
  ```

  Confirm `committedDate` is non-null on every node before trusting the parse.
- End-to-end smoke (expected values, to be **confirmed** by the run, not asserted
  in advance): `registry sync --repo kmadhok/interview-prep` → `branches_fetched=true`,
  `branches_partial=false`, ~802 branches, open-PR heads mapped;
  `registry attention` shows exactly one `stale_branches` item.
- **Cost check**: the run should issue ~9 GraphQL requests for that repo and cost
  ~9 rate-limit points (`rateLimit.cost` is 1/page, measured). Confirm wall time
  stays in the seconds range (~6s measured), not minutes.
- Snapshot size: worst case ~76k branch records across 96 repos (a few MB);
  acceptable for a gitignored cache.

## 8. Deferred follow-ups (recorded, not built)

1. **Exact merge detection**: GraphQL can likely report this in the *same* query
   (comparing each ref against the default branch, or via
   `associatedPullRequests` merge state), which is materially cheaper than the
   REST `GET /compare` fan-out Rev 2 assumed. Still deferred — it is added scope,
   not a free rider — but re-price it against GraphQL before assuming it is
   expensive.
2. **Curated branch intent**: once evidence exists, the human decides whether
   `productionalized_version` etc. become project entries with
   `successor`/`component` relationships. A curated-layer change through the
   proposal workflow, out of scope here.
3. **Understanding briefs**: `data/understanding/*` may cite branch counts/stale
   groups from the snapshot as evidence.
4. **Wider GraphQL adoption**: if branch capture proves the transport, PR/issue
   fetching could collapse many REST calls into one query per repo. Explicitly
   not attempted here.

## 9. Open questions for reviewers

1. **Default threshold**: is 90 days right for `stale_branch_days`? (Draft PRs
   age out at 30; stale PRs at 14; branches outlive PRs.) Rev 1 and Rev 2 both
   flagged this as an unvalidated guess. **Rev 3 note:** it is now *answerable* —
   real `committedDate` values exist, so the smoke run can histogram branch ages
   on `interview-prep` and pick a threshold from the distribution instead of
   guessing. Recommend doing that before merge.
2. **Noise vs. signal display**: the reason string reports group counts only.
   Naming the top-3 stale branch *names* would aid triage but could be long on
   noise-heavy repos; the data is in the snapshot either way. Current call: counts.
3. **Carried-forward staleness on display**: when `branches_fetched=False` and
   previous branches are retained, `cmd_show` would show counts computed from
   stale data. Current call: acceptable — the line renders only when
   `branches_fetched`, so stale data stays invisible until re-fetched.
   Alternative: render "branches: N (stale data, fetched <date>)".
4. **Pagination ceiling** (new in Rev 3): `interview-prep` is at 802 of the
   1000-branch ceiling (`per_page 100 × max_pages 10`) and grows with every agent
   checkpoint. When it crosses, `branches_partial` goes true and the signal goes
   silent — correct, but silent. Options: raise `max_pages` for the branch query
   specifically, surface `branches_partial` in `cmd_show`/dashboard as a visible
   warning, or both. Recommend at least the visible warning.
5. **POST in a GET-only client** (new in Rev 3): §4.2.2 argues the mutation-safety
   invariant is preserved and better tested. A reviewer who considers the literal
   GET-only property load-bearing in itself should say so now — the alternative
   is accepting the silent no-op of §1.1 or a 7-minute-per-repo sync.
