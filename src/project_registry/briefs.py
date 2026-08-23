"""Visibility into out-of-band project evidence briefs."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from .github.snapshot import Snapshot, age_days, iso, parse_ts
from .storage import Paths, Registry


def build_briefs_status(
    registry: Registry,
    snapshot: Snapshot,
    *,
    paths: Paths | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Report brief presence, age, and revision state for repo-backed projects."""
    paths = paths or registry.paths or Paths.resolve()
    now = now or dt.datetime.now(dt.timezone.utc)
    projects = []
    for project in registry:
        if not project.repo:
            continue
        path = paths.understanding_dir / f"{project.id}.json"
        projects.append(_brief_status(project.id, project.name, project.repo, path, snapshot, now))

    present = [item for item in projects if item["present"]]
    return {
        "summary": {
            "total": len(projects),
            "present": len(present),
            "missing": len(projects) - len(present),
            "current": sum(item["revision_state"] == "current" for item in present),
            "stale": sum(item["revision_state"] == "stale" for item in present),
            "unknown": sum(item["revision_state"] == "unknown" for item in present),
            "malformed": sum(not item["usable"] for item in present),
        },
        "projects": projects,
    }


def build_sync_status(
    registry: Registry,
    snapshot: Snapshot,
    *,
    paths: Paths | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """GitHub sync status plus brief coverage from the same shared query path."""
    now = now or dt.datetime.now(dt.timezone.utc)
    status = snapshot.status(now)
    status["briefs"] = build_briefs_status(
        registry, snapshot, paths=paths, now=now
    )["summary"]
    return status


def _brief_status(
    project_id: str,
    project_name: str,
    repo: str,
    path: Path,
    snapshot: Snapshot,
    now: dt.datetime,
) -> dict[str, Any]:
    base = {
        "project_id": project_id,
        "project_name": project_name,
        "repo": repo,
        "path": str(path),
        "present": path.is_file(),
        "usable": False,
        "analyzed_at": None,
        "age_days": None,
        "revision": None,
        "current_revision": _default_head(snapshot, repo),
        "revision_state": "unknown",
        "error": None,
    }
    if not base["present"]:
        return base

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {**base, "error": "malformed JSON"}
    if not isinstance(raw, dict):
        return {**base, "error": "expected a JSON object"}

    analyzed = parse_ts(raw.get("analyzed_at"))
    revision = raw.get("revision") if isinstance(raw.get("revision"), str) else None
    current = base["current_revision"]
    if revision and current:
        revision_state = "current" if revision == current else "stale"
    else:
        revision_state = "unknown"
    error = None
    if raw.get("analyzed_at") is not None and analyzed is None:
        error = "invalid analyzed_at"
    return {
        **base,
        "usable": True,
        "analyzed_at": iso(analyzed),
        "age_days": age_days(analyzed, now),
        "revision": revision,
        "revision_state": revision_state,
        "error": error,
    }


def _default_head(snapshot: Snapshot, repo: str) -> str | None:
    state = snapshot.get(repo)
    if state is None or not state.branches_fetched:
        return None
    for branch in state.branches:
        if branch.is_default and branch.head_sha:
            return branch.head_sha
    for branch in state.branches:
        if branch.name == state.default_branch and branch.head_sha:
            return branch.head_sha
    return None
