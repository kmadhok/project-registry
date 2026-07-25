"""Seed the registry from an owner's GitHub repositories (US-001).

An import creates a *stub* per repository: enough identity to exist in the
registry, `needs_review: true` because nobody has written its purpose yet, and
nothing else invented. Existing curated files are never touched -- the importer
skips any repository that already has a project, so re-running it is safe and
only ever adds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..model import Lifecycle, Project, Visibility
from ..storage import Paths, Registry, project_path, save_project
from .client import GitHubClient


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    skipped_forks: list[str] = field(default_factory=list)
    skipped_archived: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "skipped_existing": self.skipped_existing,
            "skipped_forks": self.skipped_forks,
            "skipped_archived": self.skipped_archived,
            "created_count": len(self.created),
        }


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def unique_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise ValueError(f"could not derive a unique id from {base!r}")


def stub_from_repo(repo: dict, project_id: str) -> Project:
    """Build the placeholder project for a repository.

    Deliberately conservative: lifecycle is `incubating` and `active` is False
    because GitHub activity is evidence, not priority (operating rule 6). The
    human decides what this really is during review.
    """
    return Project(
        id=project_id,
        name=repo.get("name") or project_id,
        purpose=None,
        lifecycle=Lifecycle.INCUBATING,
        active=False,
        needs_review=True,
        repo=repo.get("full_name"),
        is_fork=bool(repo.get("fork", False)),
        visibility=Visibility.PRIVATE if repo.get("private", True) else Visibility.PUBLIC,
        descriptions=_descriptions_from_repo(repo),
    )


def _descriptions_from_repo(repo: dict):
    from ..model import Descriptions

    description = (repo.get("description") or "").strip() or None
    # A GitHub description is not automatically safe to publish, so it is
    # recorded privately and left for a human to promote (US-002, US-009).
    return Descriptions(private=description, public_safe=None)


def import_inventory(
    registry: Registry,
    repos: list[dict],
    paths: Paths | None = None,
    include_forks: bool = False,
    include_archived: bool = True,
    write: bool = True,
) -> ImportResult:
    """Create stub projects for repositories not already registered."""
    paths = paths or Paths.resolve()
    result = ImportResult()
    taken = set(registry.projects)

    for repo in sorted(repos, key=lambda r: r.get("full_name", "")):
        full_name = repo.get("full_name")
        if not full_name:
            continue
        if registry.by_repo(full_name) is not None:
            result.skipped_existing.append(full_name)
            continue
        if repo.get("fork") and not include_forks:
            result.skipped_forks.append(full_name)
            continue
        if repo.get("archived") and not include_archived:
            result.skipped_archived.append(full_name)
            continue

        project_id = unique_id(slugify(repo.get("name") or full_name.split("/")[-1]), taken)
        taken.add(project_id)
        project = stub_from_repo(repo, project_id)

        if write:
            target = project_path(paths, project_id)
            if target.exists():
                # Never overwrite a curated file, even if it is not loadable.
                result.skipped_existing.append(full_name)
                continue
            save_project(project, paths)

        registry.projects[project_id] = project
        result.created.append(f"{project_id} ({full_name})")

    return result


def fetch_inventory(client: GitHubClient, owner: str) -> list[dict]:
    return client.list_owner_repos(owner)
