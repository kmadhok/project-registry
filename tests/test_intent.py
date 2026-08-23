"""Owner-brief completeness and freshness views."""

from __future__ import annotations

import datetime as dt

from project_registry.intent import build_brief_status, filter_brief_status
from project_registry.model import Project
from project_registry.storage import Registry


NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.timezone.utc)


def registry_of(*projects: Project) -> Registry:
    return Registry({project.id: project for project in projects}, loaded_at=NOW)


def project(project_id: str, **overrides) -> Project:
    raw = {
        "id": project_id,
        "name": project_id.title(),
        "purpose": "A purpose",
        "desired_outcome": "A result",
        "repo": f"owner/{project_id}",
        "brief": {"done_criteria": ["Tests pass"]},
    }
    raw.update(overrides)
    return Project.parse(raw)


def test_complete_and_incomplete_use_exact_project_gap_lists():
    complete = project("complete")
    incomplete = Project.parse({
        "id": "incomplete",
        "name": "Incomplete",
        "brief": {
            "open_decisions": [
                {"question": "Which audience?", "status": "open"},
                {"question": "Which format?", "status": "answered"},
            ]
        },
    })

    report = build_brief_status(registry_of(complete, incomplete), now=NOW)
    by_id = {item["project_id"]: item for item in report["projects"]}

    assert by_id["complete"]["complete"] is True
    assert by_id["complete"]["gaps"] == []
    assert by_id["incomplete"]["complete"] is False
    assert by_id["incomplete"]["gaps"] == [
        "purpose",
        "desired_outcome",
        "brief.done_criteria",
        "repo",
        "open_decision: Which audience?",
    ]
    assert by_id["incomplete"]["open_decisions"] == [
        {"question": "Which audience?", "status": "open"},
        {"question": "Which format?", "status": "answered"},
    ]


def test_never_reviewed_is_stale_for_build_but_not_for_off():
    report = build_brief_status(
        registry_of(
            project("build", automation={"mode": "build"}),
            project("off", automation={"mode": "off"}),
        ),
        now=NOW,
    )
    by_id = {item["project_id"]: item for item in report["projects"]}
    assert by_id["build"]["reviewed"] is None
    assert by_id["build"]["stale"] is True
    assert by_id["off"]["stale"] is False


def test_age_is_computed_from_brief_reviewed():
    report = build_brief_status(
        registry_of(project("dated", brief={
            "done_criteria": ["Tests pass"], "reviewed": "2026-05-23"
        })),
        now=NOW,
    )
    item = report["projects"][0]
    assert item["reviewed"] == "2026-05-23"
    assert item["age_days"] == 91
    assert item["stale"] is True


def test_filters_incomplete_and_stale_and_recompute_summary():
    base = build_brief_status(
        registry_of(
            project("good", brief={"done_criteria": ["Done"], "reviewed": "2026-08-01"}),
            project("incomplete", desired_outcome=None),
            project("both", desired_outcome=None, automation={"mode": "shadow"}),
        ),
        now=NOW,
    )
    incomplete = filter_brief_status(base, incomplete=True)
    stale = filter_brief_status(base, stale=True)
    both = filter_brief_status(base, incomplete=True, stale=True)

    assert {item["project_id"] for item in incomplete["projects"]} == {"incomplete", "both"}
    assert [item["project_id"] for item in stale["projects"]] == ["both"]
    assert both["summary"] == {
        "total": 1, "complete": 0, "incomplete": 1, "stale": 1,
        "open_decisions": 0,
    }


def test_summary_counts_projects_and_open_decisions():
    report = build_brief_status(
        registry_of(
            project("complete", brief={
                "done_criteria": ["Done"],
                "open_decisions": [{"question": "Closed?", "status": "answered"}],
            }),
            project("open", automation={"mode": "build"}, brief={
                "done_criteria": ["Done"],
                "open_decisions": [{"question": "Which one?", "status": "open"}],
            }),
        ),
        now=NOW,
    )
    assert report["summary"] == {
        "total": 2,
        "complete": 1,
        "incomplete": 1,
        "stale": 1,
        "open_decisions": 1,
    }
