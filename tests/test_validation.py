"""Core operating rules (US-002, US-004, US-005, US-007)."""

from __future__ import annotations

from project_registry.validation import ERROR, SUGGESTION, ValidationConfig, validate

from .conftest import TODAY


def findings(registry, rule_id, config=None):
    report = validate(registry, today=TODAY, config=config)
    return [f for f in report.findings if f.rule_id == rule_id]


def test_missing_purpose_is_an_error(write_project, load):
    """US-002 / operating rule 1."""
    write_project(id="p")
    found = findings(load(), "purpose_missing")
    assert len(found) == 1
    assert found[0].severity == ERROR


def test_needs_review_downgrades_missing_purpose_to_a_suggestion(write_project, load):
    """US-002: needs_review is the sanctioned placeholder."""
    write_project(id="p", needs_review=True)
    registry = load()
    assert not findings(registry, "purpose_missing")
    pending = findings(registry, "purpose_pending")
    assert len(pending) == 1
    assert pending[0].severity == SUGGESTION


def test_active_project_without_next_action_is_an_error(write_project, load):
    """US-005 / operating rule 2."""
    write_project(id="p", purpose="why", active=True)
    found = findings(load(), "next_action_missing")
    assert len(found) == 1
    assert found[0].severity == ERROR


def test_active_project_with_next_action_passes(write_project, load):
    write_project(
        id="p", purpose="why", active=True,
        next_action={"description": "Decide whether to merge the parser", "reviewed": "2026-07-20"},
    )
    assert not findings(load(), "next_action_missing")


def test_vague_next_action_is_flagged_as_a_suggestion(write_project, load):
    """US-005: a next action names an outcome or a decision."""
    write_project(
        id="p", purpose="why", active=True,
        next_action={"description": "continue", "reviewed": "2026-07-20"},
    )
    found = findings(load(), "next_action_vague")
    assert len(found) == 1
    assert found[0].severity == SUGGESTION


def test_next_action_without_a_reviewed_date_is_an_error(write_project, load):
    write_project(
        id="p", purpose="why", active=True, next_action={"description": "Ship the importer"}
    )
    assert findings(load(), "next_action_unreviewed")[0].severity == ERROR


def test_future_next_action_review_is_an_error_but_today_is_valid(write_project, load):
    for project_id, reviewed in (("future", "2026-07-26"), ("today", "2026-07-25")):
        write_project(
            id=project_id,
            purpose="why",
            next_action={"description": "Ship the importer", "reviewed": reviewed},
        )

    found = findings(load(), "next_action_future_review")
    assert [(item.project_id, item.severity) for item in found] == [("future", ERROR)]


def test_now_without_desired_outcome_is_an_error(write_project, load):
    """Operating rule 3: nothing enters `now` without an exit outcome."""
    write_project(
        id="p", purpose="why", lifecycle="now", active=True,
        next_action={"description": "Build the thing", "reviewed": "2026-07-20"},
    )
    assert findings(load(), "now_without_outcome")[0].severity == ERROR


def test_too_many_now_projects_is_a_suggestion(write_project, load):
    """US-004: warn when `now` is overloaded, never enforce."""
    for i in range(4):
        write_project(
            id=f"p{i}", purpose="why", lifecycle="now", desired_outcome="done",
            active=True,
            next_action={"description": f"Ship feature {i}", "reviewed": "2026-07-20"},
        )
    found = findings(load(), "now_overloaded", ValidationConfig(max_now=3))
    assert len(found) == 1
    assert found[0].severity == SUGGESTION
    assert "4 projects are in `now`" in found[0].message


def test_archived_but_active_is_a_suggestion_not_an_error(write_project, load):
    """US-004: lifecycle and activity stay separate so exceptions can exist."""
    write_project(
        id="p", purpose="why", lifecycle="archived", active=True,
        next_action={"description": "Confirm the shutdown plan", "reviewed": "2026-07-20"},
    )
    report = validate(load(), today=TODAY)
    coherence = [f for f in report.findings if f.rule_id == "active_but_closed"]
    assert len(coherence) == 1
    assert coherence[0].severity == SUGGESTION
    assert report.ok  # no errors introduced


def test_inactive_committed_lifecycles_are_suggestions(write_project, load):
    write_project(id="now", purpose="why", lifecycle="now", desired_outcome="done")
    write_project(id="next", purpose="why", lifecycle="next")
    write_project(id="maintained", purpose="why", lifecycle="maintained")

    found = findings(load(), "inactive_but_committed")
    assert [(item.project_id, item.severity) for item in found] == [
        ("maintained", SUGGESTION),
        ("next", SUGGESTION),
        ("now", SUGGESTION),
    ]


def test_build_mode_requires_a_complete_brief_but_off_does_not(write_project, load):
    for project_id, mode in (("build", "build"), ("off", "off")):
        write_project(
            id=project_id,
            purpose="why",
            desired_outcome="done",
            repo=f"owner/{project_id}",
            automation={"mode": mode},
        )

    found = findings(load(), "brief_incomplete_for_build")
    assert [(item.project_id, item.severity) for item in found] == [("build", ERROR)]
    assert "brief.done_criteria" in found[0].message


def test_open_decision_is_build_error_and_names_the_question(write_project, load):
    write_project(
        id="p",
        purpose="why",
        desired_outcome="done",
        repo="owner/repo",
        brief={
            "done_criteria": ["tests pass"],
            "open_decisions": [{"question": "Which database?"}],
        },
        automation={"mode": "build"},
    )
    found = findings(load(), "brief_incomplete_for_build")
    assert found[0].severity == ERROR
    assert "open_decision: Which database?" in found[0].message
    assert not findings(load(), "open_decision_unanswered")


def test_open_decision_is_a_suggestion_when_automation_is_off(write_project, load):
    write_project(
        id="p",
        purpose="why",
        brief={"open_decisions": [{"question": "Which database?"}]},
        automation={"mode": "off"},
    )
    found = findings(load(), "open_decision_unanswered")
    assert [(item.project_id, item.severity) for item in found] == [("p", SUGGESTION)]
    assert "Which database?" in found[0].message


def test_enabled_automation_outside_focus_lifecycle_is_a_suggestion(write_project, load):
    write_project(
        id="p",
        purpose="why",
        desired_outcome="done",
        repo="owner/repo",
        lifecycle="incubating",
        brief={"done_criteria": ["tests pass"]},
        automation={"mode": "build"},
    )
    found = findings(load(), "automation_without_focus_lifecycle")
    assert [(item.project_id, item.severity) for item in found] == [("p", SUGGESTION)]


def test_active_never_reviewed_is_a_suggestion_but_inactive_is_not(write_project, load):
    write_project(
        id="active",
        purpose="why",
        active=True,
        next_action={"description": "Decide the storage format", "reviewed": "2026-07-25"},
    )
    write_project(id="inactive", purpose="why")

    found = findings(load(), "active_never_reviewed")
    assert [(item.project_id, item.severity) for item in found] == [
        ("active", SUGGESTION)
    ]


def test_superseded_without_successor_is_an_error(write_project, load):
    """US-007 / operating rule 5."""
    write_project(id="old", purpose="why", lifecycle="superseded")
    assert findings(load(), "superseded_without_successor")[0].severity == ERROR


def test_superseded_with_successor_passes(write_project, load):
    write_project(id="new", purpose="why")
    write_project(
        id="old", purpose="why", lifecycle="superseded",
        relationships=[{"kind": "successor", "target": "new"}],
    )
    assert not findings(load(), "superseded_without_successor")


def test_broken_relationship_target_is_an_error(write_project, load):
    """US-007: broken references are detected."""
    write_project(
        id="p", purpose="why", relationships=[{"kind": "related", "target": "ghost"}]
    )
    found = findings(load(), "relationship_broken")
    assert len(found) == 1
    assert "ghost" in found[0].message


def test_self_relationship_is_an_error(write_project, load):
    write_project(
        id="p", purpose="why", relationships=[{"kind": "related", "target": "p"}]
    )
    assert findings(load(), "relationship_self_reference")[0].severity == ERROR


def test_projects_sharing_a_repo_are_flagged_for_overlap(write_project, load):
    """Operating rule 4: check new projects against existing ones."""
    write_project(id="one", purpose="why", repo="owner/thing")
    write_project(id="two", purpose="why", repo="owner/thing")
    found = findings(load(), "overlap_same_repo")
    assert len(found) == 1
    assert found[0].severity == SUGGESTION


def test_declared_duplicate_suppresses_the_overlap_suggestion(write_project, load):
    write_project(id="one", purpose="why", repo="owner/thing")
    write_project(
        id="two", purpose="why", repo="owner/thing",
        relationships=[{"kind": "duplicate", "target": "one"}],
    )
    assert not findings(load(), "overlap_same_repo")


def test_identical_names_are_flagged_for_overlap(write_project, load):
    write_project(id="one", name="Invoice Parser", purpose="why")
    write_project(id="two", name="invoice  parser", purpose="why")
    assert findings(load(), "overlap_similar_name")


def test_public_without_public_safe_description_is_an_error(write_project, load):
    """US-009 safety gate, enforced before anything can be exported."""
    write_project(id="p", purpose="why", public=True)
    assert findings(load(), "public_without_public_safe")[0].severity == ERROR


def test_showcase_order_collision_applies_only_to_public_projects(write_project, load):
    for project_id in ("public-one", "public-two"):
        write_project(
            id=project_id,
            purpose="why",
            public=True,
            showcase_order=1,
            descriptions={"public_safe": "safe summary"},
        )
    write_project(id="private", purpose="why", showcase_order=1)

    found = findings(load(), "showcase_order_collision")
    assert [(item.project_id, item.severity) for item in found] == [
        ("public-two", SUGGESTION)
    ]


def test_credential_material_is_an_error(write_project, load):
    """Safety boundary: the registry never stores credential values."""
    write_project(
        id="p", purpose="why", notes="token is ghp_abcdefghijklmnopqrstuvwxyz0123"
    )
    found = findings(load(), "credential_material")
    assert len(found) == 1
    assert found[0].severity == ERROR


def test_stale_review_on_active_project_is_a_suggestion(write_project, load):
    write_project(
        id="p", purpose="why", active=True, last_reviewed="2026-01-01",
        next_action={"description": "Decide the storage format", "reviewed": "2026-01-01"},
    )
    assert findings(load(), "active_review_stale")[0].severity == SUGGESTION


def test_report_separates_errors_from_suggestions(write_project, load):
    write_project(id="broken")  # error: no purpose
    write_project(id="soft", needs_review=True)  # suggestion
    report = validate(load(), today=TODAY)
    assert report.ok is False
    assert len(report.errors) == 1
    assert any(f.rule_id == "purpose_pending" for f in report.suggestions)
    assert report.to_dict()["error_count"] == 1


def test_clean_registry_reports_ok(write_project, load):
    write_project(
        id="p", purpose="why", lifecycle="maintained", active=True,
        last_reviewed="2026-07-20",
        next_action={"description": "Renew the deployment certificate", "reviewed": "2026-07-20"},
    )
    assert validate(load(), today=TODAY).ok
