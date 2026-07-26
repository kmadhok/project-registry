"""Refresh observed GitHub state into the snapshot (MCP-004).

Guarantees this module upholds:

* It is read-only with respect to GitHub (see `client.ALLOWED_METHOD`).
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
from .snapshot import Issue, PullRequest, RepoState, Snapshot, iso, parse_ts


@dataclass
class SyncResult:
    started_at: dt.datetime
    completed_at: dt.datetime | None = None
    repos_requested: list[str] = field(default_factory=list)
    repos_succeeded: list[str] = field(default_factory=list)
    repos_failed: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    snapshot: Snapshot | None = None

    @property
    def coverage(self) -> dict[str, Any]:
        requested = len(self.repos_requested)
        succeeded = len(self.repos_succeeded)
        return {
            "repos_requested": requested,
            "repos_succeeded": succeeded,
            "repos_failed": len(self.repos_failed),
            "complete": requested > 0 and succeeded == requested,
            "failed_repos": sorted(self.repos_failed),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
            "coverage": self.coverage,
            "errors": self.errors,
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
            fresh[full_name] = fetch_repo_state(client, full_name, now, with_details)
            result.repos_succeeded.append(full_name)
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

    return state


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
