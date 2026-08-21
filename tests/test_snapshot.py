"""Branch snapshot serialization and tolerant GraphQL-node parsing."""

from __future__ import annotations

import datetime as dt

from project_registry.github.snapshot import Branch, RepoState, Snapshot
from project_registry.github.sync import branches_from_nodes

from .conftest import NOW


def test_branch_round_trip_preserves_every_field():
    branch = Branch(
        name="feat/café-☃",
        head_sha="abc123",
        committed_at=NOW,
        protected=True,
        is_default=False,
        open_pr_numbers=[7, 9],
    )

    assert Branch.from_dict(branch.to_dict()) == branch


def test_branch_round_trip_preserves_unknown_date_and_empty_prs():
    branch = Branch(name="unknown-date", head_sha="def456")

    restored = Branch.from_dict(branch.to_dict())

    assert restored.committed_at is None
    assert restored.open_pr_numbers == []
    assert restored == branch


def test_repo_state_with_branches_round_trips_through_snapshot():
    state = RepoState(
        full_name="owner/repo",
        branches=[Branch(name="main", head_sha="abc", committed_at=NOW, is_default=True)],
        branches_fetched=True,
        branches_fetched_at=NOW,
        branches_skipped=2,
        branches_partial=False,
    )
    snapshot = Snapshot(repos={state.full_name: state}, completed_at=NOW)

    restored = Snapshot.from_dict(snapshot.to_dict()).get("owner/repo")

    assert restored is not None
    assert restored.branches == state.branches
    assert restored.branches_fetched is True
    assert restored.branches_fetched_at == NOW
    assert restored.branches_skipped == 2
    assert restored.branches_partial is False


def test_old_repo_state_loads_branch_defaults_and_only_adds_new_keys():
    serialized = RepoState(full_name="owner/repo").to_dict()
    branch_keys = {
        "branches",
        "branches_fetched",
        "branches_fetched_at",
        "branches_skipped",
        "branches_partial",
        "branches_error",
    }
    old = {key: value for key, value in serialized.items() if key not in branch_keys}

    restored = RepoState.from_dict(old)
    rewritten = restored.to_dict()

    assert restored.branches == []
    assert restored.branches_fetched is False
    assert restored.branches_fetched_at is None
    assert restored.branches_skipped == 0
    assert restored.branches_partial is False
    assert restored.branches_error is None
    assert {key: rewritten[key] for key in old} == old
    assert set(rewritten) - set(old) == branch_keys


def test_malformed_branch_date_becomes_unknown_and_future_age_clamps_to_zero():
    malformed = Branch.from_dict(
        {"name": "bad-date", "head_sha": "abc", "committed_at": "not-a-date"}
    )
    future = Branch(
        name="future",
        head_sha="def",
        committed_at=NOW + dt.timedelta(days=1),
    )

    assert malformed.committed_at is None
    assert future.age_days(NOW) == 0


def test_missing_graphql_committed_date_is_skipped_not_silently_unknown():
    nodes = [
        {
            "name": "known",
            "target": {"oid": "abc", "committedDate": NOW.isoformat()},
            "branchProtectionRule": None,
        },
        {
            "name": "rest-shaped-silent-zero",
            "target": {"oid": "def"},
            "branchProtectionRule": None,
        },
    ]

    branches, skipped = branches_from_nodes(nodes, "main", "owner/repo")

    assert [branch.name for branch in branches] == ["known"]
    assert skipped == 1


def test_malformed_graphql_nodes_are_counted_without_crashing():
    nodes = [
        None,
        {},
        {"name": "no-target", "target": None},
        {"name": "no-oid", "target": {"committedDate": NOW.isoformat()}},
        {"name": "bad-date", "target": {"oid": "abc", "committedDate": "bad"}},
        {
            "name": "valid",
            "target": {
                "oid": "def",
                "committedDate": NOW.isoformat(),
                "associatedPullRequests": "malformed-but-nonfatal",
            },
        },
    ]

    branches, skipped = branches_from_nodes(nodes, "main", "owner/repo")

    assert [branch.name for branch in branches] == ["valid"]
    assert skipped == 5
