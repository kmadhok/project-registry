from __future__ import annotations

import argparse
import copy
import sys
from pathlib import Path

from project_registry.dashboard import build_dashboard
from project_registry.github import GitHubCommandError, collect_github_snapshot
from project_registry.io import load_json, utc_now, write_json
from project_registry.model import LIFECYCLES, REVIEW_STATUSES, repository_slug
from project_registry.registry import merge_registry
from project_registry.understanding import scaffold_understanding
from project_registry.validation import validate_files, validate_registry


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


def command_sync(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    snapshot_path = root / "data" / "github_snapshot.json"
    projects_path = root / "data" / "projects.json"
    try:
        snapshot = collect_github_snapshot(
            args.owner,
            gh_bin=args.gh_bin,
            enrich_pull_requests=not args.no_pr_enrichment,
        )
    except GitHubCommandError as error:
        print(f"GitHub sync failed: {error}", file=sys.stderr)
        return 1
    registry = merge_registry(args.owner, snapshot, projects_path)
    write_json(snapshot_path, snapshot)
    write_json(projects_path, registry)
    print(
        f"Synced {len(snapshot['repositories'])} repositories, "
        f"{len(snapshot['open_pull_requests'])} PRs, and {len(snapshot['open_issues'])} issues."
    )
    if snapshot["errors"]:
        print(f"Completed with {len(snapshot['errors'])} enrichment warning(s).")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    errors = validate_files(args.root.resolve())
    if errors:
        print(f"Validation failed with {len(errors)} error(s):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("Registry validation passed.")
    return 0


def command_dashboard(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    registry = load_json(root / "data" / "projects.json")
    snapshot = load_json(root / "data" / "github_snapshot.json")
    output = build_dashboard(registry, snapshot)
    path = root / "DASHBOARD.md"
    if getattr(args, "check", False):
        if not path.exists() or path.read_text(encoding="utf-8") != output:
            print("DASHBOARD.md is stale; regenerate it.", file=sys.stderr)
            return 1
        print("DASHBOARD.md is current.")
        return 0
    path.write_text(output, encoding="utf-8", newline="\n")
    print(f"Wrote {path}.")
    return 0


def command_refresh(args: argparse.Namespace) -> int:
    if command_sync(args) != 0:
        return 1
    if command_validate(args) != 0:
        return 1
    return command_dashboard(args)


def command_scaffold(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    repo_path = args.repo_path.resolve()
    if not (repo_path / ".git").exists():
        print(f"Not a Git repository: {repo_path}", file=sys.stderr)
        return 1
    brief = scaffold_understanding(repo_path, args.repository)
    output = root / "data" / "understanding" / f"{repository_slug(args.repository)}.json"
    if output.exists() and not args.force:
        print(f"Refusing to overwrite existing brief: {output}", file=sys.stderr)
        return 1
    write_json(output, brief)
    print(f"Wrote understanding scaffold to {output}.")
    return 0


def command_set_project(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    projects_path = root / "data" / "projects.json"
    snapshot_path = root / "data" / "github_snapshot.json"
    registry = copy.deepcopy(load_json(projects_path))
    snapshot = load_json(snapshot_path) if snapshot_path.exists() else None
    project = next(
        (
            item
            for item in registry["projects"]
            if item["repository"].lower() == args.repository.lower()
        ),
        None,
    )
    if project is None:
        print(f"Unknown project: {args.repository}", file=sys.stderr)
        return 1

    fields = {
        "review_status": args.review_status,
        "purpose": args.purpose,
        "public_summary": args.public_summary,
        "lifecycle": args.lifecycle,
        "priority": args.priority,
        "desired_outcome": args.desired_outcome,
        "next_action": args.next_action,
        "understanding_path": args.understanding_path,
        "notes": args.notes,
    }
    for field, value in fields.items():
        if value is not None:
            project[field] = value
    if args.active is not None:
        project["active"] = args.active
    if args.review_now:
        project["last_reviewed_at"] = utc_now()
    if args.tag:
        project["tags"] = sorted(set(project.get("tags", [])) | set(args.tag))
    registry["updated_at"] = utc_now()

    errors = validate_registry(registry, snapshot, root)
    if errors:
        print("Project update would make the registry invalid:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    write_json(projects_path, registry)
    print(f"Updated {project['repository']}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Registry management CLI")
    parser.add_argument("--root", type=Path, default=default_root(), help="Registry repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync = subparsers.add_parser("sync", help="Refresh read-only GitHub data and merge new projects")
    sync.add_argument("--owner", default="kmadhok")
    sync.add_argument("--gh-bin", default=None)
    sync.add_argument("--no-pr-enrichment", action="store_true")
    sync.set_defaults(func=command_sync)

    validate = subparsers.add_parser("validate", help="Validate registry, snapshot, and understanding briefs")
    validate.set_defaults(func=command_validate)

    dashboard = subparsers.add_parser("dashboard", help="Generate the private Markdown dashboard")
    dashboard.add_argument("--check", action="store_true")
    dashboard.set_defaults(func=command_dashboard)

    refresh = subparsers.add_parser("refresh", help="Sync, validate, and generate the dashboard")
    refresh.add_argument("--owner", default="kmadhok")
    refresh.add_argument("--gh-bin", default=None)
    refresh.add_argument("--no-pr-enrichment", action="store_true")
    refresh.set_defaults(func=command_refresh)

    scaffold = subparsers.add_parser("scaffold-understanding", help="Create a read-only understanding draft")
    scaffold.add_argument("repository", help="GitHub repository as owner/name")
    scaffold.add_argument("repo_path", type=Path, help="Local checkout path")
    scaffold.add_argument("--force", action="store_true")
    scaffold.set_defaults(func=command_scaffold)

    set_project = subparsers.add_parser(
        "set-project", help="Update human-curated fields with full-registry validation"
    )
    set_project.add_argument("repository", help="GitHub repository as owner/name")
    set_project.add_argument("--review-status", choices=sorted(REVIEW_STATUSES))
    set_project.add_argument("--purpose")
    set_project.add_argument("--public-summary")
    set_project.add_argument("--lifecycle", choices=sorted(LIFECYCLES))
    active = set_project.add_mutually_exclusive_group()
    active.add_argument("--active", dest="active", action="store_true")
    active.add_argument("--inactive", dest="active", action="store_false")
    set_project.set_defaults(active=None)
    set_project.add_argument("--priority", type=int)
    set_project.add_argument("--desired-outcome")
    set_project.add_argument("--next-action")
    set_project.add_argument("--understanding-path")
    set_project.add_argument("--notes")
    set_project.add_argument("--tag", action="append")
    set_project.add_argument("--review-now", action="store_true")
    set_project.set_defaults(func=command_set_project)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
