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
from project_registry.github.sync import load_snapshot, sync
from project_registry.storage import load_registry

from .conftest import NOW


class FakeClient(GitHubClient):
    """A client that answers from canned data and records what was asked."""

    def __init__(self, repos=None, pulls=None, issues=None, fail=()):
        super().__init__(token=None)
        self.repos = repos or {}
        self.pulls = pulls or {}
        self.issues = issues or {}
        self.fail = set(fail)
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


# -- MCP-006: the client cannot mutate ------------------------------------


def test_client_only_ever_issues_get_requests():
    """MCP-006, enforced at the bottom of the stack."""
    source = inspect.getsource(client_module)
    assert client_module.ALLOWED_METHOD == "GET"
    verbs = set(re.findall(r'method\s*=\s*"([A-Z]+)"', source))
    assert verbs <= {"GET"}
    assert not re.search(r'\b(POST|PUT|PATCH|DELETE)\b', source)


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
