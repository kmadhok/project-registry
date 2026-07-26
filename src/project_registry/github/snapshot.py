"""Observed GitHub state.

This is evidence, not intent. Nothing here is authoritative about whether a
project matters -- operating rule 6. Every record carries the time it was
fetched so a stale answer can always be told apart from a live one.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

#: Review states we distinguish. `unknown` is used when the API did not answer,
#: and is never silently downgraded to `unreviewed`.
REVIEW_STATES = ("approved", "changes_requested", "commented", "unreviewed", "unknown")
CI_STATES = ("success", "failure", "pending", "none", "unknown")


def parse_ts(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None


def age_days(value: dt.datetime | None, now: dt.datetime) -> int | None:
    if value is None:
        return None
    return max(0, (now - value).days)


def humanize_age(value: dt.datetime | None, now: dt.datetime) -> str:
    """Short human age string used in every cached-result header (US-010)."""
    if value is None:
        return "never"
    seconds = max(0, int((now - value).total_seconds()))
    if seconds < 90:
        return "just now"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


@dataclass
class PullRequest:
    repo: str
    number: int
    title: str
    url: str
    draft: bool = False
    author: str | None = None
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    review_state: str = "unknown"
    ci_state: str = "unknown"
    requested_reviewers: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    def age_days(self, now: dt.datetime) -> int | None:
        return age_days(self.created_at, now)

    def idle_days(self, now: dt.datetime) -> int | None:
        return age_days(self.updated_at or self.created_at, now)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PullRequest":
        return cls(
            repo=raw["repo"],
            number=int(raw["number"]),
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            draft=bool(raw.get("draft", False)),
            author=raw.get("author"),
            created_at=parse_ts(raw.get("created_at")),
            updated_at=parse_ts(raw.get("updated_at")),
            review_state=raw.get("review_state", "unknown"),
            ci_state=raw.get("ci_state", "unknown"),
            requested_reviewers=list(raw.get("requested_reviewers") or []),
            labels=list(raw.get("labels") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "draft": self.draft,
            "author": self.author,
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
            "review_state": self.review_state,
            "ci_state": self.ci_state,
            "requested_reviewers": list(self.requested_reviewers),
            "labels": list(self.labels),
        }


@dataclass
class Issue:
    repo: str
    number: int
    title: str
    url: str
    author: str | None = None
    labels: list[str] = field(default_factory=list)
    created_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None

    def age_days(self, now: dt.datetime) -> int | None:
        return age_days(self.created_at, now)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Issue":
        return cls(
            repo=raw["repo"],
            number=int(raw["number"]),
            title=raw.get("title", ""),
            url=raw.get("url", ""),
            author=raw.get("author"),
            labels=list(raw.get("labels") or []),
            created_at=parse_ts(raw.get("created_at")),
            updated_at=parse_ts(raw.get("updated_at")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "number": self.number,
            "title": self.title,
            "url": self.url,
            "author": self.author,
            "labels": list(self.labels),
            "created_at": iso(self.created_at),
            "updated_at": iso(self.updated_at),
        }


@dataclass
class RepoState:
    full_name: str
    private: bool = True
    archived: bool = False
    fork: bool = False
    default_branch: str = "main"
    description: str | None = None
    url: str = ""
    pushed_at: dt.datetime | None = None
    updated_at: dt.datetime | None = None
    open_issues_count: int = 0
    pull_requests: list[PullRequest] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    fetched_at: dt.datetime | None = None
    #: True when this entry was carried over from a previous snapshot because
    #: the most recent refresh failed for this repository (MCP-004).
    stale: bool = False
    error: str | None = None

    def activity_days(self, now: dt.datetime) -> int | None:
        return age_days(self.pushed_at, now)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RepoState":
        return cls(
            full_name=raw["full_name"],
            private=bool(raw.get("private", True)),
            archived=bool(raw.get("archived", False)),
            fork=bool(raw.get("fork", False)),
            default_branch=raw.get("default_branch", "main"),
            description=raw.get("description"),
            url=raw.get("url", ""),
            pushed_at=parse_ts(raw.get("pushed_at")),
            updated_at=parse_ts(raw.get("updated_at")),
            open_issues_count=int(raw.get("open_issues_count", 0) or 0),
            pull_requests=[PullRequest.from_dict(p) for p in raw.get("pull_requests") or []],
            issues=[Issue.from_dict(i) for i in raw.get("issues") or []],
            fetched_at=parse_ts(raw.get("fetched_at")),
            stale=bool(raw.get("stale", False)),
            error=raw.get("error"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "full_name": self.full_name,
            "private": self.private,
            "archived": self.archived,
            "fork": self.fork,
            "default_branch": self.default_branch,
            "description": self.description,
            "url": self.url,
            "pushed_at": iso(self.pushed_at),
            "updated_at": iso(self.updated_at),
            "open_issues_count": self.open_issues_count,
            "pull_requests": [p.to_dict() for p in self.pull_requests],
            "issues": [i.to_dict() for i in self.issues],
            "fetched_at": iso(self.fetched_at),
            "stale": self.stale,
            "error": self.error,
        }


@dataclass
class Snapshot:
    """The whole observed picture, as of one refresh."""

    repos: dict[str, RepoState] = field(default_factory=dict)
    started_at: dt.datetime | None = None
    completed_at: dt.datetime | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)

    @property
    def fetched_at(self) -> dt.datetime | None:
        return self.completed_at or self.started_at

    def is_empty(self) -> bool:
        return not self.repos

    def get(self, repo: str | None) -> RepoState | None:
        if not repo:
            return None
        direct = self.repos.get(repo)
        if direct is not None:
            return direct
        lowered = repo.lower()
        for name, state in self.repos.items():
            if name.lower() == lowered:
                return state
        return None

    def open_pull_requests(self) -> list[PullRequest]:
        return [pr for state in self.repos.values() for pr in state.pull_requests]

    def open_issues(self) -> list[Issue]:
        return [issue for state in self.repos.values() for issue in state.issues]

    def is_stale(self, now: dt.datetime, max_age_hours: int = 24) -> bool:
        if self.fetched_at is None:
            return True
        return (now - self.fetched_at).total_seconds() > max_age_hours * 3600

    def status(self, now: dt.datetime, max_age_hours: int = 24) -> dict[str, Any]:
        """Sync status for MCP-003 / MCP-004 reporting."""
        return {
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
            "fetched_at": iso(self.fetched_at),
            "age": humanize_age(self.fetched_at, now),
            "stale": self.is_stale(now, max_age_hours),
            "coverage": dict(self.coverage),
            "errors": list(self.errors),
            "repo_count": len(self.repos),
            "stale_repos": sorted(n for n, r in self.repos.items() if r.stale),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "Snapshot":
        if not raw:
            return cls()
        return cls(
            repos={
                name: RepoState.from_dict(state)
                for name, state in (raw.get("repos") or {}).items()
            },
            started_at=parse_ts(raw.get("started_at")),
            completed_at=parse_ts(raw.get("completed_at")),
            errors=list(raw.get("errors") or []),
            coverage=dict(raw.get("coverage") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": iso(self.started_at),
            "completed_at": iso(self.completed_at),
            "coverage": dict(self.coverage),
            "errors": list(self.errors),
            "repos": {name: state.to_dict() for name, state in sorted(self.repos.items())},
        }
