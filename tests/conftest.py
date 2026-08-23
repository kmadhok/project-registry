"""Shared fixtures: an isolated registry root and snapshot builders."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml

from project_registry.github.snapshot import Branch, Issue, PullRequest, RepoState, Snapshot
from project_registry.storage import Paths, load_registry

NOW = dt.datetime(2026, 7, 25, 12, 0, tzinfo=dt.timezone.utc)
TODAY = NOW.date()


@pytest.fixture
def paths(tmp_path: Path) -> Paths:
    root = tmp_path / "registry-root"
    (root / "registry" / "projects").mkdir(parents=True)
    (root / "data").mkdir(parents=True)
    return Paths(root)


@pytest.fixture
def write_project(paths: Paths):
    def _write(**fields: Any) -> Path:
        data = {
            "id": fields.pop("id", "example"),
            "name": fields.pop("name", "Example"),
        }
        data.update(fields)
        target = paths.projects_dir / f"{data['id']}.yaml"
        target.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return target

    return _write


@pytest.fixture
def load(paths: Paths):
    return lambda: load_registry(paths)


def make_pr(
    repo: str = "owner/repo",
    number: int = 1,
    *,
    title: str = "A change",
    draft: bool = False,
    review_state: str = "unreviewed",
    ci_state: str = "success",
    created_days_ago: int = 1,
    updated_days_ago: int | None = None,
) -> PullRequest:
    updated_days_ago = created_days_ago if updated_days_ago is None else updated_days_ago
    return PullRequest(
        repo=repo,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/pull/{number}",
        draft=draft,
        author="someone",
        created_at=NOW - dt.timedelta(days=created_days_ago),
        updated_at=NOW - dt.timedelta(days=updated_days_ago),
        review_state=review_state,
        ci_state=ci_state,
    )


def make_issue(
    repo: str = "owner/repo",
    number: int = 10,
    *,
    title: str = "An issue",
    labels: list[str] | None = None,
    created_days_ago: int = 5,
) -> Issue:
    return Issue(
        repo=repo,
        number=number,
        title=title,
        url=f"https://github.com/{repo}/issues/{number}",
        labels=labels or [],
        created_at=NOW - dt.timedelta(days=created_days_ago),
        updated_at=NOW - dt.timedelta(days=created_days_ago),
    )


def make_branch(
    name: str = "feature/work",
    *,
    committed_days_ago: int | None = 100,
    is_default: bool = False,
    open_pr_numbers: list[int] | None = None,
) -> Branch:
    return Branch(
        name=name,
        head_sha=f"sha-{name}",
        committed_at=(
            NOW - dt.timedelta(days=committed_days_ago)
            if committed_days_ago is not None else None
        ),
        is_default=is_default,
        open_pr_numbers=open_pr_numbers or [],
    )


def make_repo_state(
    full_name: str = "owner/repo",
    *,
    private: bool = True,
    archived: bool = False,
    fork: bool = False,
    pushed_days_ago: int = 2,
    pull_requests: list[PullRequest] | None = None,
    issues: list[Issue] | None = None,
    branches: list[Branch] | None = None,
    branches_fetched: bool = False,
    branches_partial: bool = False,
    branches_error: str | None = None,
) -> RepoState:
    return RepoState(
        full_name=full_name,
        private=private,
        archived=archived,
        fork=fork,
        url=f"https://github.com/{full_name}",
        pushed_at=NOW - dt.timedelta(days=pushed_days_ago),
        updated_at=NOW - dt.timedelta(days=pushed_days_ago),
        pull_requests=pull_requests or [],
        issues=issues or [],
        branches=branches or [],
        branches_fetched=branches_fetched,
        branches_fetched_at=NOW if branches or branches_fetched else None,
        branches_partial=branches_partial,
        branches_error=branches_error,
        fetched_at=NOW,
    )


def make_snapshot(*states: RepoState, completed_at: dt.datetime | None = None) -> Snapshot:
    return Snapshot(
        repos={state.full_name: state for state in states},
        started_at=completed_at or NOW,
        completed_at=completed_at or NOW,
        coverage={"repos_requested": len(states), "repos_succeeded": len(states)},
    )
