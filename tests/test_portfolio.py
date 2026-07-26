"""Public-safe portfolio export (US-009)."""

from __future__ import annotations

import json

from project_registry.github.snapshot import Snapshot
from project_registry.portfolio import (
    PUBLIC_FIELDS,
    export_portfolio,
    is_publishable,
    render_portfolio_markdown,
)

from .conftest import NOW, make_repo_state, make_snapshot

SECRET = "ACME-CONFIDENTIAL-STRING"


def test_only_explicitly_public_projects_are_exported(write_project, load):
    write_project(id="private-one", purpose="p",
                  descriptions={"public_safe": "safe summary"})
    write_project(id="public-one", purpose="p", public=True,
                  descriptions={"public_safe": "safe summary"})
    document = export_portfolio(load(), Snapshot(), now=NOW)
    assert [r["id"] for r in document["projects"]] == ["public-one"]


def test_public_without_a_public_safe_summary_is_excluded(write_project, load):
    write_project(id="p", purpose="p", public=True,
                  descriptions={"private": "only private prose"})
    document = export_portfolio(load(), Snapshot(), now=NOW)
    assert document["projects"] == []


def test_export_never_leaks_private_fields(write_project, load):
    """The allowlist test: private content stuffed everywhere must not appear."""
    write_project(
        id="p",
        name="Public Name",
        purpose=f"internal purpose {SECRET}",
        desired_outcome=f"internal outcome {SECRET}",
        notes=f"private notes {SECRET}",
        blocked_by=f"blocked by {SECRET}",
        category="tooling",
        public=True,
        repo="owner/private-repo",
        descriptions={"private": f"private description {SECRET}",
                      "public_safe": "a safe summary"},
        accomplishments=[{"date": "2026-01-01", "summary": f"did {SECRET}"}],
        next_action={"description": f"do {SECRET}", "reviewed": "2026-01-01"},
    )
    document = export_portfolio(load(), Snapshot(), now=NOW)
    serialized = json.dumps(document)
    assert SECRET not in serialized
    assert "private-repo" not in serialized
    assert document["projects"][0]["summary"] == "a safe summary"


def test_exported_records_contain_only_allowlisted_keys(write_project, load):
    write_project(id="p", purpose="p", public=True, category="tooling", tags=["a"],
                  showcase_order=1, descriptions={"public_safe": "safe"},
                  accomplishments=[{"date": "2026-01-01", "summary": "shipped", "public": True}])
    record = export_portfolio(load(), Snapshot(), now=NOW)["projects"][0]
    assert set(record) <= set(PUBLIC_FIELDS)


def test_private_repo_name_is_withheld(write_project, load):
    """US-009: private repository details are excluded by default."""
    write_project(id="p", purpose="p", public=True, repo="owner/repo",
                  descriptions={"public_safe": "safe"})
    snapshot = make_snapshot(make_repo_state("owner/repo", private=True))
    record = export_portfolio(load(), snapshot, now=NOW)["projects"][0]
    assert "repo" not in record
    assert "url" not in record


def test_public_repo_name_is_included(write_project, load):
    write_project(id="p", purpose="p", public=True, repo="owner/repo",
                  descriptions={"public_safe": "safe"})
    snapshot = make_snapshot(make_repo_state("owner/repo", private=False))
    record = export_portfolio(load(), snapshot, now=NOW)["projects"][0]
    assert record["repo"] == "owner/repo"
    assert record["url"] == "https://github.com/owner/repo"


def test_repo_is_withheld_when_there_is_no_snapshot(write_project, load):
    """With no evidence the answer is 'no', not 'probably fine'."""
    write_project(id="p", purpose="p", public=True, repo="owner/repo",
                  descriptions={"public_safe": "safe"})
    record = export_portfolio(load(), Snapshot(), now=NOW)["projects"][0]
    assert "repo" not in record


def test_accomplishments_need_their_own_opt_in(write_project, load):
    write_project(
        id="p", purpose="p", public=True, descriptions={"public_safe": "safe"},
        accomplishments=[
            {"date": "2026-02-02", "summary": "public milestone", "public": True},
            {"date": "2026-01-01", "summary": f"private milestone {SECRET}"},
        ],
    )
    record = export_portfolio(load(), Snapshot(), now=NOW)["projects"][0]
    assert [h["summary"] for h in record["highlights"]] == ["public milestone"]


def test_ordering_is_human_curated(write_project, load):
    """US-009: showcase ordering is curated, never derived from activity."""
    write_project(id="third", name="Aaa", purpose="p", public=True, showcase_order=3,
                  descriptions={"public_safe": "s"})
    write_project(id="first", name="Zzz", purpose="p", public=True, showcase_order=1,
                  descriptions={"public_safe": "s"})
    write_project(id="unordered", name="Mmm", purpose="p", public=True,
                  descriptions={"public_safe": "s"})
    document = export_portfolio(load(), Snapshot(), now=NOW)
    assert [r["id"] for r in document["projects"]] == ["first", "third", "unordered"]


def test_is_publishable_requires_both_gates(write_project, load):
    write_project(id="p", purpose="p", public=True, descriptions={"public_safe": "s"})
    write_project(id="q", purpose="p", public=True)
    registry = load()
    assert is_publishable(registry.require("p")) is True
    assert is_publishable(registry.require("q")) is False


def test_markdown_rendering_uses_the_same_allowlisted_data(write_project, load):
    write_project(id="p", name="Thing", purpose=f"secret {SECRET}", public=True,
                  descriptions={"public_safe": "a safe summary"})
    text = render_portfolio_markdown(export_portfolio(load(), Snapshot(), now=NOW))
    assert "## Thing" in text
    assert "a safe summary" in text
    assert SECRET not in text


def test_empty_portfolio_renders_a_clear_message(write_project, load):
    write_project(id="p", purpose="p")
    text = render_portfolio_markdown(export_portfolio(load(), Snapshot(), now=NOW))
    assert "No projects are marked public" in text
