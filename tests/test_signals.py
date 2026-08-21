"""Attention signals and registry/GitHub mismatches (US-010, US-011, US-012)."""

from __future__ import annotations

import datetime as dt

from project_registry.github.snapshot import Snapshot
from project_registry.queries import list_open_prs, list_selected_issues
from project_registry.signals import (
    ATTENTION_RULES,
    MISMATCH_RULES,
    SignalConfig,
    build_attention_queue,
    classify_branches,
    describe_rules,
    find_mismatches,
    stale_branches,
)
from project_registry.validation import ERROR, SUGGESTION

from .conftest import NOW, make_branch, make_issue, make_pr, make_repo_state, make_snapshot


def rule_ids(items):
    return {item.rule_id for item in items}


# -- US-010: open pull requests -------------------------------------------


def test_open_prs_report_the_six_required_fields(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(number=7, title="Add parser", review_state="approved", ci_state="pending",
                created_days_ago=4)
    ]))
    row = list_open_prs(snapshot, load(), now=NOW)["pull_requests"][0]
    assert row["repo"] == "owner/repo"
    assert row["title"] == "Add parser"
    assert row["draft"] is False
    assert row["age_days"] == 4
    assert row["review_state"] == "approved"
    assert row["ci_state"] == "pending"
    assert row["project_id"] == "p"


def test_draft_filter_partitions_pull_requests(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(number=1, draft=True), make_pr(number=2, draft=False)
    ]))
    registry = load()
    drafts = list_open_prs(snapshot, registry, draft=True, now=NOW)["pull_requests"]
    ready = list_open_prs(snapshot, registry, draft=False, now=NOW)["pull_requests"]
    both = list_open_prs(snapshot, registry, draft=None, now=NOW)["pull_requests"]
    assert [r["number"] for r in drafts] == [1]
    assert [r["number"] for r in ready] == [2]
    assert len(both) == 2


def test_cached_results_carry_their_refresh_time(write_project, load):
    """US-010: cached results display their refresh time."""
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo"))
    result = list_open_prs(snapshot, load(), now=NOW)
    assert result["fetched_at"] == NOW.isoformat()
    assert result["stale"] is False


def test_old_snapshot_is_marked_stale(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    old = NOW - dt.timedelta(days=5)
    snapshot = make_snapshot(make_repo_state("owner/repo"), completed_at=old)
    result = list_open_prs(snapshot, load(), now=NOW)
    assert result["stale"] is True
    assert "d ago" in result["age"]


def test_empty_snapshot_reports_never_fetched(write_project, load):
    write_project(id="p", purpose="p")
    result = list_open_prs(Snapshot(), load(), now=NOW)
    assert result["pull_requests"] == []
    assert result["age"] == "never"
    assert result["stale"] is True


# -- US-011: attention signals --------------------------------------------


def test_every_attention_rule_fires_on_its_fixture(write_project, load):
    """US-011: the full signal set, each identified by its rule."""
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state(
        "owner/repo",
        pull_requests=[
            make_pr(number=1, ci_state="failure"),
            make_pr(number=2, review_state="changes_requested"),
            make_pr(number=3, created_days_ago=30, updated_days_ago=20),
            make_pr(number=4, created_days_ago=10, updated_days_ago=0),
            make_pr(number=5, draft=True, created_days_ago=60),
        ],
        issues=[make_issue(number=9, labels=["blocked"])],
    ))
    items = build_attention_queue(load(), snapshot, now=NOW)
    assert rule_ids(items) == {
        "ci_failing", "changes_requested", "stale_pr",
        "unreviewed_pr", "draft_pr_aging", "selected_issue",
    }


def test_every_attention_item_links_to_its_source(write_project, load):
    """US-011: each signal links to its GitHub source."""
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(number=1, ci_state="failure")
    ], issues=[make_issue(number=2, labels=["bug"])]))
    items = build_attention_queue(load(), snapshot, now=NOW)
    assert items
    assert all(item.url.startswith("https://github.com/") for item in items)


def test_attention_items_explain_the_rule_that_produced_them(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(number=1, ci_state="failure")
    ]))
    item = build_attention_queue(load(), snapshot, now=NOW)[0]
    assert item.rule_id == "ci_failing"
    assert item.reason == "checks are failing"
    assert item.severity == ERROR


def test_draft_prs_are_not_reported_as_unreviewed(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(number=1, draft=True, created_days_ago=5)
    ]))
    assert "unreviewed_pr" not in rule_ids(build_attention_queue(load(), snapshot, now=NOW))


def test_attention_queue_can_be_scoped_to_one_repo(write_project, load):
    write_project(id="p", purpose="p", repo="owner/one")
    snapshot = make_snapshot(
        make_repo_state("owner/one", pull_requests=[make_pr(repo="owner/one", ci_state="failure")]),
        make_repo_state("owner/two", pull_requests=[make_pr(repo="owner/two", ci_state="failure")]),
    )
    items = build_attention_queue(load(), snapshot, now=NOW, repo="owner/one")
    assert {i.repo for i in items} == {"owner/one"}


def test_selected_issues_are_filtered_by_label(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", issues=[
        make_issue(number=1, labels=["blocked"]),
        make_issue(number=2, labels=["documentation"]),
    ]))
    rows = list_selected_issues(snapshot, load(), now=NOW)["issues"]
    assert [r["number"] for r in rows] == [1]
    assert rows[0]["matched_labels"] == ["blocked"]


def test_unknown_ci_state_is_not_treated_as_failing(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(number=1, ci_state="unknown", created_days_ago=0, review_state="approved")
    ]))
    assert "ci_failing" not in rule_ids(build_attention_queue(load(), snapshot, now=NOW))


# -- stale branches ------------------------------------------------------


def test_stale_branch_threshold_is_inclusive_and_future_is_not_stale():
    state = make_repo_state(branches=[
        make_branch("at-threshold", committed_days_ago=90),
        make_branch("younger", committed_days_ago=89),
        make_branch("future", committed_days_ago=-1),
    ], branches_fetched=True)
    found = stale_branches(state, NOW, SignalConfig(stale_branch_days=90))
    assert [branch.name for branch in found] == ["at-threshold"]


def test_stale_branch_classifier_excludes_default_open_pr_and_unknown_dates():
    state = make_repo_state(branches=[
        make_branch("main", committed_days_ago=200, is_default=True),
        make_branch("reviewed", committed_days_ago=200, open_pr_numbers=[7]),
        make_branch("unknown", committed_days_ago=None),
        make_branch("claude/old", committed_days_ago=200),
        make_branch("claude/older", committed_days_ago=210),
        make_branch("feat/old", committed_days_ago=100),
    ], branches_fetched=True)
    counts = classify_branches(state, NOW, SignalConfig())
    assert counts.total == 6
    assert counts.stale == 3
    assert counts.open_pr_heads == 1
    assert counts.groups == (("claude/", 2), ("feat/", 1))
    assert counts.oldest_days == 210


def test_stale_branches_emit_one_repo_scoped_attention_item(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    state = make_repo_state("owner/repo", branches=[
        make_branch("claude/x", committed_days_ago=210),
        make_branch("claude/y", committed_days_ago=200),
        make_branch("feat/z", committed_days_ago=100),
    ], branches_fetched=True)
    items = [
        item for item in build_attention_queue(load(), make_snapshot(state), now=NOW)
        if item.rule_id == "stale_branches"
    ]
    assert len(items) == 1
    item = items[0]
    assert item.kind == "branch"
    assert item.number is None
    assert item.age_days is None
    assert item.title == "owner/repo"
    assert item.url == "https://github.com/owner/repo"
    assert item.reason == (
        "3 stale branches (2 claude/, 1 feat/), oldest 210d"
    )


def test_stale_branch_reason_caps_groups_and_counts_hidden_branches(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    state = make_repo_state("owner/repo", branches=[
        make_branch("a/one"), make_branch("a/two"), make_branch("b/one"),
        make_branch("c/one"), make_branch("d/one"),
    ], branches_fetched=True)
    item = next(
        item for item in build_attention_queue(load(), make_snapshot(state), now=NOW)
        if item.rule_id == "stale_branches"
    )
    assert item.reason == "5 stale branches (2 a/, 1 b/, 1 c/, +1 more), oldest 100d"


def test_unfetched_or_partial_branches_never_emit_a_known_stale_count(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    state = make_repo_state(
        "owner/repo",
        branches=[make_branch("old")],
        branches_fetched=False,
        branches_partial=True,
        branches_error="truncated",
    )
    assert "stale_branches" not in rule_ids(
        build_attention_queue(load(), make_snapshot(state), now=NOW)
    )


def test_attention_sort_accepts_repo_and_numbered_items_together(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo")
    state = make_repo_state(
        "owner/repo",
        branches=[make_branch("old")],
        branches_fetched=True,
        issues=[make_issue(number=2, labels=["bug"])],
    )
    items = build_attention_queue(load(), make_snapshot(state), now=NOW)
    assert {(item.kind, item.number) for item in items} == {("issue", 2), ("branch", None)}


# -- US-012: mismatches ---------------------------------------------------


def test_missing_repository_is_an_error(write_project, load):
    write_project(id="p", purpose="p", repo="owner/gone")
    snapshot = make_snapshot(make_repo_state("owner/other"))
    found = find_mismatches(load(), snapshot, now=NOW)
    assert [m.rule_id for m in found if m.severity == ERROR] == ["repo_missing"]


def test_archived_in_registry_but_pushed_on_github_is_an_error(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", lifecycle="archived")
    snapshot = make_snapshot(make_repo_state("owner/repo", pushed_days_ago=3))
    found = find_mismatches(load(), snapshot, now=NOW)
    assert any(m.rule_id == "archived_but_github_active" and m.severity == ERROR for m in found)


def test_archived_on_github_but_live_in_registry_is_an_error(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", lifecycle="now",
                  desired_outcome="d", active=True,
                  next_action={"description": "Ship the thing", "reviewed": "2026-07-24"})
    snapshot = make_snapshot(make_repo_state("owner/repo", archived=True))
    found = find_mismatches(load(), snapshot, now=NOW)
    assert any(m.rule_id == "github_archived_but_registry_active" for m in found)


def test_private_in_registry_but_public_on_github_is_an_error(write_project, load):
    """The risky direction of visibility drift."""
    write_project(id="p", purpose="p", repo="owner/repo", visibility="private")
    snapshot = make_snapshot(make_repo_state("owner/repo", private=False))
    found = find_mismatches(load(), snapshot, now=NOW)
    match = next(m for m in found if m.rule_id == "visibility_exposed")
    assert match.severity == ERROR


def test_public_in_registry_but_private_on_github_is_a_suggestion(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", visibility="public")
    snapshot = make_snapshot(make_repo_state("owner/repo", private=True))
    found = find_mismatches(load(), snapshot, now=NOW)
    match = next(m for m in found if m.rule_id == "visibility_drift")
    assert match.severity == SUGGESTION


def test_active_project_without_recent_review_is_a_suggestion(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", active=True,
                  last_reviewed="2026-01-01",
                  next_action={"description": "Decide the format", "reviewed": "2026-01-01"})
    snapshot = make_snapshot(make_repo_state("owner/repo"))
    found = find_mismatches(load(), snapshot, now=NOW)
    match = next(m for m in found if m.rule_id == "active_without_recent_review")
    assert match.severity == SUGGESTION


def test_inactive_project_with_recent_pushes_is_a_suggestion_not_a_promotion(write_project, load):
    """Operating rule 6: activity is evidence, never priority."""
    write_project(id="p", purpose="p", repo="owner/repo", active=False)
    snapshot = make_snapshot(make_repo_state("owner/repo", pushed_days_ago=1))
    found = find_mismatches(load(), snapshot, now=NOW)
    match = next(m for m in found if m.rule_id == "inactive_but_recent_pushes")
    assert match.severity == SUGGESTION
    assert load().require("p").active is False


def test_fork_flag_drift_is_detected(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", is_fork=False)
    snapshot = make_snapshot(make_repo_state("owner/repo", fork=True))
    found = find_mismatches(load(), snapshot, now=NOW)
    assert any(m.rule_id == "fork_flag_drift" for m in found)


def test_no_mismatches_are_reported_before_a_first_sync(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", lifecycle="archived")
    assert find_mismatches(load(), Snapshot(), now=NOW) == []


def test_agreeing_registry_and_github_produce_no_mismatches(write_project, load):
    write_project(id="p", purpose="p", repo="owner/repo", visibility="private",
                  active=True, last_reviewed="2026-07-20",
                  next_action={"description": "Decide the format", "reviewed": "2026-07-20"})
    snapshot = make_snapshot(make_repo_state("owner/repo", private=True))
    assert find_mismatches(load(), snapshot, now=NOW) == []


def test_mismatches_are_reported_not_resolved(write_project, load, paths):
    path = write_project(id="p", purpose="p", repo="owner/repo", lifecycle="archived")
    before = path.read_text(encoding="utf-8")
    snapshot = make_snapshot(make_repo_state("owner/repo", pushed_days_ago=1))
    assert find_mismatches(load(), snapshot, now=NOW)
    assert path.read_text(encoding="utf-8") == before


def test_rule_catalogue_covers_every_rule():
    catalogue = describe_rules()
    assert {r["id"] for r in catalogue["attention"]} == {r.id for r in ATTENTION_RULES}
    assert {r["id"] for r in catalogue["mismatch"]} == {r.id for r in MISMATCH_RULES}
    assert all(r["description"] for r in catalogue["attention"])
