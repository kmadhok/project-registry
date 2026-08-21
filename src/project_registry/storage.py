"""Loading and saving registry data.

Two directories, two owners:

* ``registry/`` holds curated YAML written by a human (or, on apply, by the
  proposal workflow after explicit approval).
* ``data/`` holds machine-written JSON: the GitHub snapshot, pending proposals,
  and the audit log.

Nothing in the sync path may write under ``registry/``. That is the mechanical
form of operating rule 7.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from .model import Project, RegistryError, sort_projects


@dataclass(frozen=True)
class Paths:
    """Resolved locations for every file the tool reads or writes."""

    root: Path

    @classmethod
    def resolve(cls, root: str | os.PathLike[str] | None = None) -> "Paths":
        if root is not None:
            return cls(Path(root).resolve())
        env = os.environ.get("PROJECT_REGISTRY_ROOT")
        if env:
            return cls(Path(env).resolve())
        return cls(_find_repo_root(Path.cwd()))

    @property
    def projects_dir(self) -> Path:
        return self.root / "registry" / "projects"

    @property
    def schema_file(self) -> Path:
        return self.root / "registry" / "schema" / "project.schema.json"

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def snapshot_file(self) -> Path:
        return self.data_dir / "github" / "snapshot.json"

    @property
    def push_prs_file(self) -> Path:
        return self.data_dir / "github" / "push_prs.json"

    @property
    def push_runs_file(self) -> Path:
        return self.data_dir / "push_runs.jsonl"

    @property
    def proposals_dir(self) -> Path:
        return self.data_dir / "proposals"

    @property
    def audit_log(self) -> Path:
        return self.data_dir / "audit_log.jsonl"

    @property
    def dashboard_file(self) -> Path:
        return self.root / "DASHBOARD.md"

    @property
    def portfolio_file(self) -> Path:
        return self.data_dir / "portfolio.json"


def _find_repo_root(start: Path) -> Path:
    """Walk upward looking for a registry directory, else a .git directory."""
    for candidate in [start, *start.parents]:
        if (candidate / "registry" / "projects").is_dir():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return start


@dataclass
class Registry:
    """An in-memory view of all curated projects."""

    projects: dict[str, Project] = field(default_factory=dict)
    loaded_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
    paths: Paths | None = None

    def __iter__(self) -> Iterator[Project]:
        return iter(self.sorted())

    def __len__(self) -> int:
        return len(self.projects)

    def __contains__(self, project_id: object) -> bool:
        return project_id in self.projects

    def get(self, project_id: str) -> Project | None:
        return self.projects.get(project_id)

    def require(self, project_id: str) -> Project:
        project = self.projects.get(project_id)
        if project is None:
            raise KeyError(f"unknown project id: {project_id}")
        return project

    def sorted(self) -> list[Project]:
        return sort_projects(self.projects.values())

    def by_repo(self, repo: str) -> Project | None:
        for project in self.projects.values():
            if project.repo and project.repo.lower() == repo.lower():
                return project
        return None

    @property
    def loaded_at_iso(self) -> str:
        return self.loaded_at.isoformat()


def load_registry(paths: Paths | None = None) -> Registry:
    """Read every ``registry/projects/*.yaml`` file into a Registry."""
    paths = paths or Paths.resolve()
    projects: dict[str, Project] = {}

    if paths.projects_dir.is_dir():
        for path in sorted(paths.projects_dir.glob("*.y*ml")):
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
            if raw is None:
                continue
            project = Project.parse(raw, source_path=str(path))
            if project.id in projects:
                other = projects[project.id].source_path
                raise RegistryError(
                    f"duplicate project id {project.id!r} in {path} and {other}"
                )
            projects[project.id] = project

    return Registry(projects=projects, paths=paths)


def project_path(paths: Paths, project_id: str) -> Path:
    return paths.projects_dir / f"{project_id}.yaml"


def save_project(project: Project, paths: Paths | None = None) -> Path:
    """Write one curated project file.

    The file is rewritten in canonical field order, so YAML comments in an
    existing file are not preserved. Hand-edit for prose; use this path for
    approved, machine-applied updates.
    """
    paths = paths or Paths.resolve()
    paths.projects_dir.mkdir(parents=True, exist_ok=True)
    path = Path(project.source_path) if project.source_path else project_path(paths, project.id)
    path.write_text(dump_yaml(project.to_dict()), encoding="utf-8")
    return path


def dump_yaml(data: dict[str, Any]) -> str:
    return yaml.safe_dump(
        data,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=88,
    )


# -- machine-owned data ---------------------------------------------------


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return default
    return json.loads(text)


def write_json(path: Path, data: Any) -> Path:
    """Write JSON atomically so a crashed sync cannot truncate good data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def append_jsonl(path: Path, record: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=False) + "\n")
    return path


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))
    return records
