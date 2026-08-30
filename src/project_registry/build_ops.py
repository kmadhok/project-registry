"""Safe Git and GitHub operations for an active autonomous build run."""

from __future__ import annotations

import fnmatch
import re
import subprocess
from pathlib import Path
from typing import Any

from .build_runs import BuildError, _read_lease, record_event
from .storage import Paths


def slugify(title: str) -> str:
    """Return the bounded, branch-safe slug used for build chunks."""
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug[:40].strip("-") or "chunk"


def branch_name(run_id: str, chunk_id: str, title: str) -> str:
    """Derive a chunk branch name from its lease namespace."""
    return f"push/{run_id}-{chunk_id}-{slugify(title)}"


def push_args(branch: str) -> list[str]:
    """Return the only supported push argument shape."""
    return ["push", "-u", "origin", branch]


def _active_lease(paths: Paths, run_id: str) -> dict[str, Any]:
    lease = _read_lease(paths)
    if lease is None or lease.get("run_id") != run_id:
        raise BuildError("build lease does not match run_id")
    status = lease.get("status")
    if status in {"finalize_pending", "reconciling"}:
        raise BuildError(f"run is {status}; build operations are not allowed")
    return lease


def _git(workdir: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(workdir), *args],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BuildError(f"git {args[0]} failed: {completed.stderr.strip()}")
    return completed.stdout


def _resolve(path: str | Path, base: Path) -> Path:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = base / candidate
    return candidate.resolve(strict=False)


def _matches_contract_forbidden(value: str | Path, forbidden: list[str], base: Path) -> bool:
    """Mirror the build guard's contract-forbidden glob matching."""
    raw = str(value).strip("'\"")
    if not raw:
        return False
    normalized = raw.replace("\\", "/").lstrip("./")
    resolved = _resolve(raw, base).as_posix()
    basename = Path(normalized).name
    for pattern_value in forbidden:
        pattern = str(pattern_value).replace("\\", "/").rstrip("/")
        if not pattern:
            continue
        patterns = {pattern}
        pending = [pattern]
        while pending:
            candidate_pattern = pending.pop()
            offset = candidate_pattern.find("**/")
            if offset >= 0:
                without_recursive = (
                    candidate_pattern[:offset] + candidate_pattern[offset + 3:]
                )
                if without_recursive not in patterns:
                    patterns.add(without_recursive)
                    pending.append(without_recursive)
        parts = [part for part in normalized.split("/") if part]
        suffixes = {"/".join(parts[index:]) for index in range(len(parts))}
        candidates = {normalized, resolved, basename, *suffixes}
        if any(
            fnmatch.fnmatch(candidate, candidate_pattern)
            for candidate in candidates
            for candidate_pattern in patterns
        ):
            return True
        if not Path(pattern).is_absolute():
            resolved_path = Path(resolved)
            relative = (
                resolved_path.relative_to(base).as_posix()
                if resolved_path.is_relative_to(base)
                else ""
            )
            if relative and fnmatch.fnmatch(relative, pattern):
                return True
    return False


def _forbidden_matches(
    paths_changed: list[str], forbidden: list[str], workdir: Path
) -> list[str]:
    """Return changed paths prohibited by the active build contract."""
    return [
        path
        for path in paths_changed
        if _matches_contract_forbidden(path, forbidden, workdir)
    ]


def create_branch(
    paths: Paths,
    run_id: str,
    workdir: Path,
    chunk_id: str,
    title: str,
) -> dict[str, Any]:
    """Create and journal a new chunk branch from an up-to-date main."""
    _active_lease(paths, run_id)
    _git(workdir, "checkout", "main")
    _git(workdir, "pull", "--ff-only")
    branch = branch_name(run_id, chunk_id, title)
    _git(workdir, "checkout", "-b", branch)
    recorded = record_event(paths, run_id, {
        "type": "chunk_started",
        "chunk_id": chunk_id,
        "detail": {"branch": branch},
    })
    return {"branch": branch, "chunk_id": chunk_id, "event": recorded["event"]}


def _changed_paths(status: str) -> list[str]:
    changed: list[str] = []
    for line in status.splitlines():
        path = line[3:] if len(line) >= 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        if path:
            changed.append(path)
    return changed


def open_pr(
    paths: Paths,
    run_id: str,
    workdir: Path,
    chunk_id: str,
    title: str,
    body_file: Path,
) -> dict[str, Any]:
    """Commit, push, open, and journal a PR for a leased chunk branch."""
    lease = _active_lease(paths, run_id)
    if not body_file.exists():
        raise BuildError(f"body file does not exist: {body_file}")

    branch = _git(workdir, "rev-parse", "--abbrev-ref", "HEAD").strip()
    namespace = f"push/{run_id}-{chunk_id}-"
    if not branch.startswith(namespace):
        raise BuildError(f"current branch is outside the run namespace: {branch}")

    changed = _changed_paths(
        _git(workdir, "status", "--porcelain", "--untracked-files=all")
    )
    forbidden = _forbidden_matches(
        changed, lease.get("contract_forbidden_paths", []), workdir
    )
    if forbidden:
        raise BuildError(f"contract-forbidden paths changed: {', '.join(forbidden)}")
    if not changed:
        raise BuildError("nothing to commit")

    _git(workdir, "add", "-A")
    _git(workdir, "commit", "-m", title)
    _git(workdir, *push_args(branch))

    completed = subprocess.run(
        [
            "gh", "pr", "create",
            "--repo", lease["repo"],
            "--head", branch,
            "--title", title,
            "--body-file", str(body_file),
        ],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise BuildError(f"gh pr create failed: {completed.stderr.strip()}")
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not lines:
        raise BuildError("gh pr create failed: no PR URL returned")
    pr_url = lines[-1]
    try:
        pr_number = int(pr_url.rstrip("/").rsplit("/", 1)[1])
    except (IndexError, ValueError) as exc:
        raise BuildError(f"gh pr create returned an invalid PR URL: {pr_url}") from exc

    recorded = record_event(paths, run_id, {
        "type": "pr_opened",
        "chunk_id": chunk_id,
        "pr_url": pr_url,
        "detail": {"branch": branch, "pr_number": pr_number},
    })
    return {
        "branch": branch,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "event": recorded["event"],
    }
