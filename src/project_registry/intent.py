"""Read-only views over owner-authored project intent."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .model import AutomationMode
from .storage import Registry


AUTOMATED_MODES = frozenset(
    {AutomationMode.BUILD, AutomationMode.SHADOW, AutomationMode.SPEC_ONLY}
)
BRIEF_STALE_DAYS = 90


def build_brief_status(registry: Registry, *, now: dt.date | dt.datetime) -> dict:
    """Return brief completeness and freshness for every project."""
    today = now.date() if isinstance(now, dt.datetime) else now
    projects: list[dict[str, Any]] = []

    for project in registry:
        gaps = project.brief_gaps()
        reviewed = project.brief.reviewed
        age_days = (today - reviewed).days if reviewed is not None else None
        stale = (
            age_days is not None and age_days > BRIEF_STALE_DAYS
        ) or (
            reviewed is None and project.automation.mode in AUTOMATED_MODES
        )
        projects.append(
            {
                "project_id": project.id,
                "name": project.name,
                "lifecycle": project.lifecycle.value,
                "automation_mode": project.automation.mode.value,
                "complete": not gaps,
                "gaps": gaps,
                "open_decisions": [
                    {
                        "question": decision.question,
                        "status": decision.status.value,
                    }
                    for decision in project.brief.open_decisions
                ],
                "reviewed": reviewed.isoformat() if reviewed else None,
                "age_days": age_days,
                "stale": stale,
            }
        )

    return _with_summary(projects)


def filter_brief_status(
    status: dict, *, incomplete: bool = False, stale: bool = False
) -> dict:
    """Filter a brief-status result and recompute its summary."""
    projects = [
        project
        for project in status["projects"]
        if (not incomplete or not project["complete"])
        and (not stale or project["stale"])
    ]
    return _with_summary(projects)


def _with_summary(projects: list[dict[str, Any]]) -> dict:
    return {
        "projects": projects,
        "summary": {
            "total": len(projects),
            "complete": sum(project["complete"] for project in projects),
            "incomplete": sum(not project["complete"] for project in projects),
            "stale": sum(project["stale"] for project in projects),
            "open_decisions": sum(
                decision["status"] == "open"
                for project in projects
                for decision in project["open_decisions"]
            ),
        },
    }
