"""Command line interface.

Every command reads through :mod:`queries` / :mod:`signals`, the same functions
the MCP server calls, so the two surfaces cannot answer the same question
differently.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .briefs import build_briefs_status, build_sync_status
from .contracts import parse_contract, validate_contract
from .dashboard import render_dashboard
from .github.client import GitHubClient, GitHubError
from .github.importer import fetch_inventory, import_inventory
from .github.sync import load_snapshot, sync
from .model import Effort, Lifecycle, Priority, RegistryError
from .portfolio import export_portfolio, render_portfolio_markdown
from .proposals import (
    ProposalError,
    apply_proposal,
    list_proposals,
    load_proposal,
    propose_update,
    record_review,
    reject_proposal,
)
from .push_runs import build_push_report
from .queries import (
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
    signal_ref,
)
from .signals import (
    SignalConfig,
    build_attention_queue,
    classify_branches,
    describe_rules,
    find_mismatches,
)
from .storage import Paths, load_registry, read_jsonl
from .validation import ERROR, validate


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0

    paths = Paths.resolve(args.root)
    now = dt.datetime.now(dt.timezone.utc)

    try:
        return args.handler(args, paths, now)
    except RegistryError as exc:
        print(f"registry error: {exc}", file=sys.stderr)
        return 2
    except ProposalError as exc:
        print(f"proposal error: {exc}", file=sys.stderr)
        return 2
    except GitHubError as exc:
        print(f"github error: {exc}", file=sys.stderr)
        return 3
    except KeyError as exc:
        print(f"not found: {exc}", file=sys.stderr)
        return 2


# -- helpers --------------------------------------------------------------


def emit(data: Any, args: argparse.Namespace) -> bool:
    """Print JSON and return True when --json was requested."""
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2, default=str))
        return True
    return False


def _filters(args: argparse.Namespace) -> ProjectFilter:
    return ProjectFilter(
        lifecycle=[Lifecycle(v) for v in (getattr(args, "lifecycle", None) or [])],
        active=_tristate(getattr(args, "active", None)),
        category=getattr(args, "category", None),
        tag=getattr(args, "tag", None),
        priority=[Priority(v) for v in (getattr(args, "priority", None) or [])],
        effort=[Effort(v) for v in (getattr(args, "effort", None) or [])],
        needs_review=_tristate(getattr(args, "needs_review", None)),
        blocked=_tristate(getattr(args, "blocked", None)),
    )


def _tristate(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    text = str(value).lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def _table(rows: list[list[str]], headers: list[str]) -> str:
    if not rows:
        return ""
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    out.append("  ".join("-" * widths[i] for i in range(len(headers))))
    for row in rows:
        out.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip())
    return "\n".join(out)


def _dash(value: Any) -> str:
    return "—" if value in (None, "") else str(value)


# -- commands -------------------------------------------------------------


def cmd_validate(args, paths, now) -> int:
    registry = load_registry(paths)
    report = validate(registry, today=now.date())
    if emit(report.to_dict(), args):
        return 0 if report.ok else 1

    if not report.findings:
        print(f"OK — {len(registry)} projects, no findings.")
        return 0
    for finding in report.findings:
        print(finding.render())
    print()
    print(f"{len(report.errors)} error(s), {len(report.suggestions)} suggestion(s).")
    return 0 if report.ok else 1


def cmd_validate_contract(args, paths, now) -> int:
    try:
        text = Path(args.path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RegistryError(f"cannot read contract {args.path!r}: {exc}") from exc
    report = validate_contract(
        parse_contract(text), expected_registry_id=args.project_id
    )
    if emit(report.to_dict(), args):
        return 0 if report.ok else 1

    if not report.findings:
        print("OK — contract is valid and runnable.")
        return 0
    for finding in report.findings:
        print(finding.render())
    print()
    print(f"{len(report.errors)} error(s), {len(report.suggestions)} suggestion(s).")
    return 0 if report.ok else 1


def cmd_list(args, paths, now) -> int:
    registry = load_registry(paths)
    projects = list_projects(registry, _filters(args))
    if emit([p.summary() for p in projects], args):
        return 0

    rows = [
        [
            p.id,
            p.lifecycle.value,
            "yes" if p.active else "no",
            p.visibility.value,
            _dash(p.last_reviewed),
            _dash(p.purpose)[:60],
        ]
        for p in projects
    ]
    print(_table(rows, ["ID", "LIFECYCLE", "ACTIVE", "VISIBILITY", "REVIEWED", "PURPOSE"]))
    print(f"\n{len(projects)} project(s).")
    return 0


def cmd_show(args, paths, now) -> int:
    registry = load_registry(paths)
    project = registry.require(args.project_id)
    snapshot = load_snapshot(paths)
    state = snapshot.get(project.repo)

    if emit(
        {
            **project.to_dict(),
            "source": project.source,
            "related": list_related_projects(registry, project.id),
            "github": state.to_dict() if state else None,
        },
        args,
    ):
        return 0

    print(f"{project.name}  ({project.id})")
    print(f"  lifecycle    {project.lifecycle.value}  ·  active: {project.active}")
    print(f"  purpose      {_dash(project.purpose)}")
    print(f"  outcome      {_dash(project.desired_outcome)}")
    print(f"  repo         {_dash(project.repo)}  ·  visibility: {project.visibility.value}")
    print(f"  priority     {_dash(project.priority and project.priority.value)}"
          f"  ·  effort: {_dash(project.effort and project.effort.value)}")
    print(f"  reviewed     {_dash(project.last_reviewed)}")
    if project.blocked_by:
        print(f"  blocked by   {project.blocked_by}")
    if project.next_action:
        print(f"  next action  {project.next_action.description}"
              f"  (reviewed {_dash(project.next_action.reviewed)})")
    else:
        print("  next action  — none —")

    if project.accomplishments:
        print("\n  accomplishments:")
        for line in project.accomplishment_summary(limit=10):
            print(f"    - {line}")

    related = list_related_projects(registry, project.id)
    if related:
        print("\n  relationships:")
        for edge in related:
            tag = " (inferred)" if edge["inferred"] else ""
            print(f"    - {edge['kind']} → {edge['target']}{tag}")

    if state:
        github = (
            f"\n  github: {state.full_name} · "
            f"{'private' if state.private else 'public'} · "
            f"{'archived' if state.archived else 'live'} · "
            f"{len(state.pull_requests)} open PR(s) · "
            f"{len(state.issues)} open issue(s)"
        )
        if state.branches_fetched:
            counts = classify_branches(state, now, SignalConfig())
            github += (
                f" · {counts.total} branches ({counts.stale} stale, "
                f"{counts.open_pr_heads} open-PR heads)"
            )
        if state.branches_error:
            github += f" · (branches: {state.branches_error})"
        print(github)
    return 0


def cmd_search(args, paths, now) -> int:
    registry = load_registry(paths)
    results = search_projects(registry, args.query, limit=args.limit)
    if emit([r.to_dict() for r in results], args):
        return 0
    if not results:
        print("No matches.")
        return 0
    rows = [
        [r.project.id, str(r.score), ", ".join(r.matched_fields), _dash(r.project.purpose)[:50]]
        for r in results
    ]
    print(_table(rows, ["ID", "SCORE", "MATCHED", "PURPOSE"]))
    return 0


def cmd_related(args, paths, now) -> int:
    registry = load_registry(paths)
    edges = list_related_projects(registry, args.project_id)
    if emit(edges, args):
        return 0
    if not edges:
        print("No relationships.")
        return 0
    rows = [
        [
            e["kind"], e["target"], _dash(e.get("target_name")),
            "inferred" if e["inferred"] else "declared",
            "ok" if e["resolved"] else "BROKEN",
        ]
        for e in edges
    ]
    print(_table(rows, ["KIND", "TARGET", "NAME", "ORIGIN", "RESOLVES"]))
    return 0


def cmd_next_actions(args, paths, now) -> int:
    registry = load_registry(paths)
    if args.missing:
        rows = find_missing_next_actions(registry)
        if emit(rows, args):
            return 0
        if not rows:
            print("Every active project has a next action.")
            return 0
        print(_table(
            [[r["project_id"], r["lifecycle"], r["reason"]] for r in rows],
            ["ID", "LIFECYCLE", "REASON"],
        ))
        return 0

    rows = list_next_actions(registry, _filters(args))
    if emit(rows, args):
        return 0
    if not rows:
        print("No next actions recorded.")
        return 0
    print(_table(
        [
            [r["project_id"], _dash(r["priority"]), _dash(r["reviewed"]), r["description"][:70]]
            for r in rows
        ],
        ["ID", "PRIORITY", "REVIEWED", "NEXT ACTION"],
    ))
    return 0


def cmd_work_queue(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    queue = build_work_queue(registry, snapshot, now=now, filters=_filters(args))
    if args.limit:
        queue = queue[: args.limit]
    if emit([i.to_dict() for i in queue], args):
        return 0
    if not queue:
        print("Work queue is empty.")
        return 0
    rows = [
        [
            _dash(i.project_id),
            i.human_priority or "not recorded",
            str(i.github_urgency),
            i.source,
            i.title[:60],
        ]
        for i in queue
    ]
    print(_table(rows, ["PROJECT", "HUMAN PRIORITY", "GH URGENCY", "SOURCE", "ITEM"]))
    print("\nwhy:")
    for item in queue:
        print(f"  {_dash(item.project_id)}: {'; '.join(item.reasons)}")
    return 0


def cmd_review_queue(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    queue = build_review_queue(registry, snapshot, now=now)
    if emit([i.to_dict() for i in queue], args):
        return 0
    if not queue:
        print("Nothing is due for review.")
        return 0
    for item in queue:
        print(f"{item.project_id} (`{item.lifecycle}`)")
        for reason in item.reasons:
            print(f"    - {reason}")
    print(f"\n{len(queue)} project(s) due for review. Reviewing never changes "
          "lifecycle automatically.")
    return 0


def cmd_prs(args, paths, now) -> int:
    snapshot = load_snapshot(paths)
    registry = load_registry(paths)
    draft = True if args.draft else (False if args.ready else None)
    result = list_open_prs(snapshot, registry, draft=draft, repo=args.repo, now=now)
    if emit(result, args):
        return 0

    stale = " (STALE)" if result["stale"] else ""
    print(f"GitHub evidence fetched {result['age']}{stale}\n")
    rows = result["pull_requests"]
    if not rows:
        print("No open pull requests in the last snapshot.")
        return 0
    print(_table(
        [
            [
                f"{r['repo']}#{r['number']}", "draft" if r["draft"] else "ready",
                f"{r['age_days']}d", r["review_state"], r["ci_state"], r["title"][:45],
            ]
            for r in rows
        ],
        ["PR", "STATE", "AGE", "REVIEW", "CI", "TITLE"],
    ))
    return 0


def cmd_issues(args, paths, now) -> int:
    snapshot = load_snapshot(paths)
    registry = load_registry(paths)
    result = list_selected_issues(snapshot, registry, labels=args.label or None, now=now)
    if emit(result, args):
        return 0
    stale = " (STALE)" if result["stale"] else ""
    print(f"GitHub evidence fetched {result['age']}{stale}\n")
    if not result["issues"]:
        print("No selected issues in the last snapshot.")
        return 0
    print(_table(
        [
            [
                f"{r['repo']}#{r['number']}", ", ".join(r["matched_labels"]),
                f"{r['age_days']}d", r["title"][:55],
            ]
            for r in result["issues"]
        ],
        ["ISSUE", "LABELS", "AGE", "TITLE"],
    ))
    return 0


def cmd_attention(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    items = build_attention_queue(registry, snapshot, now=now, repo=args.repo)
    if emit([i.to_dict() for i in items], args):
        return 0
    if not items:
        print("No attention signals in the last snapshot.")
        return 0
    for item in items:
        print(f"[{item.severity}] {signal_ref(item.repo, item.number)} — {item.reason}")
        print(f"    rule: {item.rule_id}")
        print(f"    url:  {item.url}")
    print(f"\n{len(items)} signal(s).")
    return 0


def cmd_mismatches(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    found = find_mismatches(registry, snapshot, now=now)
    if emit([m.to_dict() for m in found], args):
        return 0 if not any(m.severity == ERROR for m in found) else 1
    if not found:
        print("Registry intent and GitHub state agree.")
        return 0
    for mismatch in found:
        print(f"[{mismatch.severity}] {mismatch.project_id} — {mismatch.message}")
        print(f"    rule: {mismatch.rule_id}")
    errors = sum(1 for m in found if m.severity == ERROR)
    print(f"\n{errors} error(s), {len(found) - errors} suggestion(s). "
          "Nothing was changed automatically.")
    return 1 if errors else 0


def cmd_rules(args, paths, now) -> int:
    catalogue = describe_rules()
    if emit(catalogue, args):
        return 0
    for group, rules in catalogue.items():
        print(f"{group}:")
        for rule in rules:
            print(f"  {rule['id']:38} {rule['severity']:10} {rule['description']}")
        print()
    return 0


def cmd_dashboard(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    text = render_dashboard(registry, snapshot, now=now)
    target = args.output or str(paths.dashboard_file)
    if target == "-":
        print(text)
        return 0
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text)
    print(f"Wrote {target}")
    return 0


def cmd_portfolio(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    document = export_portfolio(registry, snapshot, now=now)

    if args.format == "json":
        text = json.dumps(document, indent=2)
        default_target = str(paths.portfolio_file)
    else:
        text = render_portfolio_markdown(document)
        default_target = "-"

    target = args.output or default_target
    if target == "-":
        print(text)
    else:
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
        print(f"Wrote {target} ({document['project_count']} public project(s))")
    return 0


def cmd_sync(args, paths, now) -> int:
    registry = load_registry(paths)
    client = GitHubClient()
    result = sync(
        registry,
        client,
        paths=paths,
        repos=args.repo or None,
        with_details=not args.no_details,
        with_branches=not args.no_branches,
    )
    if emit(result.to_dict(), args):
        return 0 if result.coverage["complete"] else 1

    coverage = result.coverage
    print(f"Refreshed {coverage['repos_succeeded']}/{coverage['repos_requested']} repositories.")
    for error in result.errors:
        print(f"  failed: {error['repo']} — {error['error']}")
    for error in result.partial_errors:
        print(f"  partial: {error['repo']} — {error['error']}")
    if result.errors:
        print("\nPrevious data for failed repositories was kept and marked stale.")
    return 0 if coverage["complete"] else 1


def cmd_sync_status(args, paths, now) -> int:
    snapshot = load_snapshot(paths)
    registry = load_registry(paths)
    status = build_sync_status(registry, snapshot, paths=paths, now=now)
    if emit(status, args):
        return 0
    print(f"last refresh   {_dash(status['completed_at'])} ({status['age']})")
    print(f"stale          {status['stale']}")
    print(f"repositories   {status['repo_count']}")
    if status["coverage"]:
        print(f"coverage       {json.dumps(status['coverage'])}")
    briefs = status["briefs"]
    print(
        f"briefs         {briefs['present']} present / {briefs['missing']} missing / "
        f"{briefs['stale']} stale"
    )
    for error in status["errors"]:
        print(f"  error: {error.get('repo')} — {error.get('error')}")
    return 0


def cmd_briefs_status(args, paths, now) -> int:
    registry = load_registry(paths)
    snapshot = load_snapshot(paths)
    report = build_briefs_status(registry, snapshot, paths=paths, now=now)
    if emit(report, args):
        return 0
    for item in report["projects"]:
        presence = "present" if item["present"] else "missing"
        age = "unknown age" if item["age_days"] is None else f"{item['age_days']}d"
        print(
            f"{item['project_id']:<32} {presence:<7} "
            f"{item['revision_state']:<7} {age}"
        )
        if item["error"]:
            print(f"  error: {item['error']}")
    summary = report["summary"]
    print(
        f"\n{summary['present']} present / {summary['missing']} missing / "
        f"{summary['stale']} stale ({summary['unknown']} revision unknown, "
        f"{summary['malformed']} malformed)."
    )
    return 0


def cmd_push_report(args, paths, now) -> int:
    try:
        report = build_push_report(
            paths=paths, since=args.since, refresh=args.refresh, now=now
        )
    except ValueError as exc:
        raise RegistryError(str(exc)) from None
    if emit(report, args):
        return 0

    runs = report["runs"]
    prs = report["pull_requests"]
    rate = prs["merge_rate"]
    print(f"push runs       {runs['total']}")
    print(f"by host         {json.dumps(runs['by_host'])}")
    print(f"by outcome      {json.dumps(runs['by_outcome'])}")
    print(f"by project      {json.dumps(runs['by_project'])}")
    print(f"by gear         {json.dumps(runs['by_gear'])}")
    print(f"PR states       {json.dumps(prs['by_state'])}")
    percent = "unknown" if rate["percent"] is None else f"{rate['percent']:.1f}%"
    print(f"merge rate      {rate['merged']}/{rate['total']} ({percent})")
    malformed = report["journal"]["malformed_count"]
    if malformed:
        print(f"malformed       {malformed} journal line(s)")
        for item in report["journal"]["malformed"]:
            print(f"  line {item['line']}: {item['error']}")
    for error in prs["refresh_errors"]:
        print(f"  refresh error: {error['url']} — {error['error']}")
    return 0


def cmd_import_github(args, paths, now) -> int:
    registry = load_registry(paths)
    client = GitHubClient()
    repos = fetch_inventory(client, args.owner)
    result = import_inventory(
        registry, repos, paths=paths,
        include_forks=args.include_forks,
        include_archived=not args.skip_archived,
        write=not args.dry_run,
    )
    if emit(result.to_dict(), args):
        return 0
    print(f"Created {len(result.created)} project stub(s)"
          f"{' (dry run)' if args.dry_run else ''}:")
    for entry in result.created:
        print(f"  + {entry}")
    if result.skipped_existing:
        print(f"Skipped {len(result.skipped_existing)} already-registered repositories.")
    if result.skipped_forks:
        print(f"Skipped {len(result.skipped_forks)} fork(s); pass --include-forks to add them.")
    print("\nEach stub is marked needs_review with no purpose. Fill those in next.")
    return 0


def cmd_propose(args, paths, now) -> int:
    registry = load_registry(paths)
    changes = dict(_parse_sets(args.set))
    proposal = propose_update(
        registry, args.project_id, changes, rationale=args.rationale, paths=paths, now=now
    )
    if emit(proposal.to_dict(), args):
        return 0
    print(proposal.render_diff())
    print(f"\nProposed only — `{args.project_id}` is unchanged on disk.")
    print(f"Apply with: registry proposal-apply {proposal.id} --approve")
    return 0


def cmd_proposals(args, paths, now) -> int:
    found = list_proposals(paths, status=args.status)
    if emit([p.to_dict() for p in found], args):
        return 0
    if not found:
        print("No proposals.")
        return 0
    print(_table(
        [
            [p.id, p.project_id, p.status, ", ".join(p.changes)[:50]]
            for p in found
        ],
        ["ID", "PROJECT", "STATUS", "FIELDS"],
    ))
    return 0


def cmd_proposal_show(args, paths, now) -> int:
    proposal = load_proposal(args.proposal_id, paths)
    if emit(proposal.to_dict(), args):
        return 0
    print(proposal.render_diff())
    return 0


def cmd_proposal_apply(args, paths, now) -> int:
    registry = load_registry(paths)
    result = apply_proposal(
        registry, args.proposal_id, approved=args.approve, paths=paths, now=now
    )
    if emit(result.to_dict(), args):
        return 0
    print(f"Applied {result.proposal.id} → {result.written_path}")
    print(result.proposal.render_diff())
    return 0


def cmd_proposal_reject(args, paths, now) -> int:
    proposal = reject_proposal(args.proposal_id, paths=paths, now=now)
    if emit(proposal.to_dict(), args):
        return 0
    print(f"Rejected {proposal.id}.")
    return 0


def cmd_record_review(args, paths, now) -> int:
    registry = load_registry(paths)
    reviewed_on = dt.date.fromisoformat(args.date) if args.date else now.date()
    result = record_review(
        registry, args.project_id,
        reviewed_on=reviewed_on,
        updates=dict(_parse_sets(args.set)),
        rationale=args.rationale,
        paths=paths, now=now,
    )
    if emit(result.to_dict(), args):
        return 0
    print(f"Recorded review of {args.project_id} on {reviewed_on.isoformat()}.")
    print(result.proposal.render_diff())
    return 0


def cmd_audit(args, paths, now) -> int:
    records = read_jsonl(paths.audit_log)
    if emit(records, args):
        return 0
    if not records:
        print("Audit log is empty.")
        return 0
    for record in records[-args.limit:]:
        print(f"{record['ts']}  {record['action']:16} {record.get('project_id', '')}"
              f"  ({record.get('proposal_id', '')})")
    return 0


def cmd_mcp(args, paths, now) -> int:
    from .mcp.server import serve

    serve(paths)
    return 0


def _parse_sets(values: Sequence[str] | None) -> list[tuple[str, str]]:
    pairs = []
    for item in values or []:
        if "=" not in item:
            raise ProposalError(f"--set expects path=value, got {item!r}")
        path, value = item.split("=", 1)
        pairs.append((path.strip(), value))
    return pairs


# -- parser ---------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="registry",
        description="Control plane for the project portfolio.",
    )
    parser.add_argument("--root", help="Registry root (defaults to the repository root).")
    subparsers = parser.add_subparsers(dest="command")

    def add(name: str, handler, help_text: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=help_text)
        sub.add_argument("--json", action="store_true", help="Emit JSON.")
        sub.set_defaults(handler=handler)
        return sub

    def add_project_filters(sub: argparse.ArgumentParser) -> None:
        sub.add_argument("--lifecycle", action="append", choices=[l.value for l in Lifecycle])
        sub.add_argument("--active", choices=["true", "false"])
        sub.add_argument("--category")
        sub.add_argument("--tag")
        sub.add_argument("--priority", action="append", choices=[p.value for p in Priority])
        sub.add_argument("--effort", action="append", choices=[e.value for e in Effort])
        sub.add_argument("--needs-review", dest="needs_review", choices=["true", "false"])
        sub.add_argument("--blocked", choices=["true", "false"])

    add("validate", cmd_validate, "Check the registry against the operating rules.")

    sub = add("validate-contract", cmd_validate_contract, "Validate a target-repository contract.")
    sub.add_argument("path")
    sub.add_argument("--project-id", help="Expected registry project id.")

    sub = add("list", cmd_list, "List projects.")
    add_project_filters(sub)

    sub = add("show", cmd_show, "Show one project in full.")
    sub.add_argument("project_id")

    sub = add("search", cmd_search, "Search curated project text.")
    sub.add_argument("query")
    sub.add_argument("--limit", type=int)

    sub = add("related", cmd_related, "Show a project's relationships.")
    sub.add_argument("project_id")

    sub = add("next-actions", cmd_next_actions, "List next actions, or find missing ones.")
    sub.add_argument("--missing", action="store_true")
    add_project_filters(sub)

    sub = add("work-queue", cmd_work_queue, "Ranked work across projects.")
    sub.add_argument("--limit", type=int)
    add_project_filters(sub)

    add("review-queue", cmd_review_queue, "Projects due for a deliberate review.")

    sub = add("prs", cmd_prs, "Open pull requests across the portfolio.")
    sub.add_argument("--draft", action="store_true", help="Only drafts.")
    sub.add_argument("--ready", action="store_true", help="Only ready-for-review PRs.")
    sub.add_argument("--repo")

    sub = add("issues", cmd_issues, "Open issues carrying watched labels.")
    sub.add_argument("--label", action="append")

    sub = add("attention", cmd_attention, "GitHub work needing attention.")
    sub.add_argument("--repo")

    add("mismatches", cmd_mismatches, "Conflicts between registry intent and GitHub.")
    add("rules", cmd_rules, "Describe the signal and mismatch rules.")

    sub = add("dashboard", cmd_dashboard, "Generate the portfolio dashboard.")
    sub.add_argument("-o", "--output", help="Output path, or - for stdout.")

    sub = add("portfolio", cmd_portfolio, "Export the public-safe portfolio.")
    sub.add_argument("-o", "--output", help="Output path, or - for stdout.")
    sub.add_argument("--format", choices=["json", "markdown"], default="markdown")

    sub = add("sync", cmd_sync, "Refresh observed GitHub state (read-only).")
    sub.add_argument("--repo", action="append", help="Limit to these repositories.")
    sub.add_argument("--no-details", action="store_true",
                     help="Skip per-PR review and CI lookups.")
    sub.add_argument("--no-branches", action="store_true",
                     help="Skip branch capture and carry forward prior branch evidence.")

    add("sync-status", cmd_sync_status, "Report the last GitHub refresh.")
    add("briefs-status", cmd_briefs_status, "Report evidence-brief coverage and staleness.")

    sub = add("push-report", cmd_push_report, "Summarize push runs and cached PR states.")
    sub.add_argument("--refresh", action="store_true",
                     help="Refresh linked PR states through read-only GitHub GETs.")
    sub.add_argument("--since", metavar="YYYY-MM-DD",
                     help="Include runs on or after this UTC date.")

    sub = add("import-github", cmd_import_github, "Create stubs from an owner's repositories.")
    sub.add_argument("--owner", required=True)
    sub.add_argument("--include-forks", action="store_true")
    sub.add_argument("--skip-archived", action="store_true")
    sub.add_argument("--dry-run", action="store_true")

    sub = add("propose", cmd_propose, "Propose a curated change (does not apply it).")
    sub.add_argument("project_id")
    sub.add_argument("--set", action="append", required=True, metavar="PATH=VALUE")
    sub.add_argument("--rationale")

    sub = add("proposals", cmd_proposals, "List proposals.")
    sub.add_argument("--status", choices=["pending", "applied", "rejected"])

    sub = add("proposal-show", cmd_proposal_show, "Show a proposal's before/after diff.")
    sub.add_argument("proposal_id")

    sub = add("proposal-apply", cmd_proposal_apply, "Apply an approved proposal.")
    sub.add_argument("proposal_id")
    sub.add_argument("--approve", action="store_true",
                     help="Required. Without it, the apply is refused.")

    sub = add("proposal-reject", cmd_proposal_reject, "Reject a pending proposal.")
    sub.add_argument("proposal_id")

    sub = add("record-review", cmd_record_review, "Record that a project was reviewed.")
    sub.add_argument("project_id")
    sub.add_argument("--date", help="Review date (defaults to today).")
    sub.add_argument("--set", action="append", metavar="PATH=VALUE", default=[])
    sub.add_argument("--rationale")

    sub = add("audit", cmd_audit, "Show the audit log.")
    sub.add_argument("--limit", type=int, default=20)

    add("mcp", cmd_mcp, "Run the MCP server on stdio.")

    return parser


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
