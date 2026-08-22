"""GitHub sync, inventory import, and read-only guarantees (US-001, MCP-004, MCP-006)."""

from __future__ import annotations

import datetime as dt
import inspect
import re

import pytest

from project_registry.github import client as client_module
from project_registry.github.client import (
    GitHubClient,
    GitHubError,
    derive_ci_state,
    derive_review_state,
)
from project_registry.github.importer import import_inventory, slugify, unique_id
from project_registry.github.sync import load_snapshot, save_snapshot, sync
from project_registry.signals import find_mismatches
from project_registry.storage import load_registry, read_json

from .conftest import NOW, make_repo_state, make_snapshot


class FakeClient(GitHubClient):
    """A client that answers from canned data and records what was asked."""

    def __init__(
        self, repos=None, pulls=None, issues=None, branches=None, fail=(), branch_fail=()
    ):
        super().__init__(token=None)
        self.repos = repos or {}
        self.pulls = pulls or {}
        self.issues = issues or {}
        self.branches = branches or {}
        self.fail = set(fail)
        self.branch_fail = set(branch_fail)
        self.calls: list[str] = []

    def get_repo(self, full_name):
        self.calls.append(f"get_repo:{full_name}")
        if full_name in self.fail:
            raise GitHubError(f"{full_name}: boom", 500)
        return self.repos[full_name]

    def list_open_pulls(self, full_name):
        return self.pulls.get(full_name, [])

    def list_open_issues(self, full_name):
        return self.issues.get(full_name, [])

    def list_reviews(self, full_name, number):
        return []

    def list_check_runs(self, full_name, ref):
        return []

    def get_combined_status(self, full_name, ref):
        return {}

    def list_branch_nodes(self, full_name):
        self.calls.append(f"list_branch_nodes:{full_name}")
        if full_name in self.branch_fail:
            raise GitHubError(f"{full_name}: branches boom", 502)
        return self.branches.get(full_name, ([], False, 0))


def repo_payload(full_name, **overrides):
    payload = {
        "full_name": full_name,
        "name": full_name.split("/")[-1],
        "private": True,
        "archived": False,
        "fork": False,
        "default_branch": "main",
        "description": "a repo",
        "html_url": f"https://github.com/{full_name}",
        "pushed_at": "2026-07-20T00:00:00Z",
        "updated_at": "2026-07-20T00:00:00Z",
        "open_issues_count": 0,
    }
    payload.update(overrides)
    return payload


def branch_node(name, *, date="2026-01-01T00:00:00Z", prs=None, protected=False):
    return {
        "name": name,
        "branchProtectionRule": {"id": "rule"} if protected else None,
        "target": {
            "oid": f"sha-{name}",
            "committedDate": date,
            "associatedPullRequests": {"nodes": prs or []},
        },
    }


# -- MCP-006: the client cannot mutate ------------------------------------


def test_client_only_ever_issues_get_requests():
    """MCP-006 allows only REST GET and guarded GraphQL query POST."""
    source = inspect.getsource(client_module)
    assert client_module.ALLOWED_METHOD == "GET"
    assert client_module.ALLOWED_METHODS == frozenset({"GET", "POST"})
    verbs = set(re.findall(r'method\s*=\s*"([A-Z]+)"', source))
    assert verbs <= {"GET", "POST"}
    assert source.count('method="POST"') == inspect.getsource(GitHubClient.graphql).count(
        'method="POST"'
    ) == 1
    assert not re.search(r'\b(PUT|PATCH|DELETE)\b', source)


def test_sync_never_writes_under_the_registry_directory(paths, write_project):
    """Operating rule 7, made structural."""
    path = write_project(id="p", purpose="curated purpose", repo="owner/repo")
    before = path.read_text(encoding="utf-8")
    before_mtime = path.stat().st_mtime

    registry = load_registry(paths)
    client = FakeClient(repos={"owner/repo": repo_payload("owner/repo")})
    sync(registry, client, paths=paths, now=NOW)

    assert path.read_text(encoding="utf-8") == before
    assert path.stat().st_mtime == before_mtime
    assert paths.snapshot_file.exists()


# -- MCP-004: refresh semantics -------------------------------------------


def test_sync_records_start_completion_coverage_and_errors(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    write_project(id="b", purpose="p", repo="owner/two")
    registry = load_registry(paths)
    client = FakeClient(
        repos={"owner/one": repo_payload("owner/one"), "owner/two": repo_payload("owner/two")},
        fail={"owner/two"},
    )
    result = sync(registry, client, paths=paths, now=NOW)

    assert result.started_at == NOW
    assert result.completed_at == NOW
    assert result.coverage == {
        "repos_requested": 2,
        "repos_succeeded": 1,
        "repos_failed": 1,
        "repos_partial": 0,
        "complete": False,
        "failed_repos": ["owner/two"],
    }
    assert result.errors[0]["repo"] == "owner/two"


def test_partial_failure_keeps_previous_data_and_marks_it_stale(paths, write_project):
    """MCP-004: partial failure does not overwrite the last known good data."""
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)

    good = FakeClient(repos={"owner/one": repo_payload("owner/one", description="first")})
    sync(registry, good, paths=paths, now=NOW)

    broken = FakeClient(repos={}, fail={"owner/one"})
    later = NOW + dt.timedelta(hours=1)
    result = sync(registry, broken, paths=paths, now=later)

    state = result.snapshot.get("owner/one")
    assert state is not None, "previous data should survive a failed refresh"
    assert state.description == "first"
    assert state.stale is True
    assert "boom" in state.error
    assert result.coverage["complete"] is False


def test_targeted_sync_merges_with_previous_snapshot(paths, write_project):
    """A targeted refresh replaces only its target and keeps other evidence intact."""
    for name in ("a", "b", "c"):
        write_project(id=name, purpose="p", repo=f"owner/{name}")
    registry = load_registry(paths)
    initial = FakeClient(
        repos={
            f"owner/{name}": repo_payload(f"owner/{name}", description=f"old {name}")
            for name in ("a", "b", "c")
        }
    )
    sync(registry, initial, paths=paths, now=NOW)
    before = read_json(paths.snapshot_file)["repos"]

    later = NOW + dt.timedelta(hours=1)
    result = sync(
        registry,
        FakeClient(repos={"owner/a": repo_payload("owner/a", description="fresh a")}),
        paths=paths,
        repos=["owner/a"],
        now=later,
    )
    after = read_json(paths.snapshot_file)["repos"]

    assert set(after) == {"owner/a", "owner/b", "owner/c"}
    assert result.snapshot.get("owner/a").description == "fresh a"
    assert result.snapshot.get("owner/a").fetched_at == later
    assert after["owner/b"] == before["owner/b"]
    assert after["owner/c"] == before["owner/c"]
    assert result.repos_requested == ["owner/a"]
    assert result.coverage["repos_requested"] == 1
    assert result.snapshot.started_at == later
    assert result.snapshot.completed_at == later

    mismatches = find_mismatches(registry, result.snapshot, now=later)
    missing_repos = {item.repo for item in mismatches if item.rule_id == "repo_missing"}
    assert not {"owner/b", "owner/c"} & missing_repos


def test_failed_target_is_carried_forward_stale_without_dropping_non_targets(
    paths, write_project
):
    for name in ("a", "b"):
        write_project(id=name, purpose="p", repo=f"owner/{name}")
    previous = make_snapshot(
        make_repo_state("owner/a"),
        make_repo_state("owner/b"),
    )
    previous.get("owner/a").description = "last known a"
    save_snapshot(previous, paths)
    before_b = previous.get("owner/b").to_dict()

    result = sync(
        load_registry(paths),
        FakeClient(fail={"owner/a"}),
        paths=paths,
        repos=["owner/a"],
        now=NOW + dt.timedelta(hours=1),
    )

    carried = result.snapshot.get("owner/a")
    assert carried.description == "last known a"
    assert carried.stale is True
    assert "boom" in carried.error
    assert result.snapshot.get("owner/b").to_dict() == before_b
    assert result.coverage["repos_requested"] == 1
    assert result.coverage["repos_failed"] == 1


def test_full_sync_replaces_snapshot_and_drops_repos_not_in_registry(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/a")
    save_snapshot(
        make_snapshot(make_repo_state("owner/a"), make_repo_state("owner/removed")),
        paths,
    )

    result = sync(
        load_registry(paths),
        FakeClient(repos={"owner/a": repo_payload("owner/a", description="fresh")}),
        paths=paths,
        now=NOW + dt.timedelta(hours=1),
    )

    assert set(result.snapshot.repos) == {"owner/a"}
    assert result.snapshot.get("owner/a").description == "fresh"
    assert result.coverage["repos_requested"] == 1


def test_successful_refresh_clears_staleness(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)
    sync(registry, FakeClient(repos={}, fail={"owner/one"}), paths=paths, now=NOW)
    result = sync(
        registry,
        FakeClient(repos={"owner/one": repo_payload("owner/one")}),
        paths=paths,
        now=NOW + dt.timedelta(hours=1),
    )
    assert result.snapshot.get("owner/one").stale is False


def test_sync_persists_pull_requests_and_issues(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)
    client = FakeClient(
        repos={"owner/one": repo_payload("owner/one")},
        pulls={"owner/one": [{
            "number": 3, "title": "Add thing", "html_url": "https://github.com/owner/one/pull/3",
            "draft": False, "user": {"login": "someone"},
            "created_at": "2026-07-01T00:00:00Z", "updated_at": "2026-07-02T00:00:00Z",
            "head": {"sha": "abc"}, "labels": [{"name": "enhancement"}],
        }]},
        issues={"owner/one": [{
            "number": 9, "title": "A bug", "html_url": "https://github.com/owner/one/issues/9",
            "user": {"login": "someone"}, "labels": [{"name": "bug"}],
            "created_at": "2026-06-01T00:00:00Z", "updated_at": "2026-06-01T00:00:00Z",
        }]},
    )
    sync(registry, client, paths=paths, now=NOW)

    reloaded = load_snapshot(paths)
    state = reloaded.get("owner/one")
    assert [pr.number for pr in state.pull_requests] == [3]
    assert state.pull_requests[0].review_state == "unreviewed"
    assert [issue.number for issue in state.issues] == [9]


def test_snapshot_status_reports_staleness_and_coverage(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)
    sync(registry, FakeClient(repos={"owner/one": repo_payload("owner/one")}),
         paths=paths, now=NOW)
    status = load_snapshot(paths).status(NOW + dt.timedelta(days=3))
    assert status["stale"] is True
    assert status["coverage"]["repos_succeeded"] == 1
    assert status["repo_count"] == 1


# -- branch refresh semantics --------------------------------------------


def test_sync_fetches_sorted_branches_and_maps_only_open_in_repo_prs(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    prs = [
        {"number": 1, "state": "CLOSED", "headRepository": {"nameWithOwner": "owner/one"}},
        {"number": 2, "state": "OPEN", "headRepository": {"nameWithOwner": "owner/one"}},
        {"number": 3, "state": "OPEN", "headRepository": {"nameWithOwner": "fork/one"}},
        {"number": 4, "state": "OPEN", "headRepository": None},
    ]
    client = FakeClient(
        repos={"owner/one": repo_payload("owner/one")},
        branches={"owner/one": ([
            branch_node("z-feature", prs=prs, protected=True),
            branch_node("main"),
            branch_node("a-feature"),
        ], False, 3)},
    )

    result = sync(load_registry(paths), client, paths=paths, now=NOW)
    state = result.snapshot.get("owner/one")

    assert [branch.name for branch in state.branches] == ["a-feature", "main", "z-feature"]
    assert state.branches[1].is_default is True
    assert state.branches[2].protected is True
    assert state.branches[2].open_pr_numbers == [2, 4]
    assert state.branches_fetched is True
    assert state.branches_partial is False
    assert state.branches_fetched_at == NOW
    assert state.branches_error is None


def test_partial_branch_fetch_is_retained_and_reported_without_repo_failure(
    paths, write_project
):
    write_project(id="a", purpose="p", repo="owner/one")
    client = FakeClient(
        repos={"owner/one": repo_payload("owner/one")},
        branches={"owner/one": ([branch_node("feature/one")], True, 1251)},
    )

    result = sync(load_registry(paths), client, paths=paths, now=NOW)
    state = result.snapshot.get("owner/one")

    assert [branch.name for branch in state.branches] == ["feature/one"]
    assert state.branches_fetched is False
    assert state.branches_partial is True
    assert state.branches_fetched_at == NOW
    assert "1251" in state.branches_error
    assert result.repos_succeeded == ["owner/one"]
    assert result.repos_failed == []
    assert result.coverage["repos_partial"] == 1
    assert result.partial_errors == [{
        "repo": "owner/one", "error": state.branches_error, "status": None,
    }]


def test_branch_failure_carries_forward_previous_branch_observation(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)
    first = FakeClient(
        repos={"owner/one": repo_payload("owner/one")},
        branches={"owner/one": ([branch_node("feature/one")], False, 1)},
    )
    sync(registry, first, paths=paths, now=NOW)

    later = NOW + dt.timedelta(hours=1)
    broken = FakeClient(
        repos={"owner/one": repo_payload("owner/one", description="fresh metadata")},
        branch_fail={"owner/one"},
    )
    result = sync(registry, broken, paths=paths, now=later)
    state = result.snapshot.get("owner/one")

    assert state.description == "fresh metadata"
    assert [branch.name for branch in state.branches] == ["feature/one"]
    assert state.branches_fetched is False
    assert state.branches_fetched_at == NOW
    assert state.stale is False
    assert "branches boom" in state.branches_error
    assert result.repos_failed == []
    assert result.coverage["repos_partial"] == 1


def test_no_branches_skips_client_and_carries_forward_without_an_error(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)
    sync(
        registry,
        FakeClient(
            repos={"owner/one": repo_payload("owner/one")},
            branches={"owner/one": ([branch_node("feature/one")], False, 1)},
        ),
        paths=paths,
        now=NOW,
    )
    client = FakeClient(repos={"owner/one": repo_payload("owner/one")})

    result = sync(
        registry,
        client,
        paths=paths,
        now=NOW + dt.timedelta(hours=1),
        with_branches=False,
    )
    state = result.snapshot.get("owner/one")

    assert not any(call.startswith("list_branch_nodes:") for call in client.calls)
    assert [branch.name for branch in state.branches] == ["feature/one"]
    assert state.branches_fetched is False
    assert state.branches_fetched_at == NOW
    assert state.branches_error is None
    assert result.partial_errors == []


def test_malformed_branch_nodes_are_counted_not_crashed(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    nodes = [
        branch_node("good"),
        {"name": "", "target": {"oid": "bad", "committedDate": "2026-01-01T00:00:00Z"}},
        {"name": "no-target", "target": None},
        branch_node("no-date", date=None),
    ]
    result = sync(
        load_registry(paths),
        FakeClient(
            repos={"owner/one": repo_payload("owner/one")},
            branches={"owner/one": (nodes, False, 4)},
        ),
        paths=paths,
        now=NOW,
    )
    state = result.snapshot.get("owner/one")
    assert [branch.name for branch in state.branches] == ["good"]
    assert state.branches_skipped == 3
    assert state.branches_fetched is True


def test_later_success_clears_a_previous_branch_error(paths, write_project):
    write_project(id="a", purpose="p", repo="owner/one")
    registry = load_registry(paths)
    sync(
        registry,
        FakeClient(
            repos={"owner/one": repo_payload("owner/one")},
            branch_fail={"owner/one"},
        ),
        paths=paths,
        now=NOW,
    )
    result = sync(
        registry,
        FakeClient(
            repos={"owner/one": repo_payload("owner/one")},
            branches={"owner/one": ([branch_node("fixed")], False, 1)},
        ),
        paths=paths,
        now=NOW + dt.timedelta(hours=1),
    )
    state = result.snapshot.get("owner/one")
    assert state.branches_fetched is True
    assert state.branches_error is None
    assert result.partial_errors == []


# -- derivation helpers ---------------------------------------------------


@pytest.mark.parametrize(
    "reviews,expected",
    [
        ([], "unreviewed"),
        ([{"state": "APPROVED", "user": {"login": "a"}}], "approved"),
        ([{"state": "CHANGES_REQUESTED", "user": {"login": "a"}}], "changes_requested"),
        (
            [
                {"state": "APPROVED", "user": {"login": "a"}},
                {"state": "CHANGES_REQUESTED", "user": {"login": "b"}},
            ],
            "changes_requested",
        ),
        (
            [
                {"state": "CHANGES_REQUESTED", "user": {"login": "a"}},
                {"state": "APPROVED", "user": {"login": "a"}},
            ],
            "approved",
        ),
        ([{"state": "COMMENTED", "user": {"login": "a"}}], "commented"),
    ],
)
def test_review_state_derivation(reviews, expected):
    assert derive_review_state(reviews) == expected


@pytest.mark.parametrize(
    "runs,combined,expected",
    [
        ([{"status": "completed", "conclusion": "success"}], None, "success"),
        ([{"status": "completed", "conclusion": "failure"}], None, "failure"),
        ([{"status": "in_progress", "conclusion": None}], None, "pending"),
        ([], {"state": "failure", "statuses": [{}]}, "failure"),
        ([], {"state": "", "statuses": []}, "none"),
        ([], None, "unknown"),
    ],
)
def test_ci_state_derivation(runs, combined, expected):
    assert derive_ci_state(runs, combined) == expected


def test_unknown_ci_state_is_never_reported_as_success():
    assert derive_ci_state([], None) == "unknown"


# -- US-001: inventory import ---------------------------------------------


def test_import_creates_stubs_marked_needs_review(paths):
    registry = load_registry(paths)
    result = import_inventory(
        registry,
        [repo_payload("owner/alpha"), repo_payload("owner/beta")],
        paths=paths,
    )
    assert len(result.created) == 2

    reloaded = load_registry(paths)
    alpha = reloaded.require("alpha")
    assert alpha.needs_review is True
    assert alpha.purpose is None
    assert alpha.repo == "owner/alpha"
    assert alpha.active is False, "import must not infer activity from GitHub"
    assert alpha.lifecycle.value == "incubating"


def test_import_never_overwrites_a_curated_file(paths, write_project):
    path = write_project(id="alpha", purpose="carefully written purpose", repo="owner/alpha")
    before = path.read_text(encoding="utf-8")
    registry = load_registry(paths)

    result = import_inventory(registry, [repo_payload("owner/alpha")], paths=paths)

    assert result.created == []
    assert result.skipped_existing == ["owner/alpha"]
    assert path.read_text(encoding="utf-8") == before


def test_import_records_github_description_privately(paths):
    registry = load_registry(paths)
    import_inventory(registry, [repo_payload("owner/alpha", description="does things")],
                     paths=paths)
    project = load_registry(paths).require("alpha")
    assert project.descriptions.private == "does things"
    assert project.descriptions.public_safe is None, "a GitHub blurb is not public-safe by default"


def test_import_skips_forks_unless_asked(paths):
    registry = load_registry(paths)
    result = import_inventory(
        registry, [repo_payload("owner/forked", fork=True)], paths=paths
    )
    assert result.created == []
    assert result.skipped_forks == ["owner/forked"]

    registry = load_registry(paths)
    result = import_inventory(
        registry, [repo_payload("owner/forked", fork=True)], paths=paths, include_forks=True
    )
    assert len(result.created) == 1
    assert load_registry(paths).require("forked").is_fork is True


def test_import_sets_visibility_from_github(paths):
    registry = load_registry(paths)
    import_inventory(registry, [repo_payload("owner/pub", private=False)], paths=paths)
    assert load_registry(paths).require("pub").visibility.value == "public"


def test_import_dry_run_writes_nothing(paths):
    registry = load_registry(paths)
    result = import_inventory(registry, [repo_payload("owner/alpha")], paths=paths, write=False)
    assert len(result.created) == 1
    assert list(paths.projects_dir.glob("*.yaml")) == []


def test_import_derives_unique_ids_for_colliding_names(paths):
    registry = load_registry(paths)
    import_inventory(
        registry,
        [repo_payload("one/thing"), repo_payload("two/thing")],
        paths=paths,
    )
    ids = sorted(p.stem for p in paths.projects_dir.glob("*.yaml"))
    assert ids == ["thing", "thing-2"]


def test_slug_helpers():
    assert slugify("My Cool Project!") == "my-cool-project"
    assert slugify("---") == "project"
    assert unique_id("thing", {"thing", "thing-2"}) == "thing-3"
