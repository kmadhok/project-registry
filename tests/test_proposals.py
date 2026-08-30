"""Propose-then-apply workflow and its audit trail (MCP-005)."""

from __future__ import annotations

import datetime as dt

import pytest
import yaml

from project_registry.proposals import (
    APPLIED,
    PENDING,
    REJECTED,
    ProposalError,
    apply_proposal,
    list_proposals,
    last_owner_action,
    load_proposal,
    propose_update,
    record_review,
    reject_proposal,
)
from project_registry.storage import append_jsonl, load_registry, read_jsonl

from .conftest import NOW


def test_last_owner_action_returns_latest_per_project_and_ignores_incomplete(paths):
    assert last_owner_action(paths) == {}
    records = [
        {"action": "apply_proposal", "project_id": "a", "ts": "2026-08-20T10:00:00Z"},
        {"action": "record_review", "project_id": "a", "ts": "2026-08-21T10:00:00Z"},
        {"action": "reject_proposal", "project_id": "b", "ts": "2026-08-19T10:00:00Z"},
        {"action": "record_review", "ts": "2026-08-22T10:00:00Z"},
        {"action": "other", "project_id": "b", "ts": "2026-08-23T10:00:00Z"},
    ]
    for record in records:
        append_jsonl(paths.audit_log, record)

    assert last_owner_action(paths) == {
        "a": "2026-08-21T10:00:00Z",
        "b": "2026-08-19T10:00:00Z",
    }


def test_proposing_does_not_modify_the_project(paths, write_project):
    """MCP-005: propose and apply are distinct operations."""
    path = write_project(id="p", purpose="original purpose")
    before = path.read_text(encoding="utf-8")

    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"purpose": "new purpose"}, paths=paths, now=NOW)

    assert path.read_text(encoding="utf-8") == before
    assert proposal.status == PENDING
    assert load_registry(paths).require("p").purpose == "original purpose"


def test_proposal_records_exact_before_and_after_values(paths, write_project):
    write_project(id="p", purpose="original", lifecycle="incubating")
    registry = load_registry(paths)
    proposal = propose_update(
        registry, "p", {"purpose": "updated", "lifecycle": "next"}, paths=paths, now=NOW
    )
    assert proposal.changes["purpose"] == {"before": "original", "after": "updated"}
    assert proposal.changes["lifecycle"] == {"before": "incubating", "after": "next"}

    diff = proposal.render_diff()
    assert "before: 'original'" in diff
    assert "after:  'updated'" in diff


def test_unset_field_shows_as_unset_in_the_diff(paths, write_project):
    write_project(id="p", purpose="p")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"category": "tooling"}, paths=paths, now=NOW)
    assert proposal.changes["category"]["before"] is None
    assert "(unset)" in proposal.render_diff()


def test_applying_without_approval_is_refused(paths, write_project):
    write_project(id="p", purpose="original")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"purpose": "new"}, paths=paths, now=NOW)

    with pytest.raises(ProposalError, match="explicit approval"):
        apply_proposal(registry, proposal.id, approved=False, paths=paths, now=NOW)

    assert load_registry(paths).require("p").purpose == "original"


def test_approved_apply_writes_the_change(paths, write_project):
    write_project(id="p", purpose="original")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"purpose": "new purpose"}, paths=paths, now=NOW)

    result = apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)

    assert result.proposal.status == APPLIED
    assert load_registry(paths).require("p").purpose == "new purpose"


def test_applied_proposal_cannot_be_replayed(paths, write_project):
    write_project(id="p", purpose="original")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"purpose": "new"}, paths=paths, now=NOW)
    apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)

    with pytest.raises(ProposalError, match="already applied"):
        apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)


def test_apply_refuses_a_change_that_would_break_validation(paths, write_project):
    """MCP-005: changes are validated before they land."""
    write_project(id="p", purpose="p", lifecycle="incubating")
    registry = load_registry(paths)
    # `now` requires a desired_outcome (operating rule 3).
    proposal = propose_update(registry, "p", {"lifecycle": "now"}, paths=paths, now=NOW)

    with pytest.raises(ProposalError, match="introduces new errors"):
        apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)

    assert load_registry(paths).require("p").lifecycle.value == "incubating"


def test_apply_allows_a_change_that_fixes_the_error_at_the_same_time(paths, write_project):
    write_project(id="p", purpose="p", lifecycle="incubating")
    registry = load_registry(paths)
    proposal = propose_update(
        registry, "p", {"lifecycle": "now", "desired_outcome": "shipped"}, paths=paths, now=NOW
    )
    apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)
    assert load_registry(paths).require("p").lifecycle.value == "now"


def test_pre_existing_errors_do_not_block_an_unrelated_change(paths, write_project):
    write_project(id="broken")  # no purpose: already an error
    registry = load_registry(paths)
    proposal = propose_update(registry, "broken", {"category": "tooling"}, paths=paths, now=NOW)
    apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)
    assert load_registry(paths).require("broken").category == "tooling"


def test_apply_refuses_a_value_the_schema_rejects(paths, write_project):
    write_project(id="p", purpose="p")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"lifecycle": "nonsense"}, paths=paths, now=NOW)
    with pytest.raises(ProposalError, match="invalid project"):
        apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)


def test_non_curated_paths_cannot_be_proposed(paths, write_project):
    write_project(id="p", purpose="p")
    registry = load_registry(paths)
    with pytest.raises(ProposalError, match="not proposable"):
        propose_update(registry, "p", {"accomplishments": "[]"}, paths=paths, now=NOW)


def test_no_op_proposals_are_refused(paths, write_project):
    write_project(id="p", purpose="same")
    registry = load_registry(paths)
    with pytest.raises(ProposalError, match="no-op"):
        propose_update(registry, "p", {"purpose": "same"}, paths=paths, now=NOW)


def test_proposal_for_unknown_project_is_refused(paths):
    registry = load_registry(paths)
    with pytest.raises(ProposalError, match="unknown project"):
        propose_update(registry, "ghost", {"purpose": "x"}, paths=paths, now=NOW)


@pytest.mark.parametrize(
    "path,value,expected",
    [
        ("active", "true", True),
        ("active", "yes", True),
        ("active", True, True),
        ("showcase_order", "3", 3),
        ("tags", "one, two", ["one", "two"]),
        ("tags", ["one", "two"], ["one", "two"]),
        ("brief.done_criteria", "one, two", ["one", "two"]),
        ("brief.non_goals", ["one", "two"], ["one", "two"]),
        ("brief.constraints", "local, private", ["local", "private"]),
        ("brief.reviewed", "2026-08-22", "2026-08-22"),
        ("automation.allow", "dependencies, ci", ["dependencies", "ci"]),
        ("automation.budget.chunks_per_run", "2", 2),
        ("automation.budget.minutes_per_run", "30", 30),
        ("automation.paused", "true", True),
        ("automation.mode", "spec_only", "spec_only"),
        ("last_reviewed", "2026-07-01", "2026-07-01"),
        ("purpose", "null", None),
    ],
)
def test_values_are_coerced_to_schema_types(paths, write_project, path, value, expected):
    write_project(id="p", purpose="original", tags=["existing"], showcase_order=9, active=False)
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {path: value}, paths=paths, now=NOW)
    assert proposal.changes[path]["after"] == expected


def test_boolean_false_is_coerced_and_recorded(paths, write_project):
    write_project(id="p", purpose="original", active=True,
                  next_action={"description": "Decide the format", "reviewed": "2026-07-01"})
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"active": "false"}, paths=paths, now=NOW)
    assert proposal.changes["active"] == {"before": True, "after": False}


def test_bad_boolean_is_rejected(paths, write_project):
    write_project(id="p", purpose="p")
    registry = load_registry(paths)
    with pytest.raises(ProposalError, match="expected a boolean"):
        propose_update(registry, "p", {"active": "maybe"}, paths=paths, now=NOW)


def test_open_decisions_accept_json_or_a_list_of_objects(paths, write_project):
    write_project(id="p", purpose="p")
    registry = load_registry(paths)
    for offset, value in enumerate(
        [
            '[{"question":"Which database?"}]',
            [{"question": "Which format?", "status": "answered", "answer": "YAML"}],
        ]
    ):
        proposal = propose_update(
            registry,
            "p",
            {"brief.open_decisions": value},
            paths=paths,
            now=NOW + dt.timedelta(seconds=offset),
        )
        assert isinstance(proposal.changes["brief.open_decisions"]["after"], list)


@pytest.mark.parametrize(
    "path,value,match",
    [
        ("automation.mode", "automatic", "expected one of"),
        ("automation.allow", "dependencies, source_code", "expected values"),
        ("automation.budget.chunks_per_run", "0", ">= 1"),
        ("brief.open_decisions", "not json", "JSON list"),
    ],
)
def test_invalid_brief_and_automation_proposal_values_are_rejected(
    paths, write_project, path, value, match
):
    write_project(id="p", purpose="p")
    with pytest.raises(ProposalError, match=match):
        propose_update(
            load_registry(paths), "p", {path: value}, paths=paths, now=NOW
        )


def test_brief_and_nested_automation_paths_apply_together(paths, write_project):
    write_project(
        id="p",
        purpose="why",
        desired_outcome="done",
        lifecycle="maintained",
        repo="owner/repo",
    )
    registry = load_registry(paths)
    proposal = propose_update(
        registry,
        "p",
        {
            "automation.mode": "build",
            "automation.budget.chunks_per_run": "2",
            "brief.done_criteria": "a,b",
        },
        paths=paths,
        now=NOW,
    )
    apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)

    project = load_registry(paths).require("p")
    assert project.automation.mode.value == "build"
    assert project.automation.budget.chunks_per_run == 2
    assert project.brief.done_criteria == ["a", "b"]


def test_nested_paths_can_be_proposed(paths, write_project):
    write_project(id="p", purpose="p",
                  next_action={"description": "old thing", "reviewed": "2026-07-01"})
    registry = load_registry(paths)
    proposal = propose_update(
        registry, "p",
        {"next_action.description": "Decide the storage format",
         "descriptions.public_safe": "a safe summary"},
        paths=paths, now=NOW,
    )
    apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)

    project = load_registry(paths).require("p")
    assert project.next_action.description == "Decide the storage format"
    assert project.descriptions.public_safe == "a safe summary"


def test_every_apply_appends_one_audit_record(paths, write_project):
    """MCP-005: changes are auditable."""
    write_project(id="p", purpose="original")
    registry = load_registry(paths)

    for i, value in enumerate(["one", "two"], start=1):
        proposal = propose_update(
            registry, "p", {"purpose": value}, rationale=f"round {i}",
            paths=paths, now=NOW + dt.timedelta(seconds=i),
        )
        apply_proposal(registry, proposal.id, approved=True, paths=paths,
                       now=NOW + dt.timedelta(seconds=i))

    records = read_jsonl(paths.audit_log)
    assert len(records) == 2
    assert records[0]["action"] == "apply_proposal"
    assert records[0]["project_id"] == "p"
    assert records[1]["rationale"] == "round 2"
    assert records[1]["changes"]["purpose"] == {"before": "one", "after": "two"}


def test_refused_apply_writes_no_audit_record(paths, write_project):
    write_project(id="p", purpose="p", lifecycle="incubating")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"lifecycle": "now"}, paths=paths, now=NOW)
    with pytest.raises(ProposalError):
        apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)
    assert read_jsonl(paths.audit_log) == []


def test_proposals_can_be_listed_and_filtered(paths, write_project):
    write_project(id="p", purpose="p")
    registry = load_registry(paths)
    first = propose_update(registry, "p", {"purpose": "a"}, paths=paths, now=NOW)
    propose_update(registry, "p", {"category": "tooling"}, paths=paths,
                   now=NOW + dt.timedelta(seconds=1))
    apply_proposal(registry, first.id, approved=True, paths=paths, now=NOW)

    assert len(list_proposals(paths)) == 2
    assert [p.id for p in list_proposals(paths, status=PENDING)] != [first.id]
    assert [p.id for p in list_proposals(paths, status=APPLIED)] == [first.id]


def test_rejecting_a_proposal_leaves_the_project_alone(paths, write_project):
    write_project(id="p", purpose="original")
    registry = load_registry(paths)
    proposal = propose_update(registry, "p", {"purpose": "new"}, paths=paths, now=NOW)
    reject_proposal(proposal.id, paths=paths, now=NOW)

    assert load_proposal(proposal.id, paths).status == REJECTED
    assert load_registry(paths).require("p").purpose == "original"
    with pytest.raises(ProposalError, match="already rejected"):
        apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)


# -- US-008: recording a review -------------------------------------------


def test_record_review_stamps_the_review_date(paths, write_project):
    write_project(id="p", purpose="p", last_reviewed="2026-01-01")
    registry = load_registry(paths)
    record_review(registry, "p", reviewed_on=dt.date(2026, 7, 25), paths=paths, now=NOW)
    assert load_registry(paths).require("p").last_reviewed == dt.date(2026, 7, 25)


def test_record_review_can_update_the_reviewed_fields(paths, write_project):
    """US-008: a review may update purpose, lifecycle, next action, and notes."""
    write_project(id="p", purpose="old purpose", lifecycle="incubating",
                  next_action={"description": "old action", "reviewed": "2026-01-01"})
    registry = load_registry(paths)
    record_review(
        registry, "p",
        reviewed_on=dt.date(2026, 7, 25),
        updates={
            "purpose": "clarified purpose",
            "lifecycle": "next",
            "next_action.description": "Decide whether to keep the CLI",
            "notes": "revisited after the quarter",
        },
        paths=paths, now=NOW,
    )
    project = load_registry(paths).require("p")
    assert project.purpose == "clarified purpose"
    assert project.lifecycle.value == "next"
    assert project.next_action.description == "Decide whether to keep the CLI"
    assert project.notes == "revisited after the quarter"


def test_record_review_re_dates_the_confirmed_next_action(paths, write_project):
    write_project(id="p", purpose="p",
                  next_action={"description": "Decide the format", "reviewed": "2026-01-01"})
    registry = load_registry(paths)
    record_review(registry, "p", reviewed_on=dt.date(2026, 7, 25), paths=paths, now=NOW)
    assert load_registry(paths).require("p").next_action.reviewed == dt.date(2026, 7, 25)


def test_record_review_is_audited(paths, write_project):
    write_project(id="p", purpose="p", last_reviewed="2026-01-01")
    registry = load_registry(paths)
    record_review(registry, "p", reviewed_on=dt.date(2026, 7, 25), paths=paths, now=NOW)
    records = read_jsonl(paths.audit_log)
    assert len(records) == 1
    assert records[0]["source"] == "record_review"


def test_record_review_without_approval_is_refused(paths, write_project):
    write_project(id="p", purpose="p", last_reviewed="2026-01-01")
    registry = load_registry(paths)
    with pytest.raises(
        ProposalError,
        match=r"pending proposal p-\d+ filed, NOTHING applied; call again with approved=true",
    ):
        record_review(registry, "p", reviewed_on=dt.date(2026, 7, 25), paths=paths,
                      now=NOW, approved=False)
    assert load_registry(paths).require("p").last_reviewed == dt.date(2026, 1, 1)
    assert len(list_proposals(paths, status=PENDING)) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("is_fork", False), ("tags", []), ("next_action", "")],
)
def test_record_review_preserves_explicit_falsy_fields(
    paths, write_project, field, value
):
    path = write_project(id="p", purpose="p", **{field: value})
    registry = load_registry(paths)

    record_review(registry, "p", reviewed_on=dt.date(2026, 7, 25), paths=paths, now=NOW)

    assert yaml.safe_load(path.read_text(encoding="utf-8"))[field] == value


def test_record_review_does_not_add_an_unset_fork_flag(paths, write_project):
    path = write_project(id="p", purpose="p")
    registry = load_registry(paths)

    record_review(registry, "p", reviewed_on=dt.date(2026, 7, 25), paths=paths, now=NOW)

    assert "is_fork" not in yaml.safe_load(path.read_text(encoding="utf-8"))


def test_proposal_apply_does_not_add_unset_curated_keys(paths, write_project):
    path = write_project(id="p", purpose="original")
    registry = load_registry(paths)
    proposal = propose_update(
        registry, "p", {"purpose": "updated"}, paths=paths, now=NOW
    )

    apply_proposal(registry, proposal.id, approved=True, paths=paths, now=NOW)

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert raw == {
        "id": "p",
        "name": "Example",
        "purpose": "updated",
        "lifecycle": "incubating",
        "active": False,
        "visibility": "private",
    }
    assert "is_fork" not in raw
    assert "tags" not in raw
