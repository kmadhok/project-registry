"""JSON-RPC 2.0 MCP server over stdio, standard library only.

Every tool result carries a ``source_timestamps`` block naming when the registry
was read and when the GitHub evidence behind the answer was fetched, so a client
can always tell live data from cached data (MCP-002).

Two invariants are enforced by tests rather than by convention:

* no advertised tool mutates GitHub (MCP-006);
* nothing is written to ``registry/`` without an explicit approval (MCP-005).
"""

from __future__ import annotations

import datetime as dt
import json
import re
import sys
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TextIO

from ..briefs import build_briefs_status, build_sync_status
from ..contracts import parse_contract, validate_contract
from ..github.client import GitHubClient
from ..github.sync import load_snapshot, sync
from ..model import Effort, Lifecycle, Priority
from ..proposals import (
    ProposalError,
    apply_proposal,
    list_proposals,
    load_proposal,
    propose_update,
    record_review,
)
from ..push_runs import build_push_report
from ..queries import (
    ProjectFilter,
    build_review_queue,
    build_work_queue,
    find_missing_next_actions,
    list_next_actions,
    list_open_prs,
    list_projects,
    list_related_projects,
    list_selected_issues,
    search_projects,
    source_timestamps,
)
from ..signals import build_attention_queue, describe_rules, find_mismatches
from ..specs import validate_spec
from ..storage import Paths, load_registry
from ..validation import validate

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "project-registry"
SERVER_VERSION = "0.1.0"

#: Verbs a tool name must never contain. Checked in tests as the machine-readable
#: form of "the initial MCP provides no merge, delete, archive, visibility-change,
#: or issue-closing tools" (MCP-006).
FORBIDDEN_TOOL_VERBS = re.compile(
    r"merge|close|delete|archive|visibility|dispatch|push_|comment|approve_pr", re.I
)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[Paths, dict[str, Any]], dict[str, Any]]
    story: str

    def advertise(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": f"{self.description} ({self.story})",
            "inputSchema": self.input_schema,
        }


def _schema(properties: dict[str, Any] | None = None, required: Iterable[str] = ()) -> dict:
    return {
        "type": "object",
        "properties": properties or {},
        "required": list(required),
        "additionalProperties": False,
    }


_FILTER_PROPERTIES = {
    "lifecycle": {"type": "array", "items": {"enum": [l.value for l in Lifecycle]}},
    "active": {"type": "boolean"},
    "category": {"type": "string"},
    "tag": {"type": "string"},
    "priority": {"type": "array", "items": {"enum": [p.value for p in Priority]}},
    "effort": {"type": "array", "items": {"enum": [e.value for e in Effort]}},
    "needs_review": {"type": "boolean"},
    "blocked": {"type": "boolean"},
}


def _filters_from(args: dict[str, Any]) -> ProjectFilter:
    return ProjectFilter(
        lifecycle=[Lifecycle(v) for v in args.get("lifecycle") or []],
        active=args.get("active"),
        category=args.get("category"),
        tag=args.get("tag"),
        priority=[Priority(v) for v in args.get("priority") or []],
        effort=[Effort(v) for v in args.get("effort") or []],
        needs_review=args.get("needs_review"),
        blocked=args.get("blocked"),
    )


def _context(paths: Paths):
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    now = dt.datetime.now(dt.timezone.utc)
    return registry, snapshot, now


def _envelope(registry, snapshot, now, payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "source_timestamps": source_timestamps(registry, snapshot, now)}


# -- MCP-001: query projects ---------------------------------------------


def tool_list_projects(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    projects = list_projects(registry, _filters_from(args))
    return _envelope(registry, snapshot, now, {
        "count": len(projects),
        "projects": [p.summary() for p in projects],
    })


def tool_get_project(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    project = registry.get(args["project_id"])
    if project is None:
        raise ToolError(f"unknown project id: {args['project_id']}")
    state = snapshot.get(project.repo)
    return _envelope(registry, snapshot, now, {
        "project": project.to_dict(),
        "source": project.source,
        "related": list_related_projects(registry, project.id),
        "github": state.to_dict() if state else None,
    })


def tool_search_projects(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    results = search_projects(registry, args["query"], limit=args.get("limit"))
    return _envelope(registry, snapshot, now, {
        "count": len(results),
        "results": [r.to_dict() for r in results],
    })


def tool_list_related_projects(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    if args["project_id"] not in registry:
        raise ToolError(f"unknown project id: {args['project_id']}")
    return _envelope(registry, snapshot, now, {
        "project_id": args["project_id"],
        "relationships": list_related_projects(registry, args["project_id"]),
    })


# -- MCP-002: next review or action --------------------------------------


def tool_list_next_actions(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    rows = list_next_actions(registry, _filters_from(args))
    return _envelope(registry, snapshot, now, {"count": len(rows), "next_actions": rows})


def tool_find_missing_next_actions(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    rows = find_missing_next_actions(registry)
    return _envelope(registry, snapshot, now, {"count": len(rows), "missing": rows})


def tool_list_projects_needing_review(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    queue = build_review_queue(registry, snapshot, now=now)
    return _envelope(registry, snapshot, now, {
        "count": len(queue),
        "review_queue": [item.to_dict() for item in queue],
        "note": "Staleness never changes a lifecycle; this is a read-only view.",
    })


def tool_get_attention_queue(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    """Curated next actions merged with live signals, each explaining itself."""
    registry, snapshot, now = _context(paths)
    queue = build_work_queue(registry, snapshot, now=now, filters=_filters_from(args))
    limit = args.get("limit")
    if limit:
        queue = queue[:limit]
    return _envelope(registry, snapshot, now, {
        "count": len(queue),
        "items": [item.to_dict() for item in queue],
        "note": (
            "human_priority is null when no priority is recorded; it is never "
            "given a default value."
        ),
    })


# -- MCP-003: GitHub work -------------------------------------------------


def tool_list_open_prs(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    result = list_open_prs(
        snapshot, registry, draft=args.get("draft"), repo=args.get("repo"), now=now
    )
    return _envelope(registry, snapshot, now, result)


def tool_get_pr_attention(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    items = build_attention_queue(registry, snapshot, now=now, repo=args.get("repo"))
    return _envelope(registry, snapshot, now, {
        "count": len(items),
        "items": [item.to_dict() for item in items],
        "rules": describe_rules()["attention"],
    })


def tool_list_selected_issues(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    result = list_selected_issues(snapshot, registry, labels=args.get("labels"), now=now)
    return _envelope(registry, snapshot, now, result)


def tool_get_github_sync_status(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    status = build_sync_status(registry, snapshot, paths=paths, now=now)
    return _envelope(registry, snapshot, now, {"status": status})


def tool_get_briefs_status(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    report = build_briefs_status(registry, snapshot, paths=paths, now=now)
    return _envelope(registry, snapshot, now, {"briefs": report})


def tool_get_push_report(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    report = build_push_report(
        paths=paths,
        since=args.get("since"),
        refresh=bool(args.get("refresh", False)),
        now=now,
    )
    return _envelope(registry, snapshot, now, {"report": report})


def tool_find_registry_mismatches(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    found = find_mismatches(registry, snapshot, now=now)
    return _envelope(registry, snapshot, now, {
        "count": len(found),
        "mismatches": [m.to_dict() for m in found],
        "rules": describe_rules()["mismatch"],
        "note": "Mismatches are reported, never resolved automatically.",
    })


def tool_validate_registry(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    return _envelope(registry, snapshot, now, validate(registry, today=now.date()).to_dict())


def tool_validate_contract(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    report = validate_contract(
        parse_contract(args["contract_text"]),
        expected_registry_id=args.get("project_id"),
    )
    return _envelope(registry, snapshot, now, report.to_dict())


def tool_validate_spec(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    project = registry.get(args["project_id"])
    if project is None:
        raise ToolError(f"unknown project id: {args['project_id']}")
    report = validate_spec(args["spec_text"], project=project)
    return _envelope(registry, snapshot, now, report.to_dict())


# -- MCP-004: refresh -----------------------------------------------------


def tool_refresh_github(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    """Re-read GitHub. Read-only with respect to GitHub; writes only the cache."""
    registry = load_registry(paths)
    client = GitHubClient()
    result = sync(
        registry, client, paths=paths,
        repos=args.get("repos"),
        with_details=args.get("with_details", True),
        with_branches=args.get("with_branches", True),
    )
    snapshot = result.snapshot or load_snapshot(paths)
    now = dt.datetime.now(dt.timezone.utc)
    return _envelope(registry, snapshot, now, {
        "started_at": result.to_dict()["started_at"],
        "completed_at": result.to_dict()["completed_at"],
        "coverage": result.coverage,
        "errors": result.errors,
        "partial_errors": result.partial_errors,
        "note": (
            "Repositories that failed to refresh kept their previous data, "
            "marked stale."
        ),
    })


# -- MCP-005: propose registry updates ------------------------------------


def tool_propose_project_update(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    """Record a proposed change. Writes no curated file."""
    registry, snapshot, now = _context(paths)
    proposal = propose_update(
        registry,
        args["project_id"],
        args["changes"],
        rationale=args.get("rationale"),
        paths=paths,
        now=now,
        source="mcp",
    )
    return _envelope(registry, snapshot, now, {
        "proposal": proposal.to_dict(),
        "diff": proposal.render_diff(),
        "applied": False,
        "note": (
            "Nothing was changed. Call apply_approved_project_update with "
            "approved=true to land this."
        ),
    })


def tool_apply_approved_project_update(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    result = apply_proposal(
        registry, args["proposal_id"], approved=bool(args.get("approved", False)),
        paths=paths, now=now,
    )
    return _envelope(registry, snapshot, now, result.to_dict())


def tool_list_project_update_proposals(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    found = list_proposals(paths, status=args.get("status"))
    return _envelope(registry, snapshot, now, {
        "count": len(found),
        "proposals": [p.to_dict() for p in found],
    })


def tool_get_project_update_proposal(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    proposal = load_proposal(args["proposal_id"], paths)
    return _envelope(registry, snapshot, now, {
        "proposal": proposal.to_dict(),
        "diff": proposal.render_diff(),
    })


def tool_record_project_review(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    registry, snapshot, now = _context(paths)
    reviewed_on = (
        dt.date.fromisoformat(args["reviewed_on"]) if args.get("reviewed_on") else now.date()
    )
    result = record_review(
        registry, args["project_id"],
        reviewed_on=reviewed_on,
        updates=args.get("updates"),
        rationale=args.get("rationale"),
        paths=paths, now=now,
        approved=bool(args.get("approved", False)),
    )
    return _envelope(registry, snapshot, now, result.to_dict())


def tool_describe_rules(paths: Paths, args: dict[str, Any]) -> dict[str, Any]:
    return {"rules": describe_rules()}


TOOLS: tuple[Tool, ...] = (
    Tool("list_projects", "List projects filtered by lifecycle, activity, category, tag, priority, or effort.",
         _schema(_FILTER_PROPERTIES), tool_list_projects, "MCP-001"),
    Tool("get_project", "Retrieve one project with its relationships and observed GitHub state.",
         _schema({"project_id": {"type": "string"}}, ["project_id"]), tool_get_project, "MCP-001"),
    Tool("search_projects", "Free-text search across curated project fields.",
         _schema({"query": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
         tool_search_projects, "MCP-001"),
    Tool("list_related_projects", "Relationships for a project, including inverse edges declared elsewhere.",
         _schema({"project_id": {"type": "string"}}, ["project_id"]),
         tool_list_related_projects, "MCP-001"),

    Tool("list_next_actions", "Curated next actions across projects.",
         _schema(_FILTER_PROPERTIES), tool_list_next_actions, "MCP-002"),
    Tool("find_missing_next_actions", "Active projects that have no next action.",
         _schema(), tool_find_missing_next_actions, "MCP-002"),
    Tool("list_projects_needing_review", "Projects due for review, by human review age and GitHub activity.",
         _schema(), tool_list_projects_needing_review, "MCP-002"),
    Tool("get_attention_queue", "Ranked work combining curated next actions with live GitHub signals.",
         _schema({**_FILTER_PROPERTIES, "limit": {"type": "integer"}}),
         tool_get_attention_queue, "MCP-002"),

    Tool("list_open_prs", "Open pull requests across the portfolio, from the last snapshot.",
         _schema({"draft": {"type": "boolean"}, "repo": {"type": "string"}}),
         tool_list_open_prs, "MCP-003"),
    Tool("get_pr_attention", "GitHub work needing attention, with the rule behind each signal.",
         _schema({"repo": {"type": "string"}}), tool_get_pr_attention, "MCP-003"),
    Tool("list_selected_issues", "Open issues carrying watched labels.",
         _schema({"labels": {"type": "array", "items": {"type": "string"}}}),
         tool_list_selected_issues, "MCP-003"),
    Tool("get_github_sync_status", "When GitHub evidence was last refreshed, with coverage and errors.",
         _schema(), tool_get_github_sync_status, "MCP-003"),
    Tool("get_briefs_status", "Evidence-brief presence, age, and revision staleness.",
         _schema(), tool_get_briefs_status, "MCP-003"),
    Tool("get_push_report", "Push-run totals and cached linked-PR states; refresh uses read-only GETs.",
         _schema({
             "refresh": {"type": "boolean"},
             "since": {"type": "string", "format": "date"},
         }), tool_get_push_report, "MCP-003"),
    Tool("find_registry_mismatches", "Conflicts between registry intent and observed GitHub state.",
         _schema(), tool_find_registry_mismatches, "MCP-003"),
    Tool("validate_registry", "Run the registry's own operating rules.",
         _schema(), tool_validate_registry, "MCP-003"),
    Tool("validate_contract", "Validate a target repository contract without changing it.",
         _schema({
             "contract_text": {"type": "string"},
             "project_id": {"type": "string"},
         }, ["contract_text"]), tool_validate_contract, "MCP-003"),
    Tool("validate_spec", "Validate an agent-owned repository roadmap without changing it.",
         _schema({
             "project_id": {"type": "string"},
             "spec_text": {"type": "string"},
         }, ["project_id", "spec_text"]), tool_validate_spec, "MCP-003"),

    Tool("refresh_github", "Re-read GitHub into the local snapshot. Read-only with respect to GitHub.",
         _schema({
             "repos": {"type": "array", "items": {"type": "string"}},
             "with_details": {"type": "boolean"},
             "with_branches": {"type": "boolean"},
         }), tool_refresh_github, "MCP-004"),

    Tool("propose_project_update", "Propose changes to curated fields. Writes a proposal only; changes nothing.",
         _schema({
             "project_id": {"type": "string"},
             "changes": {"type": "object"},
             "rationale": {"type": "string"},
         }, ["project_id", "changes"]), tool_propose_project_update, "MCP-005"),
    Tool("apply_approved_project_update", "Apply a previously proposed change. Requires approved=true.",
         _schema({
             "proposal_id": {"type": "string"},
             "approved": {"type": "boolean"},
         }, ["proposal_id", "approved"]), tool_apply_approved_project_update, "MCP-005"),
    Tool("list_project_update_proposals", "List pending, applied, or rejected proposals.",
         _schema({"status": {"enum": ["pending", "applied", "rejected"]}}),
         tool_list_project_update_proposals, "MCP-005"),
    Tool("get_project_update_proposal", "Show one proposal's exact before/after values.",
         _schema({"proposal_id": {"type": "string"}}, ["proposal_id"]),
         tool_get_project_update_proposal, "MCP-005"),
    Tool("record_project_review", "Stamp a review date and optionally update reviewed fields. Requires approved=true.",
         _schema({
             "project_id": {"type": "string"},
             "reviewed_on": {"type": "string"},
             "updates": {"type": "object"},
             "rationale": {"type": "string"},
             "approved": {"type": "boolean"},
         }, ["project_id", "approved"]), tool_record_project_review, "MCP-005"),

    Tool("describe_rules", "The signal and mismatch rule catalogue.",
         _schema(), tool_describe_rules, "US-011"),
)

TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


class ToolError(RuntimeError):
    """A tool call failed in a way the client should see as a tool error."""


# -- JSON-RPC plumbing ----------------------------------------------------


def handle_request(request: dict[str, Any], paths: Paths) -> dict[str, Any] | None:
    """Handle one JSON-RPC request. Returns None for notifications."""
    method = request.get("method")
    request_id = request.get("id")
    params = request.get("params") or {}

    if request_id is None and method and method.startswith("notifications/"):
        return None

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })

    if method == "ping":
        return _result(request_id, {})

    if method == "tools/list":
        return _result(request_id, {"tools": [tool.advertise() for tool in TOOLS]})

    if method == "tools/call":
        name = params.get("name")
        tool = TOOLS_BY_NAME.get(name)
        if tool is None:
            return _error(request_id, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        try:
            payload = tool.handler(paths, arguments)
        except (ToolError, ProposalError, KeyError, ValueError) as exc:
            return _result(request_id, {
                "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                "isError": True,
            })
        return _result(request_id, {
            "content": [
                {"type": "text", "text": json.dumps(payload, indent=2, default=str)}
            ],
            "isError": False,
        })

    return _error(request_id, -32601, f"unknown method: {method}")


def _result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(
    paths: Paths | None = None,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Run the stdio loop until EOF."""
    paths = paths or Paths.resolve()
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            _write(stdout, _error(None, -32700, "parse error"))
            continue

        response = handle_request(request, paths)
        if response is not None:
            _write(stdout, response)


def _write(stdout: TextIO, payload: dict[str, Any]) -> None:
    stdout.write(json.dumps(payload) + "\n")
    stdout.flush()
