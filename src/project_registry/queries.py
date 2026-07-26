"""Read-only views over the registry.

The CLI and the MCP server both call these functions, so the two surfaces
cannot drift apart. Nothing here writes.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any, Iterable

from .github.snapshot import Snapshot, humanize_age, iso
from .model import (
    Effort,
    Lifecycle,
    NORMALLY_ACTIVE,
    Priority,
    Project,
    sort_projects,
)
from .signals import SignalConfig, build_attention_queue
from .storage import Registry

#: Per-lifecycle staleness thresholds in days.
#:
#: ``review`` -- how long a human review may go without becoming stale.
#: ``activity`` -- how long a lifecycle that claims to be live may go without
#: any GitHub push before that claim is worth re-checking.
STALENESS_THRESHOLDS: dict[Lifecycle, tuple[int, int]] = {
    Lifecycle.NOW: (14, 21),
    Lifecycle.NEXT: (30, 60),
    Lifecycle.INCUBATING: (60, 120),
    Lifecycle.SHOWCASE: (180, 365),
    Lifecycle.MAINTAINED: (90, 180),
    Lifecycle.REFERENCE: (365, 730),
    Lifecycle.SUPERSEDED: (365, 730),
    Lifecycle.ARCHIVED: (730, 1095),
}

#: How much a recorded human priority contributes to work-queue ordering.
#: An unrecorded priority contributes nothing -- it is never given a default,
#: and `human_priority` stays null in the output (MCP-002).
PRIORITY_WEIGHT: dict[Priority, int] = {
    Priority.HIGH: 300,
    Priority.MEDIUM: 200,
    Priority.LOW: 100,
}


@dataclass
class ProjectFilter:
    lifecycle: list[Lifecycle] = field(default_factory=list)
    active: bool | None = None
    category: str | None = None
    tag: str | None = None
    priority: list[Priority] = field(default_factory=list)
    effort: list[Effort] = field(default_factory=list)
    needs_review: bool | None = None
    public: bool | None = None
    has_repo: bool | None = None
    is_fork: bool | None = None
    blocked: bool | None = None

    def matches(self, project: Project) -> bool:
        if self.lifecycle and project.lifecycle not in self.lifecycle:
            return False
        if self.active is not None and project.active is not self.active:
            return False
        if self.category and (project.category or "").lower() != self.category.lower():
            return False
        if self.tag and self.tag.lower() not in {t.lower() for t in project.tags}:
            return False
        if self.priority and project.priority not in self.priority:
            return False
        if self.effort and project.effort not in self.effort:
            return False
        if self.needs_review is not None and project.needs_review is not self.needs_review:
            return False
        if self.public is not None and project.public is not self.public:
            return False
        if self.has_repo is not None and bool(project.repo) is not self.has_repo:
            return False
        if self.is_fork is not None and project.is_fork is not self.is_fork:
            return False
        if self.blocked is not None and bool(project.blocked_by) is not self.blocked:
            return False
        return True


def list_projects(
    registry: Registry, filters: ProjectFilter | None = None
) -> list[Project]:
    """Every project matching the filter, in stable lifecycle order (US-001)."""
    filters = filters or ProjectFilter()
    return sort_projects(p for p in registry.projects.values() if filters.matches(p))


def get_project(registry: Registry, project_id: str) -> Project:
    return registry.require(project_id)


@dataclass
class SearchResult:
    project: Project
    matched_fields: list[str]
    score: int

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.project.summary(),
            "matched_fields": self.matched_fields,
            "score": self.score,
        }


_SEARCH_FIELDS: tuple[tuple[str, int], ...] = (
    ("id", 5), ("name", 5), ("purpose", 4), ("desired_outcome", 3),
    ("category", 2), ("tags", 2), ("next_action", 3), ("accomplishments", 2),
    ("repo", 2), ("notes", 1), ("descriptions", 1),
)


def search_projects(registry: Registry, query: str, limit: int | None = None) -> list[SearchResult]:
    """Free-text search across curated fields, reporting what matched."""
    needle = query.strip().lower()
    if not needle:
        return []

    results: list[SearchResult] = []
    for project in registry.projects.values():
        matched: list[str] = []
        score = 0
        for name, weight in _SEARCH_FIELDS:
            if needle in _field_text(project, name):
                matched.append(name)
                score += weight
        if matched:
            results.append(SearchResult(project=project, matched_fields=matched, score=score))

    results.sort(key=lambda r: (-r.score, r.project.name.lower()))
    return results[:limit] if limit else results


def _field_text(project: Project, name: str) -> str:
    if name == "tags":
        return " ".join(project.tags).lower()
    if name == "next_action":
        return (project.next_action.description if project.next_action else "").lower()
    if name == "accomplishments":
        return " ".join(a.summary for a in project.accomplishments).lower()
    if name == "descriptions":
        parts = [project.descriptions.private or "", project.descriptions.public_safe or ""]
        return " ".join(parts).lower()
    return str(getattr(project, name, "") or "").lower()


def list_related_projects(registry: Registry, project_id: str) -> list[dict[str, Any]]:
    """Declared edges plus inverse edges other projects declare (US-007).

    Inferred edges are labelled as such so a caller can tell which project
    actually recorded the relationship.
    """
    project = registry.require(project_id)
    edges: list[dict[str, Any]] = []
    declared: set[tuple[str, str]] = set()

    for relationship in project.relationships:
        target = registry.get(relationship.target)
        declared.add((relationship.kind.value, relationship.target))
        edges.append(
            {
                "kind": relationship.kind.value,
                "target": relationship.target,
                "target_name": target.name if target else None,
                "note": relationship.note,
                "inferred": False,
                "declared_by": project.id,
                "resolved": target is not None,
            }
        )

    for other in registry.projects.values():
        if other.id == project.id:
            continue
        for relationship in other.relationships:
            if relationship.target != project.id:
                continue
            inverse = relationship.kind.inverse()
            if (inverse.value, other.id) in declared:
                continue  # already declared from this side
            edges.append(
                {
                    "kind": inverse.value,
                    "target": other.id,
                    "target_name": other.name,
                    "note": relationship.note,
                    "inferred": True,
                    "declared_by": other.id,
                    "declared_kind": relationship.kind.value,
                    "resolved": True,
                }
            )

    edges.sort(key=lambda e: (e["kind"], e["target"]))
    return edges


# -- US-005: next actions -------------------------------------------------


def list_next_actions(
    registry: Registry, filters: ProjectFilter | None = None
) -> list[dict[str, Any]]:
    out = []
    for project in list_projects(registry, filters):
        if project.next_action is None:
            continue
        out.append(
            {
                "project_id": project.id,
                "project_name": project.name,
                "lifecycle": project.lifecycle.value,
                "active": project.active,
                "priority": project.priority.value if project.priority else None,
                "description": project.next_action.description,
                "reviewed": (
                    project.next_action.reviewed.isoformat()
                    if project.next_action.reviewed else None
                ),
                "link": project.next_action.link,
                "blocked_by": project.blocked_by,
            }
        )
    return out


def find_missing_next_actions(registry: Registry) -> list[dict[str, Any]]:
    """Active projects with no next action, plus committed-but-inactive ones."""
    out = []
    for project in registry:
        if project.next_action is not None:
            continue
        if project.active:
            reason = "active project with no next action"
        elif project.lifecycle in NORMALLY_ACTIVE:
            reason = f"lifecycle `{project.lifecycle.value}` with no next action"
        else:
            continue
        out.append(
            {
                "project_id": project.id,
                "project_name": project.name,
                "lifecycle": project.lifecycle.value,
                "active": project.active,
                "reason": reason,
            }
        )
    return out


# -- US-008: review queue -------------------------------------------------


@dataclass
class ReviewItem:
    project_id: str
    project_name: str
    lifecycle: str
    reasons: list[str]
    days_since_review: int | None
    days_since_activity: int | None
    staleness: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "lifecycle": self.lifecycle,
            "reasons": self.reasons,
            "days_since_review": self.days_since_review,
            "days_since_activity": self.days_since_activity,
            "staleness": self.staleness,
        }


def build_review_queue(
    registry: Registry,
    snapshot: Snapshot | None = None,
    now: dt.datetime | None = None,
) -> list[ReviewItem]:
    """Projects due for a deliberate look (US-008).

    Staleness is computed from two independent inputs -- the last human review
    and the last GitHub push -- and each item states which one put it here.
    This function never changes a lifecycle; that is always a human decision.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    snapshot = snapshot or Snapshot()
    items: list[ReviewItem] = []

    for project in registry:
        review_days_limit, activity_days_limit = STALENESS_THRESHOLDS[project.lifecycle]
        reasons: list[str] = []
        staleness = 0
        # Either signal -- the explicit flag or a committed lifecycle -- means
        # the project claims to be live and is therefore worth re-checking.
        claims_live = project.active or project.lifecycle in NORMALLY_ACTIVE

        days_since_review = project.days_since_review(today)
        if days_since_review is None:
            if claims_live or project.needs_review:
                reasons.append("never reviewed")
                staleness += review_days_limit
        elif days_since_review > review_days_limit:
            reasons.append(
                f"last reviewed {days_since_review} days ago "
                f"(limit {review_days_limit} for `{project.lifecycle.value}`)"
            )
            staleness += days_since_review - review_days_limit

        state = snapshot.get(project.repo)
        days_since_activity = state.activity_days(now) if state else None
        if (
            days_since_activity is not None
            and claims_live
            and days_since_activity > activity_days_limit
        ):
            reasons.append(
                f"no GitHub activity for {days_since_activity} days while lifecycle "
                f"is `{project.lifecycle.value}`"
            )
            staleness += days_since_activity - activity_days_limit

        if project.needs_review and "never reviewed" not in reasons:
            reasons.append("marked needs_review")
            staleness += 1

        if reasons:
            items.append(
                ReviewItem(
                    project_id=project.id,
                    project_name=project.name,
                    lifecycle=project.lifecycle.value,
                    reasons=reasons,
                    days_since_review=days_since_review,
                    days_since_activity=days_since_activity,
                    staleness=staleness,
                )
            )

    items.sort(key=lambda i: (-i.staleness, i.project_id))
    return items


# -- US-006: cross-project work queue -------------------------------------


@dataclass
class WorkItem:
    project_id: str | None
    project_name: str | None
    title: str
    source: str  # "next_action" | "github"
    reasons: list[str]
    #: Curated priority. None means none was recorded -- never a default.
    human_priority: str | None
    #: Aggregate urgency of the GitHub signals behind this item, 0 if none.
    github_urgency: int
    lifecycle: str | None = None
    effort: str | None = None
    blocked_by: str | None = None
    category: str | None = None
    url: str | None = None
    rule_ids: list[str] = field(default_factory=list)

    @property
    def sort_score(self) -> int:
        return self._priority_weight + self.github_urgency

    @property
    def _priority_weight(self) -> int:
        if self.human_priority is None:
            return 0
        return PRIORITY_WEIGHT.get(Priority(self.human_priority), 0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "title": self.title,
            "source": self.source,
            "reasons": self.reasons,
            "human_priority": self.human_priority,
            "github_urgency": self.github_urgency,
            "lifecycle": self.lifecycle,
            "effort": self.effort,
            "blocked_by": self.blocked_by,
            "category": self.category,
            "url": self.url,
            "rule_ids": self.rule_ids,
        }


def build_work_queue(
    registry: Registry,
    snapshot: Snapshot | None = None,
    now: dt.datetime | None = None,
    filters: ProjectFilter | None = None,
    config: SignalConfig | None = None,
) -> list[WorkItem]:
    """One ranked list combining curated next actions and live signals (US-006).

    Human priority and GitHub urgency are carried as separate fields all the
    way to the renderer. Ordering combines them, but a project with no recorded
    priority is never assigned one -- it simply contributes nothing from that
    term, and reports `human_priority: null`.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    snapshot = snapshot or Snapshot()
    filters = filters or ProjectFilter()

    items: dict[str, WorkItem] = {}

    for project in list_projects(registry, filters):
        if project.next_action is None:
            continue
        key = f"next_action:{project.id}"
        items[key] = WorkItem(
            project_id=project.id,
            project_name=project.name,
            title=project.next_action.description,
            source="next_action",
            reasons=[f"curated next action for `{project.id}`"],
            human_priority=project.priority.value if project.priority else None,
            github_urgency=0,
            lifecycle=project.lifecycle.value,
            effort=project.effort.value if project.effort else None,
            blocked_by=project.blocked_by,
            category=project.category,
            url=project.next_action.link,
        )

    for signal in build_attention_queue(registry, snapshot, now=now, config=config):
        project = registry.get(signal.project_id) if signal.project_id else None
        if project is not None and not filters.matches(project):
            continue
        if project is None and _filter_requires_project(filters):
            continue

        # Attach the signal to the project's existing next-action item when
        # there is one, so a single project does not fragment into many rows.
        key = f"next_action:{project.id}" if project and f"next_action:{project.id}" in items \
            else f"github:{signal.repo}#{signal.number}"

        existing = items.get(key)
        reason = f"{signal.repo}#{signal.number} {signal.reason} [{signal.rule_id}]"
        if existing is not None:
            existing.reasons.append(reason)
            existing.github_urgency = max(existing.github_urgency, signal.urgency)
            existing.rule_ids.append(signal.rule_id)
            if existing.url is None:
                existing.url = signal.url
            continue

        items[key] = WorkItem(
            project_id=project.id if project else None,
            project_name=project.name if project else None,
            title=f"{signal.repo}#{signal.number} {signal.title}",
            source="github",
            reasons=[reason],
            human_priority=(
                project.priority.value if project and project.priority else None
            ),
            github_urgency=signal.urgency,
            lifecycle=project.lifecycle.value if project else None,
            effort=project.effort.value if project and project.effort else None,
            blocked_by=project.blocked_by if project else None,
            category=project.category if project else None,
            url=signal.url,
            rule_ids=[signal.rule_id],
        )

    ordered = sorted(
        items.values(),
        key=lambda i: (-i.sort_score, i.project_id or "~", i.title),
    )
    return ordered


def _filter_requires_project(filters: ProjectFilter) -> bool:
    """True when the filter can only be satisfied by a registered project."""
    return any(
        [
            filters.lifecycle, filters.priority, filters.effort, filters.category,
            filters.tag, filters.active is not None, filters.needs_review is not None,
            filters.public is not None, filters.has_repo is not None,
            filters.is_fork is not None, filters.blocked is not None,
        ]
    )


# -- US-010: pull requests ------------------------------------------------


def list_open_prs(
    snapshot: Snapshot,
    registry: Registry | None = None,
    draft: bool | None = None,
    repo: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Open PRs across the portfolio, read from the snapshot only (US-010).

    ``draft=None`` returns both; ``True`` only drafts; ``False`` only ready PRs.
    The result always carries the snapshot's refresh time, so a cached answer
    can be told apart from a fresh one.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    rows: list[dict[str, Any]] = []

    for pr in snapshot.open_pull_requests():
        if repo and pr.repo.lower() != repo.lower():
            continue
        if draft is not None and pr.draft is not draft:
            continue
        project = registry.by_repo(pr.repo) if registry else None
        rows.append(
            {
                "repo": pr.repo,
                "number": pr.number,
                "title": pr.title,
                "draft": pr.draft,
                "author": pr.author,
                "age_days": pr.age_days(now),
                "idle_days": pr.idle_days(now),
                "review_state": pr.review_state,
                "ci_state": pr.ci_state,
                "url": pr.url,
                "project_id": project.id if project else None,
            }
        )

    rows.sort(key=lambda r: (-(r["age_days"] or 0), r["repo"], r["number"]))
    return {"pull_requests": rows, **snapshot_meta(snapshot, now)}


def list_selected_issues(
    snapshot: Snapshot,
    registry: Registry | None = None,
    labels: Iterable[str] | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Open issues carrying a watched label (US-011, MCP-003)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    watched = {label.lower() for label in (labels or SignalConfig().watched_issue_labels)}
    rows: list[dict[str, Any]] = []

    for issue in snapshot.open_issues():
        hits = sorted({label for label in issue.labels if label.lower() in watched})
        if not hits:
            continue
        project = registry.by_repo(issue.repo) if registry else None
        rows.append(
            {
                "repo": issue.repo,
                "number": issue.number,
                "title": issue.title,
                "labels": issue.labels,
                "matched_labels": hits,
                "age_days": issue.age_days(now),
                "url": issue.url,
                "project_id": project.id if project else None,
            }
        )

    rows.sort(key=lambda r: (-(r["age_days"] or 0), r["repo"], r["number"]))
    return {"issues": rows, **snapshot_meta(snapshot, now)}


def snapshot_meta(snapshot: Snapshot, now: dt.datetime) -> dict[str, Any]:
    """Provenance attached to every snapshot-derived result."""
    return {
        "fetched_at": iso(snapshot.fetched_at),
        "age": humanize_age(snapshot.fetched_at, now),
        "stale": snapshot.is_stale(now),
    }


def source_timestamps(
    registry: Registry, snapshot: Snapshot, now: dt.datetime | None = None
) -> dict[str, Any]:
    """Where an answer came from and how old it is (MCP-002)."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return {
        "registry_loaded_at": registry.loaded_at_iso,
        "github_fetched_at": iso(snapshot.fetched_at),
        "github_age": humanize_age(snapshot.fetched_at, now),
        "github_stale": snapshot.is_stale(now),
    }
