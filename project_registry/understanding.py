from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from project_registry.io import utc_now
from project_registry.model import SCHEMA_VERSION


DOCUMENT_NAMES = {
    "README.md", "README.rst", "README.txt", "ARCHITECTURE.md", "PLAN.md",
    "ROADMAP.md", "SPEC.md", "PRD.md", "SETUP.md",
}
MANIFEST_NAMES = {
    "pyproject.toml", "requirements.txt", "package.json", "Cargo.toml", "go.mod",
    "pom.xml", "build.gradle", "docker-compose.yml", "Dockerfile",
}


def _git(repo_path: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _relative_paths(repo_path: Path, predicate: Any, limit: int = 100) -> list[str]:
    paths: list[str] = []
    excluded_parts = {
        ".git", ".mypy_cache", ".pytest_cache", ".tox", ".venv", "__pycache__",
        "build", "dist", "env", "node_modules", "site-packages", "venv",
    }
    for path in sorted(repo_path.rglob("*")):
        relative = path.relative_to(repo_path)
        if excluded_parts.intersection(relative.parts) or not path.is_file():
            continue
        if predicate(relative):
            paths.append(relative.as_posix())
            if len(paths) >= limit:
                break
    return paths


def scaffold_understanding(repo_path: Path, repository: str) -> dict[str, Any]:
    revision = _git(repo_path, "rev-parse", "HEAD") or "unknown-revision"
    documents = _relative_paths(
        repo_path,
        lambda path: path.name in DOCUMENT_NAMES or path.suffix.lower() == ".md",
        40,
    )
    manifests = _relative_paths(repo_path, lambda path: path.name in MANIFEST_NAMES, 30)
    tests = _relative_paths(
        repo_path,
        lambda path: "test" in path.name.lower() or "tests" in path.parts,
        40,
    )
    workflows = _relative_paths(repo_path, lambda path: ".github/workflows" in path.as_posix(), 30)
    inspected = sorted(set(documents + manifests + tests + workflows))
    evidence = [
        {
            "id": "revision",
            "kind": "git_revision",
            "location": revision,
            "summary": "Commit analyzed by this draft brief.",
        }
    ]
    for index, path in enumerate(inspected, start=1):
        evidence.append(
            {
                "id": f"file-{index}",
                "kind": "repository_file",
                "location": path,
                "summary": "Discovered during the evidence inventory; content review is still required.",
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "repository": repository,
        "analyzed_at": utc_now(),
        "revision": revision,
        "analysis_scope": {
            "inspected": inspected,
            "missing": [],
            "inaccessible": [],
            "sampling_notes": "Automated scaffold only; every material claim requires human or agent evidence review.",
        },
        "stated_purpose": None,
        "observed_capabilities": [],
        "planned_work": [],
        "architecture": [],
        "maturity": "concept",
        "accomplishments": [],
        "gaps": [],
        "risks": [],
        "suggested_next_actions": [],
        "questions": ["What outcome should this project ultimately produce?"],
        "evidence": evidence,
        "confidence": {
            "level": "low",
            "rationale": "This is an inventory scaffold and has not received a substantive evidence review.",
        },
        "limitations": ["Repository files were inventoried but their behavior was not yet verified."],
    }
