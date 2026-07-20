from __future__ import annotations

from pathlib import Path
from typing import Any

from project_registry.io import load_json, utc_now
from project_registry.model import SCHEMA_VERSION, new_project


def merge_registry(
    owner: str,
    snapshot: dict[str, Any],
    existing_path: Path | None = None,
) -> dict[str, Any]:
    existing_projects: list[dict[str, Any]] = []
    if existing_path and existing_path.exists():
        existing = load_json(existing_path)
        existing_projects = existing.get("projects", [])

    by_repository = {
        project["repository"].lower(): project
        for project in existing_projects
        if isinstance(project, dict) and isinstance(project.get("repository"), str)
    }
    merged: list[dict[str, Any]] = []
    observed_repositories: set[str] = set()

    for repository in snapshot["repositories"]:
        key = repository["name_with_owner"].lower()
        observed_repositories.add(key)
        if key in by_repository:
            project = by_repository[key]
            project["name"] = repository["name"]
            project["repository"] = repository["name_with_owner"]
        else:
            project = new_project(repository)
        merged.append(project)

    for project in existing_projects:
        repository = str(project.get("repository") or "").lower()
        if repository and repository not in observed_repositories:
            merged.append(project)

    merged.sort(key=lambda project: project["repository"].lower())
    return {
        "schema_version": SCHEMA_VERSION,
        "owner": owner,
        "updated_at": utc_now(),
        "projects": merged,
    }
