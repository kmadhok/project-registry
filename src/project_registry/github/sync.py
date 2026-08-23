"""Refresh observed GitHub state into the snapshot (MCP-004).

Guarantees this module upholds:

* It is read-only with respect to GitHub (see `client.ALLOWED_METHODS`).
* It writes only under ``data/`` -- never under ``registry/``.
* A partial failure never silently discards good data: the previous entry for a
  failed repository is carried forward, marked ``stale`` with the error attached,
  and reported in ``coverage``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..storage import Paths, Registry, read_json, write_json
from .client import GitHubClient, GitHubError, derive_ci_state, derive_review_state
from .snapshot import Branch, Issue, PullRequest, RepoState, Snapshot, iso, parse_ts


@dataclass
class SyncResult:
    started_at: dt.datetime
    completed_at: dt.datetime | None = None
    repos_requested: list[str] = field(default_factory=list)
    repos_succeeded: list[str] = field(default_factory=list)
    repos_failed: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    partial_errors: list[dict[str, Any]] = field(default_factory=list)
    snapshot: Snapshot | None = None

    @property
    def coverage(self) -> dict[str, Any]:
        requested = len(self.repos_requested)
        succeeded = len(self.repos_succeeded)
        return {
            "repos_requested": requested,
            "repos_succeeded": succeeded,
            "repos_failed": len(self.repos_failed),
            "repos_partial": len(self.partial_errors),
            "complete": requested > 0 and succeeded == requested,
            "failed_repos": sorted(self.repos_failed),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
            "coverage": self.coverage,
            "errors": self.errors,
            "partial_errors": self.partial_errors,
        }


def load_snapshot(paths: Paths | None = None) -> Snapshot:
    paths = paths or Paths.resolve()
    return Snapshot.from_dict(read_json(paths.snapshot_file))


def save_snapshot(snapshot: Snapshot, paths: Paths | None = None):
    paths = paths or Paths.resolve()
    return write_json(paths.snapshot_file, snapshot.to_dict())


def sync(
    registry: Registry,
    client: GitHubClient,
    paths: Paths | None = None,
    repos: Iterable[str] | None = None,
    now: dt.datetime | None = None,
    with_details: bool = True,
    with_branches: bool = True,
    write: bool = True,
) -> SyncResult:
    """Refresh the snapshot for the registry's repositories.

    ``repos`` overrides the repository list; by default every repository named
    by a registered project is refreshed.
    """
    # A caller-supplied `now` pins both timestamps, which keeps tests
    # deterministic; otherwise completion is timed for real.
    pinned = now is not None
    now = now or dt.datetime.now(dt.timezone.utc)
    paths = paths or Paths.resolve()
    previous = load_snapshot(paths)

    targets = list(repos) if repos is not None else sorted(
        {p.repo for p in registry if p.repo}
    )
    result = SyncResult(started_at=now, repos_requested=list(targets))
    fresh: dict[str, RepoState] = {}

    for full_name in targets:
        try:
            fresh[full_name] = fetch_repo_state(
                client,
                full_name,
                now,
                with_details=with_details,
                with_branches=with_branches,
                previous=previous.get(full_name),
            )
            result.repos_succeeded.append(full_name)
            if fresh[full_name].branches_error:
                result.partial_errors.append(
                    {
                        "repo": full_name,
                        "error": fresh[full_name].branches_error,
                        "status": None,
                    }
                )
        except GitHubError as exc:
            result.repos_failed.append(full_name)
            result.errors.append(
                {"repo": full_name, "error": str(exc), "status": getattr(exc, "status", None)}
            )
            carried = previous.get(full_name)
            if carried is not None:
                # Keep the last known good data rather than dropping the repo,
                # but mark clearly that it did not refresh.
                carried.stale = True
                carried.error = str(exc)
                fresh[full_name] = carried

    result.completed_at = now if pinned else dt.datetime.now(dt.timezone.utc)
    snapshot = Snapshot(
        repos=fresh,
        started_at=result.started_at,
        completed_at=result.completed_at,
        errors=result.errors,
        coverage=result.coverage,
    )
    result.snapshot = snapshot

    if write:
        save_snapshot(snapshot, paths)
    return result


def fetch_repo_state(
    client: GitHubClient,
    full_name: str,
    now: dt.datetime,
    with_details: bool = True,
    with_branches: bool = True,
    previous: RepoState | None = None,
) -> RepoState:
    """Read one repository plus its open PRs and issues."""
    repo = client.get_repo(full_name)
    state = repo_state_from_api(repo, now)

    for raw in client.list_open_pulls(full_name):
        state.pull_requests.append(
            pull_request_from_api(raw, full_name, client if with_details else None)
        )
    for raw in client.list_open_issues(full_name):
        state.issues.append(issue_from_api(raw, full_name))

    if not with_branches:
        _carry_forward_branches(state, previous)
        state.branches_error = None
        return state

    try:
        nodes, partial, total_count = client.list_branch_nodes(full_name)
    except GitHubError as exc:
        _carry_forward_branches(state, previous)
        state.branches_fetched = False
        state.branches_error = str(exc)
        return state

    state.branches, state.branches_skipped = branches_from_nodes(
        nodes, state.default_branch, full_name
    )
    state.branches_fetched = not partial
    state.branches_fetched_at = now
    state.branches_partial = partial
    if partial:
        state.branches_error = (
            f"branch list has {total_count} entries; truncated after {len(nodes)}"
        )

    return state


def _carry_forward_branches(state: RepoState, previous: RepoState | None) -> None:
    """Copy the last branch observation without presenting it as freshly fetched."""
    state.branches_fetched = False
    if previous is None:
        return
    state.branches = list(previous.branches)
    state.branches_fetched_at = previous.branches_fetched_at
    state.branches_skipped = previous.branches_skipped
    state.branches_partial = previous.branches_partial


def repo_state_from_api(repo: dict, now: dt.datetime) -> RepoState:
    return RepoState(
        full_name=repo.get("full_name", ""),
        private=bool(repo.get("private", True)),
        archived=bool(repo.get("archived", False)),
        fork=bool(repo.get("fork", False)),
        default_branch=repo.get("default_branch") or "main",
        description=repo.get("description"),
        url=repo.get("html_url", ""),
        pushed_at=parse_ts(repo.get("pushed_at")),
        updated_at=parse_ts(repo.get("updated_at")),
        open_issues_count=int(repo.get("open_issues_count") or 0),
        fetched_at=now,
    )


def branch_from_node(
    node: dict[str, Any], default_branch: str, full_name: str
) -> Branch | None:
    """Parse one GraphQL ref node, failing closed on unknown commit age."""
    if not isinstance(node, dict) or not node.get("name"):
        return None
    target = node.get("target")
    if not isinstance(target, dict) or not target.get("oid"):
        return None
    committed_at = parse_ts(target.get("committedDate"))
    if committed_at is None:
        return None

    branch = Branch(
        name=str(node["name"]),
        head_sha=str(target["oid"]),
        committed_at=committed_at,
        protected=node.get("branchProtectionRule") is not None,
        is_default=node["name"] == default_branch,
    )
    associated = target.get("associatedPullRequests") or {}
    if not isinstance(associated, dict):
        associated = {}
    associated_nodes = associated.get("nodes") or []
    if not isinstance(associated_nodes, list):
        associated_nodes = []
    for raw in associated_nodes:
        if not isinstance(raw, dict) or raw.get("state") != "OPEN":
            continue
        head_repository = raw.get("headRepository")
        if head_repository is not None and not isinstance(head_repository, dict):
            continue
        head_repo = (head_repository or {}).get("nameWithOwner")
        if head_repo is not None and head_repo != full_name:
            continue
        try:
            branch.open_pr_numbers.append(int(raw["number"]))
        except (KeyError, TypeError, ValueError):
            continue
    return branch


def branches_from_nodes(
    nodes: Iterable[dict[str, Any]], default_branch: str, full_name: str
) -> tuple[list[Branch], int]:
    """Parse branch nodes and count every malformed node that was dropped."""
    branches: list[Branch] = []
    skipped = 0
    for node in nodes:
        branch = branch_from_node(node, default_branch, full_name)
        if branch is None:
            skipped += 1
            continue
        branches.append(branch)
    return sorted(branches, key=lambda branch: branch.name), skipped


def pull_request_from_api(
    raw: dict, full_name: str, client: GitHubClient | None = None
) -> PullRequest:
    """Build a PullRequest, optionally enriching review and CI state.

    When the detail requests fail the states stay ``unknown`` rather than being
    guessed -- an unknown CI state must never read as a passing one.
    """
    number = int(raw.get("number"))
    pr = PullRequest(
        repo=full_name,
        number=number,
        title=raw.get("title", ""),
        url=raw.get("html_url", ""),
        draft=bool(raw.get("draft", False)),
        author=(raw.get("user") or {}).get("login"),
        created_at=parse_ts(raw.get("created_at")),
        updated_at=parse_ts(raw.get("updated_at")),
        requested_reviewers=[
            (u or {}).get("login", "") for u in raw.get("requested_reviewers") or []
        ],
        labels=[(label or {}).get("name", "") for label in raw.get("labels") or []],
    )

    if client is None:
        return pr

    try:
        pr.review_state = derive_review_state(client.list_reviews(full_name, number))
    except GitHubError:
        pr.review_state = "unknown"

    head_sha = ((raw.get("head") or {}).get("sha")) or ""
    if head_sha:
        try:
            check_runs = client.list_check_runs(full_name, head_sha)
            combined = client.get_combined_status(full_name, head_sha) if not check_runs else None
            pr.ci_state = derive_ci_state(check_runs, combined)
        except GitHubError:
            pr.ci_state = "unknown"

    return pr


def issue_from_api(raw: dict, full_name: str) -> Issue:
    return Issue(
        repo=full_name,
        number=int(raw.get("number")),
        title=raw.get("title", ""),
        url=raw.get("html_url", ""),
        author=(raw.get("user") or {}).get("login"),
        labels=[(label or {}).get("name", "") for label in raw.get("labels") or []],
        created_at=parse_ts(raw.get("created_at")),
        updated_at=parse_ts(raw.get("updated_at")),
    )
