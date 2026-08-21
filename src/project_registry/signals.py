"""Rule tables over observed GitHub state.

Two independent passes:

* :func:`build_attention_queue` -- GitHub work that needs a human (US-011).
* :func:`find_mismatches`       -- registry intent contradicting GitHub (US-012).

Both are pure reads. Neither is called from the sync path, so a refresh can
never quietly resolve a mismatch it just created.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

from .github.snapshot import Branch, Issue, PullRequest, RepoState, Snapshot
from .model import Lifecycle, Project
from .storage import Registry
from .validation import ERROR, SUGGESTION


@dataclass
class SignalConfig:
    #: An open PR untouched for this long is stale.
    stale_pr_days: int = 14
    #: A ready-for-review PR with no review after this long is unreviewed.
    unreviewed_pr_days: int = 3
    #: A draft open this long has probably been forgotten.
    draft_pr_days: int = 30
    #: Issues carrying any of these labels are "selected" for the queue.
    watched_issue_labels: tuple[str, ...] = ("blocked", "bug", "priority", "next")
    #: Pushes within this window count as GitHub activity.
    recent_activity_days: int = 30
    #: Active projects unreviewed for this long are called out.
    review_warning_days: int = 45
    #: A branch with no commit for this long is stale.
    stale_branch_days: int = 60


@dataclass(frozen=True)
class BranchCounts:
    total: int
    stale: int
    open_pr_heads: int
    groups: tuple[tuple[str, int], ...]
    oldest_days: int | None


@dataclass(frozen=True)
class AttentionItem:
    """One piece of GitHub work needing attention, with its provenance."""

    rule_id: str
    severity: str
    reason: str
    url: str
    repo: str
    kind: str  # "pull_request" | "issue" | "branch"
    title: str
    number: int | None = None
    project_id: str | None = None
    age_days: int | None = None
    urgency: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "reason": self.reason,
            "url": self.url,
            "repo": self.repo,
            "kind": self.kind,
            "number": self.number,
            "title": self.title,
            "project_id": self.project_id,
            "age_days": self.age_days,
            "urgency": self.urgency,
        }


@dataclass(frozen=True)
class AttentionRule:
    id: str
    description: str
    severity: str
    urgency: int
    applies_to: str  # "pull_request" | "issue" | "repo"
    check: Callable[[Any, dt.datetime, SignalConfig], str | None]


def _ci_failing(pr: PullRequest, now: dt.datetime, cfg: SignalConfig) -> str | None:
    if pr.ci_state == "failure":
        return "checks are failing"
    return None


def _changes_requested(pr: PullRequest, now: dt.datetime, cfg: SignalConfig) -> str | None:
    if pr.review_state == "changes_requested":
        return "a reviewer requested changes"
    return None


def _stale_pr(pr: PullRequest, now: dt.datetime, cfg: SignalConfig) -> str | None:
    idle = pr.idle_days(now)
    if idle is not None and idle >= cfg.stale_pr_days:
        return f"open with no update for {idle} days"
    return None


def _unreviewed_pr(pr: PullRequest, now: dt.datetime, cfg: SignalConfig) -> str | None:
    if pr.draft or pr.review_state != "unreviewed":
        return None
    age = pr.age_days(now)
    if age is not None and age >= cfg.unreviewed_pr_days:
        return f"ready for review and unreviewed for {age} days"
    return None


def _draft_aging(pr: PullRequest, now: dt.datetime, cfg: SignalConfig) -> str | None:
    if not pr.draft:
        return None
    age = pr.age_days(now)
    if age is not None and age >= cfg.draft_pr_days:
        return f"still a draft after {age} days"
    return None


def _selected_issue(issue: Issue, now: dt.datetime, cfg: SignalConfig) -> str | None:
    watched = {label.lower() for label in cfg.watched_issue_labels}
    hits = sorted({label for label in issue.labels if label.lower() in watched})
    if hits:
        return f"labelled {', '.join(hits)}"
    return None


def stale_branches(
    repo_state: RepoState, now: dt.datetime, cfg: SignalConfig
) -> list[Branch]:
    """Branches old enough to flag, excluding unknown and actively reviewed work."""
    found = []
    for branch in repo_state.branches:
        age = branch.age_days(now)
        if branch.is_default or branch.open_pr_numbers or age is None:
            continue
        if age >= cfg.stale_branch_days:
            found.append(branch)
    return found


def classify_branches(
    repo_state: RepoState, now: dt.datetime, cfg: SignalConfig
) -> BranchCounts:
    stale = stale_branches(repo_state, now, cfg)
    grouped: dict[str, int] = {}
    ages: list[int] = []
    for branch in stale:
        label = branch.name.split("/", 1)[0] + "/" if "/" in branch.name else branch.name
        grouped[label] = grouped.get(label, 0) + 1
        age = branch.age_days(now)
        if age is not None:
            ages.append(age)
    return BranchCounts(
        total=len(repo_state.branches),
        stale=len(stale),
        open_pr_heads=sum(bool(branch.open_pr_numbers) for branch in repo_state.branches),
        groups=tuple(sorted(grouped.items(), key=lambda item: (-item[1], item[0]))),
        oldest_days=max(ages) if ages else None,
    )


def _stale_branches(
    repo_state: RepoState, now: dt.datetime, cfg: SignalConfig
) -> str | None:
    if not repo_state.branches_fetched:
        return None
    counts = classify_branches(repo_state, now, cfg)
    if not counts.stale:
        return None
    shown = counts.groups[:3]
    parts = [f"{count} {label}" for label, count in shown]
    shown_count = sum(count for _, count in shown)
    if shown_count < counts.stale:
        parts.append(f"+{counts.stale - shown_count} more")
    groups = ", ".join(parts)
    return f"{counts.stale} stale branches ({groups}), oldest {counts.oldest_days}d"


#: The full signal table. Adding a signal means adding a row.
ATTENTION_RULES: tuple[AttentionRule, ...] = (
    AttentionRule(
        "ci_failing", "Open PR whose checks are failing",
        ERROR, 100, "pull_request", _ci_failing,
    ),
    AttentionRule(
        "changes_requested", "Open PR with requested changes",
        ERROR, 90, "pull_request", _changes_requested,
    ),
    AttentionRule(
        "unreviewed_pr", "Ready PR waiting on a first review",
        SUGGESTION, 70, "pull_request", _unreviewed_pr,
    ),
    AttentionRule(
        "stale_pr", "Open PR with no recent activity",
        SUGGESTION, 60, "pull_request", _stale_pr,
    ),
    AttentionRule(
        "draft_pr_aging", "Draft PR left open a long time",
        SUGGESTION, 40, "pull_request", _draft_aging,
    ),
    AttentionRule(
        "selected_issue", "Open issue carrying a watched label",
        SUGGESTION, 50, "issue", _selected_issue,
    ),
    AttentionRule(
        "stale_branches",
        "Repository with branches untouched for N days (excluding default and open-PR heads)",
        SUGGESTION, 35, "repo", _stale_branches,
    ),
)


def build_attention_queue(
    registry: Registry,
    snapshot: Snapshot,
    now: dt.datetime | None = None,
    config: SignalConfig | None = None,
    repo: str | None = None,
) -> list[AttentionItem]:
    """Apply every attention rule to the snapshot (US-011).

    A single PR can appear once per rule it trips; each appearance names the
    rule that produced it and links to its GitHub source.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = config or SignalConfig()
    items: list[AttentionItem] = []

    for repo_state in snapshot.repos.values():
        if repo and repo_state.full_name.lower() != repo.lower():
            continue
        project = registry.by_repo(repo_state.full_name)
        project_id = project.id if project else None

        for rule in ATTENTION_RULES:
            if rule.applies_to == "repo":
                reason = rule.check(repo_state, now, cfg)
                if reason:
                    items.append(
                        AttentionItem(
                            rule_id=rule.id,
                            severity=rule.severity,
                            reason=reason,
                            url=repo_state.url,
                            repo=repo_state.full_name,
                            kind="branch",
                            title=repo_state.full_name,
                            project_id=project_id,
                            age_days=None,
                            urgency=rule.urgency,
                        )
                    )
                continue
            candidates = (
                repo_state.pull_requests if rule.applies_to == "pull_request"
                else repo_state.issues
            )
            for candidate in candidates:
                reason = rule.check(candidate, now, cfg)
                if not reason:
                    continue
                items.append(
                    AttentionItem(
                        rule_id=rule.id,
                        severity=rule.severity,
                        reason=reason,
                        url=candidate.url or repo_state.url,
                        repo=repo_state.full_name,
                        kind=rule.applies_to,
                        number=candidate.number,
                        title=candidate.title,
                        project_id=project_id,
                        age_days=candidate.age_days(now),
                        urgency=rule.urgency,
                    )
                )

    items.sort(key=lambda i: (-i.urgency, i.repo, i.number or 0, i.rule_id))
    return items


# -- US-012: registry / GitHub mismatches ---------------------------------


@dataclass(frozen=True)
class Mismatch:
    rule_id: str
    severity: str
    message: str
    project_id: str
    repo: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "project_id": self.project_id,
            "repo": self.repo,
            "url": self.url,
        }


@dataclass(frozen=True)
class MismatchRule:
    id: str
    description: str
    severity: str
    #: Receives (project, repo_state | None, now, config); returns a message or None.
    check: Callable[[Project, RepoState | None, dt.datetime, SignalConfig], str | None]


def _repo_missing(project, state, now, cfg) -> str | None:
    if project.repo and state is None:
        return (
            f"registry points at {project.repo}, which is not present in the "
            "latest GitHub snapshot"
        )
    return None


def _archived_but_github_active(project, state, now, cfg) -> str | None:
    if state is None or project.lifecycle is not Lifecycle.ARCHIVED:
        return None
    days = state.activity_days(now)
    if days is not None and days <= cfg.recent_activity_days:
        return (
            f"registry lifecycle is `archived` but {state.full_name} was pushed "
            f"{days} days ago"
        )
    return None


def _github_archived_but_registry_active(project, state, now, cfg) -> str | None:
    if state is None or not state.archived:
        return None
    if project.active or project.lifecycle in {Lifecycle.NOW, Lifecycle.NEXT}:
        return (
            f"{state.full_name} is archived on GitHub but the registry treats "
            "the project as live work"
        )
    return None


def _visibility_exposed(project, state, now, cfg) -> str | None:
    if state is None:
        return None
    if project.visibility.value == "private" and not state.private:
        return (
            f"registry records {project.repo} as private but GitHub reports it public"
        )
    return None


def _visibility_drift(project, state, now, cfg) -> str | None:
    if state is None:
        return None
    if project.visibility.value == "public" and state.private:
        return (
            f"registry records {project.repo} as public but GitHub reports it private"
        )
    return None


def _active_without_recent_review(project, state, now, cfg) -> str | None:
    if not project.active:
        return None
    if project.last_reviewed is None:
        return "is active but has never been reviewed"
    days = (now.date() - project.last_reviewed).days
    if days > cfg.review_warning_days:
        return f"is active but was last reviewed {days} days ago"
    return None


def _inactive_but_recent_pushes(project, state, now, cfg) -> str | None:
    if state is None or project.active:
        return None
    if project.lifecycle in {Lifecycle.ARCHIVED, Lifecycle.SUPERSEDED}:
        return None  # covered by the archived rules
    days = state.activity_days(now)
    if days is not None and days <= cfg.recent_activity_days:
        return (
            f"is marked inactive but {state.full_name} was pushed {days} days ago "
            "(evidence, not priority)"
        )
    return None


def _fork_flag_drift(project, state, now, cfg) -> str | None:
    if state is None or project.is_fork == state.fork:
        return None
    claim = "a fork" if project.is_fork else "an original project"
    truth = "a fork" if state.fork else "not a fork"
    return f"registry records this as {claim} but GitHub reports it is {truth}"


MISMATCH_RULES: tuple[MismatchRule, ...] = (
    MismatchRule("repo_missing", "Registry repo absent from GitHub", ERROR, _repo_missing),
    MismatchRule(
        "archived_but_github_active", "Archived in registry, active on GitHub",
        ERROR, _archived_but_github_active,
    ),
    MismatchRule(
        "github_archived_but_registry_active", "Archived on GitHub, live in registry",
        ERROR, _github_archived_but_registry_active,
    ),
    MismatchRule(
        "visibility_exposed", "Registry says private, GitHub says public",
        ERROR, _visibility_exposed,
    ),
    MismatchRule(
        "visibility_drift", "Registry says public, GitHub says private",
        SUGGESTION, _visibility_drift,
    ),
    MismatchRule(
        "active_without_recent_review", "Active project with no recent review",
        SUGGESTION, _active_without_recent_review,
    ),
    MismatchRule(
        "inactive_but_recent_pushes", "Inactive project receiving pushes",
        SUGGESTION, _inactive_but_recent_pushes,
    ),
    MismatchRule("fork_flag_drift", "Fork flag disagrees with GitHub", SUGGESTION, _fork_flag_drift),
)

_SEVERITY_ORDER = {ERROR: 0, SUGGESTION: 1}


def find_mismatches(
    registry: Registry,
    snapshot: Snapshot,
    now: dt.datetime | None = None,
    config: SignalConfig | None = None,
) -> list[Mismatch]:
    """Compare curated intent against observed state (US-012)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cfg = config or SignalConfig()
    found: list[Mismatch] = []

    for project in registry:
        state = snapshot.get(project.repo)
        # With no snapshot at all we cannot distinguish "repo gone" from
        # "never synced", so stay silent rather than report a false error.
        if project.repo and snapshot.is_empty():
            continue
        for rule in MISMATCH_RULES:
            message = rule.check(project, state, now, cfg)
            if message:
                found.append(
                    Mismatch(
                        rule_id=rule.id,
                        severity=rule.severity,
                        message=message,
                        project_id=project.id,
                        repo=project.repo,
                        url=state.url if state else None,
                    )
                )

    found.sort(key=lambda m: (_SEVERITY_ORDER.get(m.severity, 9), m.project_id, m.rule_id))
    return found


def describe_rules() -> dict[str, list[dict[str, str]]]:
    """Machine-readable rule catalogue, so output can explain itself."""
    return {
        "attention": [
            {"id": r.id, "description": r.description, "severity": r.severity}
            for r in ATTENTION_RULES
        ],
        "mismatch": [
            {"id": r.id, "description": r.description, "severity": r.severity}
            for r in MISMATCH_RULES
        ],
    }
