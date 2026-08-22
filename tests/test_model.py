"""Schema and model behaviour (US-001, US-002, US-003, US-007)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from project_registry.model import (
    AutomationMode,
    ChangeClass,
    Lifecycle,
    Project,
    RegistryError,
    RelationKind,
)
from project_registry.storage import Paths, dump_yaml, load_registry, save_project

from .conftest import TODAY


def test_repo_and_non_repo_projects_are_both_representable(write_project, load):
    """US-001: intentional non-repository projects are first-class."""
    write_project(id="with-repo", repo="owner/thing", purpose="p")
    write_project(id="no-repo", purpose="a research effort with no code")

    registry = load()
    assert registry.require("with-repo").source == "repo"
    assert registry.require("no-repo").source == "non_repo"
    assert registry.require("no-repo").repo is None


def test_summary_exposes_the_six_listing_fields(write_project, load):
    """US-001: purpose, lifecycle, activity, visibility, last review."""
    write_project(
        id="p", purpose="why", lifecycle="maintained", active=True,
        visibility="public", last_reviewed="2026-01-02",
    )
    summary = load().require("p").summary()
    assert summary == {
        "id": "p",
        "name": "Example",
        "purpose": "why",
        "lifecycle": "maintained",
        "active": True,
        "visibility": "public",
        "last_reviewed": "2026-01-02",
    }


def test_fork_flag_and_duplicate_relationship_distinguish_copies(write_project, load):
    """US-001: forks and duplicate checkouts are distinguishable."""
    write_project(id="original", purpose="p", repo="owner/thing")
    write_project(
        id="a-fork", purpose="p", repo="other/thing", is_fork=True,
        relationships=[{"kind": "duplicate", "target": "original"}],
    )
    registry = load()
    assert registry.require("a-fork").is_fork is True
    assert registry.require("original").is_fork is False
    assert registry.require("a-fork").relationships[0].kind is RelationKind.DUPLICATE


def test_private_and_public_descriptions_are_separate_fields(write_project, load):
    """US-002: the two descriptions never bleed into each other."""
    write_project(
        id="p", purpose="why",
        descriptions={"private": "client name is ACME", "public_safe": "a scheduling tool"},
    )
    project = load().require("p")
    assert project.descriptions.private == "client name is ACME"
    assert project.descriptions.public_safe == "a scheduling tool"


def test_unknown_fields_are_rejected():
    with pytest.raises(RegistryError, match="unknown field"):
        Project.parse({"id": "p", "name": "P", "lifecyle": "now"})


@pytest.mark.parametrize(
    "field,value",
    [("lifecycle", "someday"), ("priority", "urgent"), ("visibility", "secret")],
)
def test_enum_fields_reject_unknown_values(field, value):
    with pytest.raises(RegistryError, match=field):
        Project.parse({"id": "p", "name": "P", field: value})


def test_id_must_be_a_slug():
    with pytest.raises(RegistryError, match="lowercase slug"):
        Project.parse({"id": "Not A Slug", "name": "P"})


def test_repo_must_be_owner_slash_name():
    with pytest.raises(RegistryError, match="owner/name"):
        Project.parse({"id": "p", "name": "P", "repo": "just-a-name"})


def test_next_action_is_a_single_object_not_a_list():
    """US-005: 'exactly one' is expressed in the type."""
    with pytest.raises(RegistryError, match="next_action must be a mapping"):
        Project.parse({
            "id": "p", "name": "P",
            "next_action": [{"description": "one"}, {"description": "two"}],
        })


def test_next_action_rejects_a_bare_string():
    with pytest.raises(RegistryError, match="not a bare string"):
        Project.parse({"id": "p", "name": "P", "next_action": "do the thing"})


def test_explicit_empty_next_action_round_trips_as_empty_string():
    project = Project.parse({"id": "p", "name": "P", "next_action": ""})
    assert project.next_action is None
    assert project.to_dict()["next_action"] == ""


def test_new_project_defaults_keep_the_existing_canonical_output():
    assert Project(id="p", name="P").to_dict() == {
        "id": "p",
        "name": "P",
        "lifecycle": "incubating",
        "active": False,
        "visibility": "private",
    }


def test_brief_and_automation_round_trip_byte_stably(paths):
    data = {
        "id": "p",
        "name": "P",
        "purpose": "why",
        "desired_outcome": "done",
        "brief": {
            "done_criteria": ["tests pass"],
            "non_goals": ["deployment"],
            "constraints": ["local only"],
            "open_decisions": [
                {"question": "Which format?", "status": "answered", "answer": "YAML"},
                {"question": "Which owner?", "status": "open", "answer": None},
            ],
            "reviewed": "2026-08-22",
        },
        "lifecycle": "maintained",
        "active": False,
        "blocked_by": "review",
        "automation": {
            "mode": "off",
            "allow": ["dependencies", "ci"],
            "budget": {"chunks_per_run": 6, "minutes_per_run": 120},
            "paused": False,
        },
        "repo": "owner/repo",
        "visibility": "private",
    }
    path = paths.projects_dir / "p.yaml"
    original = dump_yaml(data)
    path.write_text(original, encoding="utf-8")

    project = load_registry(paths).require("p")
    assert project.automation.mode is AutomationMode.OFF
    assert project.automation.allow == [ChangeClass.DEPENDENCIES, ChangeClass.CI]
    save_project(project, paths)

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("brief", {"mystery": True}, "unknown brief"),
        ("automation", {"mystery": True}, "unknown automation"),
        ("automation", {"budget": {"mystery": 1}}, "unknown automation.budget"),
        (
            "brief",
            {"open_decisions": [{"question": "Q?", "mystery": True}]},
            "unknown open decision",
        ),
    ],
)
def test_brief_and_automation_unknown_subkeys_are_rejected(field, value, match):
    with pytest.raises(RegistryError, match=match):
        Project.parse({"id": "p", "name": "P", field: value})


@pytest.mark.parametrize(
    "automation,match",
    [
        ({"mode": "automatic"}, "automation.mode"),
        ({"allow": ["source_code"]}, "automation.allow"),
        ({"budget": {"chunks_per_run": 0}}, "chunks_per_run"),
        ({"budget": {"minutes_per_run": -1}}, "minutes_per_run"),
    ],
)
def test_invalid_automation_mode_class_and_budget_are_rejected(automation, match):
    with pytest.raises(RegistryError, match=match):
        Project.parse({"id": "p", "name": "P", "automation": automation})


def test_open_decision_requires_a_question():
    with pytest.raises(RegistryError, match="open_decisions.question is required"):
        Project.parse(
            {"id": "p", "name": "P", "brief": {"open_decisions": [{"status": "open"}]}}
        )


def test_brief_gaps_reports_each_missing_piece_and_open_question():
    project = Project.parse(
        {
            "id": "p",
            "name": "P",
            "brief": {
                "open_decisions": [
                    {"question": "Which database?"},
                    {"question": "Which format?", "status": "answered", "answer": "YAML"},
                ]
            },
        }
    )
    assert project.brief_gaps() == [
        "purpose",
        "desired_outcome",
        "brief.done_criteria",
        "repo",
        "open_decision: Which database?",
    ]

    complete = Project.parse(
        {
            "id": "complete",
            "name": "Complete",
            "purpose": "why",
            "desired_outcome": "done",
            "repo": "owner/repo",
            "brief": {
                "done_criteria": ["tests pass"],
                "open_decisions": [
                    {"question": "Which format?", "status": "answered", "answer": "YAML"}
                ],
            },
        }
    )
    assert complete.brief_gaps() == []


def test_every_curated_project_yaml_is_byte_stable_on_load_and_dump():
    root = Path(__file__).resolve().parents[1]
    registry = load_registry(Paths(root))

    changed = []
    for project in registry:
        path = Path(project.source_path or "")
        if dump_yaml(project.to_dict()) != path.read_text(encoding="utf-8"):
            changed.append(path.name)

    assert changed == []


def test_accomplishments_sort_newest_first(write_project, load):
    """US-003: multiple dated accomplishments, most recent first."""
    write_project(
        id="p", purpose="why",
        accomplishments=[
            {"date": "2025-01-01", "summary": "older"},
            {"date": "2026-05-05", "summary": "newer"},
            {"date": "2025-09-09", "summary": "middle"},
        ],
    )
    project = load().require("p")
    assert [a.summary for a in project.accomplishments] == ["newer", "middle", "older"]


def test_accomplishment_links_round_trip(write_project, load):
    links = [
        "https://github.com/owner/repo/commit/abc",
        "https://github.com/owner/repo/releases/tag/v1",
        "https://example.com/demo",
        "https://github.com/owner/repo/pull/7",
    ]
    write_project(
        id="p",
        purpose="why",
        accomplishments=[
            {"date": "2026-05-05", "kind": "demo", "summary": "Shipped", "links": links}
        ],
    )

    assert load().require("p").accomplishments[0].links == links


def test_accomplishment_requires_a_date():
    with pytest.raises(RegistryError, match="accomplishment.date is required"):
        Project.parse({
            "id": "p", "name": "P", "accomplishments": [{"summary": "no date"}]
        })


def test_accomplishment_summary_respects_its_limit(write_project, load):
    write_project(
        id="p", purpose="why",
        accomplishments=[
            {"date": f"2026-01-0{i}", "summary": f"thing {i}", "links": ["http://x"]}
            for i in range(1, 6)
        ],
    )
    lines = load().require("p").accomplishment_summary(limit=2)
    assert len(lines) == 2
    assert lines[0].startswith("2026-01-05")


def test_relationship_inverses_pair_up_and_symmetric_kinds_self_invert():
    """US-007: directional where appropriate."""
    assert RelationKind.SUCCESSOR.inverse() is RelationKind.PREDECESSOR
    assert RelationKind.PREDECESSOR.inverse() is RelationKind.SUCCESSOR
    assert RelationKind.COMPONENT.inverse() is RelationKind.PART_OF
    assert RelationKind.DUPLICATE.inverse() is RelationKind.DUPLICATE
    assert RelationKind.RELATED.inverse() is RelationKind.RELATED
    assert RelationKind.SUCCESSOR.directional is True
    assert RelationKind.RELATED.directional is False


def test_round_trip_preserves_every_populated_field(write_project, load, paths):
    write_project(
        id="p",
        name="Full",
        purpose="why",
        desired_outcome="what good looks like",
        lifecycle="next",
        active=True,
        category="tooling",
        priority="high",
        effort="small",
        horizon="month",
        blocked_by="waiting on review",
        repo="owner/thing",
        visibility="public",
        last_reviewed="2026-07-01",
        next_action={"description": "ship it", "reviewed": "2026-07-01", "link": "http://x"},
        accomplishments=[{"date": "2026-06-01", "summary": "shipped", "public": True}],
        relationships=[{"kind": "related", "target": "p"}],
        descriptions={"private": "a", "public_safe": "b"},
        public=True,
        showcase_order=1,
        tags=["one", "two"],
        notes="a note",
    )
    original = load().require("p")
    reparsed = Project.parse(original.to_dict())
    assert reparsed.to_dict() == original.to_dict()
    assert reparsed.accomplishments[0].public is True


def test_duplicate_ids_across_files_are_rejected(paths, write_project):
    write_project(id="dupe", purpose="one")
    (paths.projects_dir / "other.yaml").write_text(
        "id: dupe\nname: Other\npurpose: two\n", encoding="utf-8"
    )
    with pytest.raises(RegistryError, match="duplicate project id"):
        load_registry(paths)


def test_days_since_review(write_project, load):
    write_project(id="p", purpose="why", last_reviewed="2026-07-01")
    assert load().require("p").days_since_review(TODAY) == 24


def test_save_project_writes_canonical_yaml(paths, write_project, load):
    write_project(id="p", purpose="why", lifecycle="now", desired_outcome="done")
    project = load().require("p")
    written = save_project(project, paths)
    text = written.read_text(encoding="utf-8")
    assert text.startswith("id: p\n")
    assert "lifecycle: now" in text


def test_empty_registry_loads_cleanly(paths):
    assert len(load_registry(paths)) == 0
