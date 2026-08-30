"""Builder eligibility, explainability, and deterministic ranking (US-013)."""

from __future__ import annotations

from project_registry.automation import build_queue, classify
from project_registry.build import ProjectBuildState
from project_registry.storage import Registry, load_registry

from .conftest import TODAY, make_pr, make_repo_state, make_snapshot


def _complete(write_project, project_id: str, **fields):
    values = {
        "id": project_id,
        "purpose": "Ship a useful result",
        "desired_outcome": "The result is in production",
        "repo": f"owner/{project_id}",
        "brief": {"done_criteria": ["Tests pass"]},
        "automation": {"mode": "build"},
    }
    values.update(fields)
    write_project(**values)


def _waiting(kind="blocked_by_policy", classes=None):
    return {
        "kind": kind,
        "classes": classes or [],
        "since": "2026-08-20T12:00:00+00:00",
        "run_id": "run-waiting",
    }


def test_all_seven_states_are_reachable_and_explained(paths, write_project):
    _complete(write_project, "ready")
    _complete(write_project, "spec", automation={"mode": "spec_only"})
    _complete(write_project, "paused", automation={"mode": "build", "paused": True})
    _complete(write_project, "intent", brief={"done_criteria": []})
    _complete(write_project, "manual", automation={"mode": "off"})
    _complete(write_project, "waiting")
    write_project(id="excluded", purpose="No repository")

    registry = load_registry(paths)
    states = {"waiting": ProjectBuildState(waiting_on=_waiting())}
    queue = build_queue(registry, make_snapshot(), states, TODAY)
    found = {item["project_id"]: item for item in queue["candidates"]}

    assert {item["state"] for item in found.values()} == {
        "ready", "waiting_owner", "spec_only", "paused", "needs_intent",
        "manual_only", "ineligible",
    }
    assert all(item["reasons"] for item in found.values())
    assert found["ready"]["reasons"] == [
        "automation.mode=build", "brief complete", "never built"
    ]
    assert queue["by_state"] == {state: 1 for state in queue["by_state"]}


def test_blocked_policy_wait_requires_allow_or_later_owner_action(
    paths, write_project
):
    waiting_on = _waiting(classes=["generated_data"])
    state = ProjectBuildState(waiting_on=waiting_on)
    _complete(write_project, "blocked")
    project = load_registry(paths).require("blocked")

    waiting = classify(project, state, None, TODAY)
    assert waiting.state == "waiting_owner"
    assert "generated_data" in waiting.reasons[0]
    assert "run-waiting" in waiting.reasons[0]
    assert "2026-08-20" in waiting.reasons[0]
    assert waiting.to_dict()["waiting_on"] == waiting_on

    _complete(
        write_project, "allowed",
        automation={"mode": "build", "allow": ["generated_data"]},
    )
    allowed = load_registry(paths).require("allowed")
    assert classify(allowed, state, None, TODAY).state == "ready"
    assert classify(
        project, state, None, TODAY,
        owner_action_at="2026-08-20T12:00:01+00:00",
    ).state == "ready"
    assert classify(
        project, state, None, TODAY,
        owner_action_at="2026-08-20T11:59:59+00:00",
    ).state == "waiting_owner"


def test_waiting_owner_action_compares_normalized_timestamps(paths, write_project):
    _complete(write_project, "blocked")
    project = load_registry(paths).require("blocked")
    state = ProjectBuildState(waiting_on={
        "kind": "blocked_by_policy",
        "classes": ["generated_data"],
        "since": "2026-08-26T12:00:00+00:00",
        "run_id": "run-waiting",
    })

    assert classify(
        project, state, None, TODAY,
        owner_action_at="2026-08-26T12:00:00Z",
    ).state == "waiting_owner"
    assert classify(
        project, state, None, TODAY,
        owner_action_at="2026-08-26T12:00:01Z",
    ).state == "ready"


def test_needs_intent_wait_resolves_by_review_and_gaps_win(paths, write_project):
    state = ProjectBuildState(waiting_on=_waiting(kind="needs_intent"))
    _complete(write_project, "unreviewed")
    _complete(
        write_project, "reviewed",
        brief={"done_criteria": ["Tests pass"], "reviewed": "2026-08-20"},
    )
    _complete(
        write_project, "gap",
        brief={"done_criteria": [], "reviewed": "2026-08-21"},
    )
    registry = load_registry(paths)

    assert classify(registry.require("unreviewed"), state, None, TODAY).state == (
        "waiting_owner"
    )
    assert classify(registry.require("reviewed"), state, None, TODAY).state == "ready"
    assert classify(registry.require("gap"), state, None, TODAY).state == "needs_intent"


def test_build_queue_unknown_owner_action_keeps_waiting(paths, write_project):
    _complete(write_project, "blocked")
    states = {"blocked": ProjectBuildState(waiting_on=_waiting())}

    queue = build_queue(
        load_registry(paths), make_snapshot(), states, TODAY, owner_actions=None
    )

    assert queue["candidates"][0]["state"] == "waiting_owner"


def test_registry_itself_is_always_ineligible(paths, write_project):
    _complete(write_project, "project-registry")
    project = load_registry(paths).require("project-registry")
    result = classify(project, None, None, TODAY)
    assert result.state == "ineligible"
    assert "never eligible" in result.reasons[0]


def test_ranking_ignores_github_urgency(paths, write_project):
    _complete(write_project, "urgent-github")
    _complete(write_project, "human-high", priority="high")
    snapshot = make_snapshot(
        make_repo_state(
            "owner/urgent-github",
            pull_requests=[
                make_pr(repo="owner/urgent-github", number=number, ci_state="failure")
                for number in range(1, 8)
            ],
        )
    )
    queue = build_queue(load_registry(paths), snapshot, {}, TODAY)
    assert [item["project_id"] for item in queue["candidates"][:2]] == [
        "human-high", "urgent-github"
    ]


def test_fairness_never_built_then_oldest_success(paths, write_project):
    for project_id in ("never", "older", "newer"):
        _complete(write_project, project_id, priority="medium")
    states = {
        "older": ProjectBuildState(last_success_at="2026-05-01T12:00:00+00:00"),
        "newer": ProjectBuildState(last_success_at="2026-07-01T12:00:00+00:00"),
    }
    queue = build_queue(load_registry(paths), make_snapshot(), states, TODAY)
    assert [item["project_id"] for item in queue["candidates"]] == [
        "never", "older", "newer"
    ]


def test_fairness_falls_back_to_last_run_when_no_run_has_merged(
    paths, write_project
):
    for project_id in ("never", "older-shadow", "newer-shadow"):
        _complete(
            write_project,
            project_id,
            priority="medium",
            automation={"mode": "shadow"},
        )
    states = {
        "older-shadow": ProjectBuildState(
            last_run_at="2026-05-01T12:00:00+00:00"
        ),
        "newer-shadow": ProjectBuildState(
            last_run_at="2026-07-01T12:00:00+00:00"
        ),
    }

    queue = build_queue(load_registry(paths), make_snapshot(), states, TODAY)

    assert [item["project_id"] for item in queue["candidates"]] == [
        "never", "older-shadow", "newer-shadow"
    ]
    reasons = {item["project_id"]: item["reasons"] for item in queue["candidates"]}
    assert reasons["never"][-1] == "never built"
    assert reasons["older-shadow"][-1] == "last run 85 d ago"


def test_blocked_and_open_decision_explain_the_gate(paths, write_project):
    _complete(write_project, "blocked", blocked_by="owner approval")
    _complete(
        write_project,
        "decision",
        brief={
            "done_criteria": ["Tests pass"],
            "open_decisions": [{"question": "Which database?", "status": "open"}],
        },
    )
    registry = load_registry(paths)

    blocked = classify(registry.require("blocked"), None, None, TODAY)
    decision = classify(registry.require("decision"), None, None, TODAY)
    assert blocked.state == "paused"
    assert blocked.paused_reason == "owner approval"
    assert "blocked_by" in blocked.reasons[0]
    assert decision.state == "needs_intent"
    assert "open_decision: Which database?" in decision.reasons


def test_queue_is_deterministic_when_registry_input_order_changes(paths, write_project):
    for project_id in ("charlie", "alpha", "bravo"):
        _complete(write_project, project_id)
    registry = load_registry(paths)
    reversed_registry = Registry(
        projects=dict(reversed(list(registry.projects.items()))),
        loaded_at=registry.loaded_at,
        paths=registry.paths,
    )
    first = build_queue(registry, make_snapshot(), {}, TODAY)
    second = build_queue(reversed_registry, make_snapshot(), {}, TODAY)
    assert [item["project_id"] for item in first["candidates"]] == [
        item["project_id"] for item in second["candidates"]
    ]


def test_shadow_mode_is_ready_but_dry_run(paths, write_project):
    _complete(write_project, "shadow", automation={"mode": "shadow"})
    result = classify(load_registry(paths).require("shadow"), None, None, TODAY)
    assert result.state == "ready"
    assert result.dry_run is True
