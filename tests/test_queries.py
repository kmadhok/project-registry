"""Queries, work queue, and review queue (US-001, US-005, US-006, US-008)."""

from __future__ import annotations

import json

from project_registry.model import Lifecycle, Priority
from project_registry.queries import (
    ProjectFilter,
    build_review_queue,
    build_work_queue,
    find_missing_next_actions,
    list_next_actions,
    list_projects,
    list_related_projects,
    search_projects,
)

from .conftest import NOW, make_branch, make_pr, make_repo_state, make_snapshot


def test_filters_compose(write_project, load):
    """US-006: filter by lifecycle, category, priority, effort, blocker."""
    write_project(id="a", purpose="p", lifecycle="now", desired_outcome="d",
                  category="tooling", priority="high", effort="small")
    write_project(id="b", purpose="p", lifecycle="next", category="tooling", priority="low")
    write_project(id="c", purpose="p", lifecycle="now", desired_outcome="d",
                  category="research", priority="high")
    registry = load()

    result = list_projects(registry, ProjectFilter(
        lifecycle=[Lifecycle.NOW], category="tooling", priority=[Priority.HIGH]
    ))
    assert [p.id for p in result] == ["a"]


def test_blocked_filter(write_project, load):
    write_project(id="blocked", purpose="p", blocked_by="waiting on the API team")
    write_project(id="free", purpose="p")
    result = list_projects(load(), ProjectFilter(blocked=True))
    assert [p.id for p in result] == ["blocked"]


def test_search_reports_which_fields_matched(write_project, load):
    write_project(id="parser", purpose="Parse invoices reliably", tags=["invoices"])
    write_project(id="other", purpose="Something unrelated")
    results = search_projects(load(), "invoice")
    assert len(results) == 1
    assert results[0].project.id == "parser"
    assert set(results[0].matched_fields) >= {"purpose", "tags"}


def test_search_matches_accomplishments(write_project, load):
    write_project(
        id="p", purpose="unrelated",
        accomplishments=[{"date": "2026-01-01", "summary": "Shipped the widget exporter"}],
    )
    results = search_projects(load(), "widget exporter")
    assert [r.project.id for r in results] == ["p"]


def test_empty_search_returns_nothing(write_project, load):
    write_project(id="p", purpose="p")
    assert search_projects(load(), "   ") == []


def test_related_projects_include_inferred_inverse_edges(write_project, load):
    """US-007: the reverse edge is visible from the other side."""
    write_project(id="old", purpose="p", lifecycle="superseded",
                  relationships=[{"kind": "successor", "target": "new"}])
    write_project(id="new", purpose="p")

    edges = list_related_projects(load(), "new")
    assert len(edges) == 1
    assert edges[0]["kind"] == "predecessor"
    assert edges[0]["inferred"] is True
    assert edges[0]["declared_by"] == "old"


def test_declared_edge_is_not_duplicated_by_its_inverse(write_project, load):
    write_project(id="a", purpose="p", relationships=[{"kind": "related", "target": "b"}])
    write_project(id="b", purpose="p", relationships=[{"kind": "related", "target": "a"}])
    edges = list_related_projects(load(), "a")
    assert len(edges) == 1
    assert edges[0]["inferred"] is False


def test_broken_relationship_is_marked_unresolved(write_project, load):
    write_project(id="a", purpose="p", relationships=[{"kind": "related", "target": "ghost"}])
    edges = list_related_projects(load(), "a")
    assert edges[0]["resolved"] is False


def test_missing_next_actions_are_reported(write_project, load):
    """US-005: missing next actions are reported."""
    write_project(id="active-no-action", purpose="p", active=True)
    write_project(id="committed", purpose="p", lifecycle="next")
    write_project(id="fine", purpose="p", active=True,
                  next_action={"description": "Decide the format", "reviewed": "2026-07-01"})
    write_project(id="archived", purpose="p", lifecycle="archived")

    rows = find_missing_next_actions(load())
    ids = {r["project_id"] for r in rows}
    assert ids == {"active-no-action", "committed"}


def test_list_next_actions_includes_review_date_and_link(write_project, load):
    write_project(
        id="p", purpose="p", active=True, priority="high",
        next_action={
            "description": "Decide whether to merge the parser",
            "reviewed": "2026-07-20",
            "link": "https://github.com/owner/repo/pull/3",
        },
    )
    row = list_next_actions(load())[0]
    assert row["reviewed"] == "2026-07-20"
    assert row["link"].endswith("/pull/3")
    assert row["priority"] == "high"


# -- US-008: review queue -------------------------------------------------


def test_review_queue_triggers_on_stale_human_review(write_project, load):
    write_project(id="p", purpose="p", lifecycle="now", desired_outcome="d",
                  active=True, last_reviewed="2026-01-01",
                  next_action={"description": "Ship the thing", "reviewed": "2026-01-01"})
    queue = build_review_queue(load(), make_snapshot(), now=NOW)
    assert len(queue) == 1
    assert any("last reviewed" in reason for reason in queue[0].reasons)


def test_review_queue_triggers_on_stale_github_activity(write_project, load):
    """US-008: staleness uses GitHub activity as an independent input."""
    write_project(id="p", purpose="p", lifecycle="now", desired_outcome="d",
                  repo="owner/repo", active=True, last_reviewed="2026-07-24",
                  next_action={"description": "Ship the thing", "reviewed": "2026-07-24"})
    snapshot = make_snapshot(make_repo_state("owner/repo", pushed_days_ago=200))
    queue = build_review_queue(load(), snapshot, now=NOW)
    assert len(queue) == 1
    assert any("no GitHub activity" in reason for reason in queue[0].reasons)
    assert queue[0].days_since_activity == 200


def test_review_queue_leaves_the_registry_untouched(write_project, load, paths):
    """US-008: staleness never changes a lifecycle."""
    path = write_project(id="p", purpose="p", lifecycle="now", desired_outcome="d",
                         last_reviewed="2020-01-01")
    before = path.read_text(encoding="utf-8")
    build_review_queue(load(), make_snapshot(), now=NOW)
    assert path.read_text(encoding="utf-8") == before


def test_fresh_project_is_not_in_the_review_queue(write_project, load):
    write_project(id="p", purpose="p", lifecycle="maintained", active=True,
                  last_reviewed="2026-07-20",
                  next_action={"description": "Renew the certificate", "reviewed": "2026-07-20"})
    assert build_review_queue(load(), make_snapshot(), now=NOW) == []


# -- US-006: work queue ---------------------------------------------------


def test_work_queue_keeps_human_priority_and_github_urgency_separate(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", active=True, priority="medium",
                  next_action={"description": "Decide the schema", "reviewed": "2026-07-20"})
    snapshot = make_snapshot(
        make_repo_state("owner/repo", pull_requests=[make_pr(ci_state="failure")])
    )
    item = build_work_queue(load(), snapshot, now=NOW)[0]
    assert item.human_priority == "medium"
    assert item.github_urgency == 100
    assert item.to_dict()["human_priority"] == "medium"


def test_work_queue_never_invents_a_priority(write_project, load):
    """MCP-002: a project with no recorded priority reports null."""
    write_project(id="p", purpose="p", active=True,
                  next_action={"description": "Decide the schema", "reviewed": "2026-07-20"})
    item = build_work_queue(load(), make_snapshot(), now=NOW)[0]
    assert item.human_priority is None
    assert item.sort_score == 0


def test_work_queue_item_lists_every_contributing_reason(write_project, load):
    """US-006: the view explains why each item appears."""
    write_project(id="p", purpose="p", repo="owner/repo", active=True,
                  next_action={"description": "Decide the schema", "reviewed": "2026-07-20"})
    snapshot = make_snapshot(
        make_repo_state("owner/repo", pull_requests=[
            make_pr(number=1, ci_state="failure", review_state="changes_requested")
        ])
    )
    item = build_work_queue(load(), snapshot, now=NOW)[0]
    assert any("curated next action" in r for r in item.reasons)
    assert any("ci_failing" in r for r in item.reasons)
    assert any("changes_requested" in r for r in item.reasons)


def test_work_queue_ranks_recorded_priority_above_unprioritized(write_project, load):
    write_project(id="high", purpose="p", active=True, priority="high",
                  next_action={"description": "Decide the schema", "reviewed": "2026-07-20"})
    write_project(id="none", purpose="p", active=True,
                  next_action={"description": "Decide the layout", "reviewed": "2026-07-20"})
    queue = build_work_queue(load(), make_snapshot(), now=NOW)
    assert [i.project_id for i in queue] == ["high", "none"]


def test_work_queue_respects_project_filters(write_project, load):
    write_project(id="tool", purpose="p", category="tooling", active=True,
                  next_action={"description": "Decide the schema", "reviewed": "2026-07-20"})
    write_project(id="research", purpose="p", category="research", active=True,
                  next_action={"description": "Read the paper set", "reviewed": "2026-07-20"})
    queue = build_work_queue(load(), make_snapshot(), now=NOW,
                             filters=ProjectFilter(category="tooling"))
    assert [i.project_id for i in queue] == ["tool"]


def test_unregistered_repo_signal_appears_when_unfiltered(write_project, load):
    write_project(id="p", purpose="p")
    snapshot = make_snapshot(
        make_repo_state("stranger/repo", pull_requests=[make_pr(repo="stranger/repo",
                                                               ci_state="failure")])
    )
    queue = build_work_queue(load(), snapshot, now=NOW)
    assert len(queue) == 1
    assert queue[0].project_id is None


def test_branch_signal_reference_never_renders_none(write_project, load):
    write_project(id="p", purpose="p")
    snapshot = make_snapshot(make_repo_state(
        "stranger/repo",
        branches=[make_branch("old")],
        branches_fetched=True,
    ))
    item = build_work_queue(load(), snapshot, now=NOW)[0]
    rendered = json.dumps(item.to_dict())
    assert item.title == "stranger/repo stranger/repo"
    assert item.reasons == [
        "stranger/repo 1 stale branches (1 old), oldest 100d [stale_branches]"
    ]
    assert "#None" not in rendered
