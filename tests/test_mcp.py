"""MCP server behaviour (MCP-001 … MCP-006)."""

from __future__ import annotations

import io
import json

import pytest

from project_registry.build import append_event
from project_registry.cli import main
from project_registry.github.sync import save_snapshot
from project_registry.mcp.server import (
    FORBIDDEN_TOOL_VERBS,
    PROTOCOL_VERSION,
    TOOLS,
    TOOLS_BY_NAME,
    handle_request,
    serve,
)
from project_registry.proposals import PENDING, list_proposals
from project_registry.storage import load_registry

from .conftest import make_branch, make_pr, make_repo_state, make_snapshot


def call(paths, name, arguments=None):
    response = handle_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        paths,
    )
    result = response["result"]
    payload = json.loads(result["content"][0]["text"])
    return payload, result.get("isError", False)


@pytest.fixture
def seeded(paths, write_project):
    write_project(
        id="alpha", name="Alpha", purpose="Parse invoices", repo="owner/alpha",
        lifecycle="now", desired_outcome="Ships to production", active=True,
        priority="high", category="tooling", last_reviewed="2026-07-20",
        next_action={"description": "Decide the storage format", "reviewed": "2026-07-20"},
        relationships=[{"kind": "successor", "target": "beta"}],
    )
    write_project(id="beta", name="Beta", purpose="An earlier attempt", lifecycle="reference")
    save_snapshot(
        make_snapshot(make_repo_state("owner/alpha", pull_requests=[
            make_pr(repo="owner/alpha", number=4, ci_state="failure")
        ])),
        paths,
    )
    return paths


# -- protocol -------------------------------------------------------------


def test_initialize_advertises_tools(paths):
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, paths)
    assert response["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert "tools" in response["result"]["capabilities"]


def test_notifications_get_no_response(paths):
    assert handle_request({"jsonrpc": "2.0", "method": "notifications/initialized"}, paths) is None


def test_unknown_method_is_a_jsonrpc_error(paths):
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "nope"}, paths)
    assert response["error"]["code"] == -32601


def test_unknown_tool_is_a_jsonrpc_error(paths):
    response = handle_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "ghost"}}, paths
    )
    assert response["error"]["code"] == -32602


def test_tools_list_exposes_schemas_and_story_ids(paths):
    response = handle_request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, paths)
    tools = response["result"]["tools"]
    assert len(tools) == len(TOOLS)
    assert all(tool["inputSchema"]["type"] == "object" for tool in tools)
    assert any("MCP-001" in tool["description"] for tool in tools)


def test_serve_round_trips_over_stdio(seeded):
    stdin = io.StringIO(
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n"
    )
    stdout = io.StringIO()
    serve(seeded, stdin=stdin, stdout=stdout)

    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [1, 2]


def test_malformed_json_gets_a_parse_error(paths):
    stdout = io.StringIO()
    serve(paths, stdin=io.StringIO("not json\n"), stdout=stdout)
    assert json.loads(stdout.getvalue())["error"]["code"] == -32700


# -- MCP-001: query projects ----------------------------------------------


def test_list_projects_and_filters(seeded):
    payload, is_error = call(seeded, "list_projects")
    assert not is_error
    assert payload["count"] == 2

    payload, _ = call(seeded, "list_projects", {"lifecycle": ["now"]})
    assert [p["id"] for p in payload["projects"]] == ["alpha"]

    payload, _ = call(
        seeded, "list_projects", {"active": True, "category": "tooling"}
    )
    assert [p["id"] for p in payload["projects"]] == ["alpha"]

    payload, _ = call(seeded, "list_projects", {"active": False})
    assert [p["id"] for p in payload["projects"]] == ["beta"]


def test_get_project_includes_relationships_and_github_state(seeded):
    payload, _ = call(seeded, "get_project", {"project_id": "alpha"})
    assert payload["project"]["purpose"] == "Parse invoices"
    assert payload["related"][0]["target"] == "beta"
    assert payload["github"]["full_name"] == "owner/alpha"


def test_get_project_unknown_id_is_a_structured_tool_error(seeded):
    payload, is_error = call(seeded, "get_project", {"project_id": "ghost"})
    assert is_error is True
    assert "unknown project id" in payload["error"]


def test_search_projects(seeded):
    payload, _ = call(seeded, "search_projects", {"query": "invoices"})
    assert [r["id"] for r in payload["results"]] == ["alpha"]


def test_list_related_projects_returns_inverse_edges(seeded):
    payload, _ = call(seeded, "list_related_projects", {"project_id": "beta"})
    assert payload["relationships"][0]["kind"] == "predecessor"
    assert payload["relationships"][0]["inferred"] is True


# -- MCP-002: next action / review recommendations ------------------------


def test_every_response_carries_source_timestamps(seeded):
    for name in (
        "list_projects",
        "list_next_actions",
        "find_missing_next_actions",
        "list_projects_needing_review",
        "get_attention_queue",
        "list_open_prs",
        "get_github_sync_status",
    ):
        payload, _ = call(seeded, name)
        stamps = payload["source_timestamps"]
        assert stamps["registry_loaded_at"]
        assert "github_fetched_at" in stamps
        assert "github_stale" in stamps


def test_attention_queue_items_carry_reasons(seeded):
    payload, _ = call(seeded, "get_attention_queue")
    assert payload["items"]
    assert all(item["reasons"] for item in payload["items"])


def test_get_brief_status_matches_cli(paths, write_project, capsys):
    write_project(
        id="p", purpose="why", desired_outcome="result", repo="owner/p",
        automation={"mode": "build"}, brief={"done_criteria": ["Tests pass"]},
    )
    payload, is_error = call(paths, "get_brief_status", {"stale": True})
    assert not is_error

    code = main([
        "--root", str(paths.root), "brief-status", "--stale", "--json"
    ])
    cli_payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["projects"] == cli_payload["projects"]
    assert payload["summary"] == cli_payload["summary"]


def test_get_brief_status_tool_name_is_allowed():
    assert "get_brief_status" in TOOLS_BY_NAME
    assert FORBIDDEN_TOOL_VERBS.search("get_brief_status") is None


def test_attention_queue_reports_null_for_unrecorded_priority(paths, write_project):
    """MCP-002: never invent a project priority."""
    write_project(id="p", purpose="p", active=True,
                  next_action={"description": "Decide the format", "reviewed": "2026-07-20"})
    payload, _ = call(paths, "get_attention_queue")
    assert payload["items"][0]["human_priority"] is None


def test_branch_state_and_signal_flow_through_shared_mcp_queries(paths, write_project):
    write_project(id="p", purpose="p", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state(
        "owner/repo",
        branches=[make_branch("old")],
        branches_fetched=True,
    )), paths)

    project, _ = call(paths, "get_project", {"project_id": "p"})
    assert project["github"]["branches"][0]["name"] == "old"
    assert project["github"]["branches_fetched"] is True

    payload, is_error = call(paths, "get_attention_queue")
    assert not is_error
    branch_item = next(
        item for item in payload["items"] if "stale_branches" in item["rule_ids"]
    )
    assert branch_item["title"].startswith("owner/repo")
    assert "#None" not in json.dumps(payload)


def test_missing_next_actions_and_review_queue(paths, write_project):
    write_project(id="p", purpose="p", active=True)
    payload, _ = call(paths, "find_missing_next_actions")
    assert payload["count"] == 1

    payload, _ = call(paths, "list_projects_needing_review")
    assert payload["count"] >= 1


# -- MCP-003: GitHub work -------------------------------------------------


def test_list_open_prs_and_attention(seeded):
    payload, _ = call(seeded, "list_open_prs")
    assert payload["pull_requests"][0]["number"] == 4
    assert payload["fetched_at"]

    payload, _ = call(seeded, "get_pr_attention")
    assert payload["items"][0]["rule_id"] == "ci_failing"
    assert any(rule["id"] == "ci_failing" for rule in payload["rules"])


def test_sync_status_and_mismatches(seeded):
    payload, _ = call(seeded, "get_github_sync_status")
    assert payload["status"]["repo_count"] == 1

    payload, _ = call(seeded, "find_registry_mismatches")
    assert "mismatches" in payload
    assert "never resolved automatically" in payload["note"]


def test_selected_issues_tool(seeded):
    payload, _ = call(seeded, "list_selected_issues", {"labels": ["bug"]})
    assert payload["issues"] == []


def test_validate_registry_tool(paths, write_project):
    write_project(id="p")  # no purpose
    payload, _ = call(paths, "validate_registry")
    assert payload["ok"] is False
    assert payload["error_count"] == 1


def test_validate_contract_tool_returns_report_in_standard_envelope(paths):
    payload, is_error = call(paths, "validate_contract", {
        "contract_text": (
            'schema: 1\nregistry_id: target\n'
            'runtime: {kind: python, version: "3.11"}\n'
            'test: ["python3 -m pytest -q"]\n'
        ),
        "project_id": "target",
    })
    assert not is_error
    assert payload["ok"] is True
    assert payload["runnable"] is True
    assert payload["contract"]["registry_id"] == "target"
    assert payload["source_timestamps"]["registry_loaded_at"]


def test_build_report_cli_and_mcp_return_same_numbers(paths, capsys):
    append_event(paths, {
        "run_id": "run-1", "host": "mac", "type": "chunk_started",
        "project_id": "alpha", "chunk_id": "c1",
        "ts": "2026-08-22T12:00:00+00:00",
    })
    append_event(paths, {
        "run_id": "run-1", "host": "mac", "type": "merged",
        "project_id": "alpha", "chunk_id": "c1",
        "ts": "2026-08-22T12:15:00+00:00",
    })
    append_event(paths, {
        "run_id": "run-1", "host": "mac", "type": "run_finished",
        "project_id": "alpha", "outcome": "completed",
        "ts": "2026-08-22T12:16:00+00:00",
    })

    assert main(["--root", str(paths.root), "build-report", "--json"]) == 0
    cli_report = json.loads(capsys.readouterr().out)
    payload, is_error = call(paths, "get_build_report")
    assert not is_error
    mcp_report = payload["report"]
    assert mcp_report["runs"] == cli_report["runs"]
    assert mcp_report["chunks"] == cli_report["chunks"]
    assert mcp_report["minutes_per_merged_chunk"] == cli_report["minutes_per_merged_chunk"]


def test_build_queue_cli_and_mcp_are_identical(paths, write_project, capsys):
    write_project(
        id="builder", purpose="Ship it", desired_outcome="It ships",
        repo="owner/builder", brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build"}, priority="high",
    )
    write_project(id="manual", purpose="Human work", repo="owner/manual")

    assert main(["--root", str(paths.root), "build-queue", "--json"]) == 0
    cli_queue = json.loads(capsys.readouterr().out)
    payload, is_error = call(paths, "get_build_queue")
    assert not is_error
    assert payload["candidates"] == cli_queue["candidates"]
    assert payload["by_state"] == cli_queue["by_state"]
    assert payload["ready_count"] == cli_queue["ready_count"]

    readiness, is_error = call(
        paths, "validate_project_readiness", {"project_id": "builder"}
    )
    assert not is_error
    assert readiness["state"] == "ready"
    assert readiness["policy"]["mode"] == "build"


def test_build_lifecycle_mcp_begin_context_finish(paths, write_project):
    write_project(
        id="builder", name="Builder", purpose="Ship it",
        desired_outcome="It ships", repo="owner/builder",
        brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build"},
    )
    context, is_error = call(paths, "get_build_context", {"project_id": "builder"})
    assert not is_error
    assert context["eligibility"]["state"] == "ready"

    started, is_error = call(paths, "begin_build_run", {
        "host": "mac", "project_id": "builder",
    })
    assert not is_error
    assert started["candidate"]["project_id"] == context["project_id"]
    assert started["candidate"]["contract_expectations"] == context["contract_expectations"]

    finished, is_error = call(paths, "finish_build_run", {
        "run_id": started["run_id"], "outcome": "completed",
    })
    assert not is_error
    assert finished["state_after"]["last_outcome"] == "completed"
    assert not paths.build_lease_file.exists()

    error, is_error = call(
        paths, "validate_project_readiness", {"project_id": "missing"}
    )
    assert is_error
    assert error["error"] == "unknown project id: missing"


def test_validate_spec_tool_matches_validator_and_rejects_unknown_id(paths, write_project):
    write_project(id="target", automation={"allow": []})
    spec_text = (
        "## Remaining work\n- [ ] Add one test\n"
        "      Acceptance: the test passes\n      Tests: tests/test_one.py\n"
        "      Size: S\n      Classes: none\n"
        "      Verified-missing: the test does not exist\n"
    )
    payload, is_error = call(
        paths, "validate_spec", {"project_id": "target", "spec_text": spec_text}
    )
    assert not is_error
    assert payload["next_ready_index"] == 1
    assert payload["source_timestamps"]["registry_loaded_at"]

    payload, is_error = call(
        paths, "validate_spec", {"project_id": "missing", "spec_text": spec_text}
    )
    assert is_error
    assert payload["error"] == "unknown project id: missing"


# -- MCP-005: proposals ---------------------------------------------------


def test_propose_tool_changes_nothing(seeded):
    payload, is_error = call(seeded, "propose_project_update", {
        "project_id": "alpha", "changes": {"purpose": "A new purpose"},
        "rationale": "clarified after review",
    })
    assert not is_error
    assert payload["applied"] is False
    assert payload["proposal"]["changes"]["purpose"]["before"] == "Parse invoices"
    assert load_registry(seeded).require("alpha").purpose == "Parse invoices"


def test_propose_tool_accepts_a_brief_path(seeded):
    payload, is_error = call(
        seeded,
        "propose_project_update",
        {
            "project_id": "alpha",
            "changes": {"brief.done_criteria": "parser succeeds,tests pass"},
        },
    )
    assert not is_error
    change = payload["proposal"]["changes"]["brief.done_criteria"]
    assert change == {"before": None, "after": ["parser succeeds", "tests pass"]}
    assert load_registry(seeded).require("alpha").brief.done_criteria == []


def test_apply_requires_approved_true(seeded):
    payload, _ = call(seeded, "propose_project_update", {
        "project_id": "alpha", "changes": {"purpose": "A new purpose"},
    })
    proposal_id = payload["proposal"]["id"]

    payload, is_error = call(seeded, "apply_approved_project_update", {
        "proposal_id": proposal_id, "approved": False,
    })
    assert is_error is True
    assert "explicit approval" in payload["error"]
    assert load_registry(seeded).require("alpha").purpose == "Parse invoices"

    payload, is_error = call(seeded, "apply_approved_project_update", {
        "proposal_id": proposal_id, "approved": True,
    })
    assert not is_error
    assert payload["applied"] is True
    assert load_registry(seeded).require("alpha").purpose == "A new purpose"


def test_proposals_can_be_listed_and_inspected(seeded):
    payload, _ = call(seeded, "propose_project_update", {
        "project_id": "alpha", "changes": {"category": "research"},
    })
    proposal_id = payload["proposal"]["id"]

    payload, _ = call(seeded, "list_project_update_proposals", {"status": "pending"})
    assert payload["count"] == 1

    payload, _ = call(seeded, "get_project_update_proposal", {"proposal_id": proposal_id})
    assert "before:" in payload["diff"]


@pytest.mark.parametrize("approval", [None, False])
def test_record_review_without_true_approval_is_an_unmistakable_error(
    seeded, approval
):
    arguments = {"project_id": "alpha", "reviewed_on": "2026-07-25"}
    if approval is not None:
        arguments["approved"] = approval

    payload, is_error = call(seeded, "record_project_review", arguments)

    assert is_error is True
    pending = list_proposals(seeded, status=PENDING)
    assert len(pending) == 1
    assert f"pending proposal {pending[0].id} filed" in payload["error"]
    assert "NOTHING applied" in payload["error"]
    assert "approved=true" in payload["error"]
    assert str(load_registry(seeded).require("alpha").last_reviewed) == "2026-07-20"


def test_record_review_with_true_approval_applies(seeded):
    payload, is_error = call(seeded, "record_project_review", {
        "project_id": "alpha", "reviewed_on": "2026-07-25", "approved": True,
    })
    assert not is_error
    assert str(load_registry(seeded).require("alpha").last_reviewed) == "2026-07-25"


# -- MCP-006: no external mutations ---------------------------------------


def test_no_tool_name_suggests_an_external_mutation():
    """MCP-006: no merge, close, delete, archive, or visibility tool exists."""
    offenders = [
        tool.name for tool in TOOLS
        if tool.name != "get_push_report" and FORBIDDEN_TOOL_VERBS.search(tool.name)
    ]
    assert offenders == []
    assert "read-only GETs" in TOOLS_BY_NAME["get_push_report"].description


def test_the_only_write_tools_target_the_registry_itself():
    write_tools = {
        "propose_project_update",
        "apply_approved_project_update",
        "record_project_review",
        "begin_build_run",
        "record_build_event",
        "finish_build_run",
        "reconcile_build_runs",
    }
    # refresh_github writes only the local cache; everything else is a read.
    assert write_tools <= set(TOOLS_BY_NAME)
    for name in write_tools:
        assert "GitHub" not in TOOLS_BY_NAME[name].description or name == "refresh_github"
    for name in {
        "begin_build_run", "record_build_event", "finish_build_run",
        "reconcile_build_runs",
    }:
        assert "writes only under data/build/" in TOOLS_BY_NAME[name].description


def test_apply_and_review_tools_require_approval_in_their_schema():
    for name in ("apply_approved_project_update", "record_project_review"):
        assert "approved" in TOOLS_BY_NAME[name].input_schema["required"]
