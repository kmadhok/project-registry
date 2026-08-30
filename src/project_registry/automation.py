"""Explainable autonomous-builder eligibility and deterministic ranking.

GitHub urgency, snapshot freshness, and evidence briefs are not ranking inputs.
Observed GitHub state is consulted only to exclude archived repositories.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass
from typing import Any

from .build import ProjectBuildState
from .github.snapshot import RepoState, Snapshot, parse_ts
from .model import AutomationMode, Lifecycle, Project
from .queries import PRIORITY_WEIGHT
from .storage import Registry

ELIGIBILITY_STATES = (
    "ready",
    "waiting_owner",
    "spec_only",
    "paused",
    "needs_intent",
    "manual_only",
    "ineligible",
)


@dataclass
class Eligibility:
    project_id: str
    state: str
    reasons: list[str]
    dry_run: bool
    rank_key: tuple[Any, ...] | None
    brief_gaps: list[str]
    paused_reason: str | None
    waiting_on: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _date(value: dt.date | dt.datetime) -> dt.date:
    return value.date() if isinstance(value, dt.datetime) else value


def rank_key(
    project: Project,
    build_state: ProjectBuildState | None,
    today: dt.date | dt.datetime,
) -> tuple[int, str, int, str]:
    """Return the stable builder order without inventing a priority."""
    del today  # Required by the public contract; current rank terms do not use it.
    priority_weight = PRIORITY_WEIGHT.get(project.priority, 0)
    # Shadow runs intentionally never record a successful merge. Fall back to the
    # last attempted run so a shadow rollout rotates through equally prioritized
    # projects instead of selecting the same "never built" project forever.
    last_activity = (
        build_state.last_success_at or build_state.last_run_at
        if build_state
        else None
    )
    lifecycle_order = list(Lifecycle).index(project.lifecycle)
    return (-priority_weight, last_activity or "", lifecycle_order, project.id)


def _history_reason(
    build_state: ProjectBuildState | None, today: dt.date | dt.datetime
) -> str:
    if build_state is None or not (
        build_state.last_success_at or build_state.last_run_at
    ):
        return "never built"
    label = "last success" if build_state.last_success_at else "last run"
    timestamp = build_state.last_success_at or build_state.last_run_at
    activity = parse_ts(timestamp)
    if activity is None:
        return f"{label} {timestamp}"
    days = max(0, (_date(today) - activity.date()).days)
    return f"{label} {days} d ago"


def waiting_resolved(
    waiting_on: dict, project: Project, owner_action_at: str | None
) -> bool:
    """Return whether an owner wait has been cleared by policy or owner action."""
    if owner_action_at is not None:
        action_ts = parse_ts(owner_action_at)
        waiting_since = parse_ts(waiting_on["since"])
        if (
            action_ts > waiting_since
            if action_ts is not None and waiting_since is not None
            else owner_action_at > waiting_on["since"]
        ):
            return True
    kind = waiting_on.get("kind")
    if kind == "blocked_by_policy":
        classes = waiting_on.get("classes") or []
        allowed = {item.value for item in project.automation.allow}
        return bool(classes) and all(item in allowed for item in classes)
    if kind == "needs_intent" and project.brief.reviewed is not None:
        reviewed = project.brief.reviewed
        reviewed_date = reviewed.isoformat() if isinstance(reviewed, dt.date) else str(reviewed)
        return reviewed_date >= waiting_on["since"][:10]
    return False


def classify(
    project: Project,
    build_state: ProjectBuildState | None,
    snapshot_state: RepoState | None,
    today: dt.date | dt.datetime,
    *,
    owner_action_at: str | None = None,
) -> Eligibility:
    """Classify one project, stopping at the first matching eligibility rule."""
    gaps = project.brief_gaps()
    dry_run = project.automation.mode is AutomationMode.SHADOW
    state: str
    reasons: list[str]
    paused_reason: str | None = None
    key: tuple[Any, ...] | None = None

    if not project.repo:
        state, reasons = "ineligible", ["no repository configured"]
    elif project.id == "project-registry":
        state, reasons = "ineligible", [
            "project-registry is never eligible for autonomous builds"
        ]
    elif project.lifecycle in {Lifecycle.ARCHIVED, Lifecycle.SUPERSEDED}:
        state, reasons = "ineligible", [f"lifecycle={project.lifecycle.value}"]
    elif snapshot_state is not None and snapshot_state.archived:
        state, reasons = "ineligible", ["GitHub repository is archived"]
    elif project.automation.mode is AutomationMode.OFF:
        state, reasons = "manual_only", ["automation.mode=off"]
    elif project.automation.paused:
        state, reasons = "paused", ["automation.paused=true"]
        paused_reason = "automation.paused"
    elif build_state is not None and build_state.paused_reason:
        paused_reason = build_state.paused_reason
        state, reasons = "paused", [f"build_state.paused_reason={paused_reason}"]
    elif project.blocked_by:
        paused_reason = project.blocked_by
        state, reasons = "paused", [f"blocked_by: {project.blocked_by}"]
    elif project.automation.mode in {
        AutomationMode.BUILD,
        AutomationMode.SHADOW,
        AutomationMode.SPEC_ONLY,
    } and gaps:
        state, reasons = "needs_intent", list(gaps)
    elif (
        build_state is not None
        and build_state.waiting_on
        and not waiting_resolved(build_state.waiting_on, project, owner_action_at)
    ):
        waiting_on = build_state.waiting_on
        kind = waiting_on["kind"]
        classes = waiting_on["classes"]
        reason = f"waiting on owner since {waiting_on['since'][:10]}: {kind}"
        if classes:
            reason += f" classes={','.join(classes)}"
        reason += f" (run {waiting_on['run_id']})"
        state, reasons = "waiting_owner", [reason]
    elif project.automation.mode is AutomationMode.SPEC_ONLY:
        state, reasons = "spec_only", ["automation.mode=spec_only", "brief complete"]
    else:
        state = "ready"
        reasons = [
            f"automation.mode={project.automation.mode.value}",
            "brief complete",
            _history_reason(build_state, today),
        ]
        key = rank_key(project, build_state, today)

    return Eligibility(
        project_id=project.id,
        state=state,
        reasons=reasons,
        dry_run=dry_run,
        rank_key=key,
        brief_gaps=gaps,
        paused_reason=paused_reason,
        waiting_on=build_state.waiting_on if build_state else None,
    )


def build_queue(
    registry: Registry,
    snapshot: Snapshot | None,
    build_state_map: dict[str, ProjectBuildState],
    today: dt.date | dt.datetime,
    owner_actions: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Classify every project and return a deterministic, grouped queue."""
    snapshot = snapshot or Snapshot()
    classified = [
        classify(
            project,
            build_state_map.get(project.id),
            snapshot.get(project.repo),
            today,
            owner_action_at=(owner_actions or {}).get(project.id),
        )
        for project in registry.projects.values()
    ]
    ordered: list[Eligibility] = []
    ready = sorted(
        (item for item in classified if item.state == "ready"),
        key=lambda item: item.rank_key or (),
    )
    ordered.extend(ready)
    for state in ELIGIBILITY_STATES[1:]:
        ordered.extend(sorted(
            (item for item in classified if item.state == state),
            key=lambda item: item.project_id,
        ))
    return {
        "candidates": [item.to_dict() for item in ordered],
        "by_state": {
            state: sum(item.state == state for item in classified)
            for state in ELIGIBILITY_STATES
        },
        "ready_count": len(ready),
    }


def readiness(
    project: Project,
    build_state: ProjectBuildState | None,
    snapshot_state: RepoState | None,
    today: dt.date | dt.datetime,
    *,
    owner_action_at: str | None = None,
) -> dict[str, Any]:
    """Return the shared CLI/MCP readiness view for one project."""
    result = classify(
        project, build_state, snapshot_state, today,
        owner_action_at=owner_action_at,
    ).to_dict()
    result["brief"] = {
        "complete": not result["brief_gaps"],
        "missing": list(result["brief_gaps"]),
    }
    result["policy"] = {
        "mode": project.automation.mode.value,
        "allow": [item.value for item in project.automation.allow],
        "budget": {
            "chunks_per_run": project.automation.budget.chunks_per_run,
            "minutes_per_run": project.automation.budget.minutes_per_run,
        },
        "paused": project.automation.paused,
    }
    return result
