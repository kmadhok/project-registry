"""Read-only reporting over the push-project run journal.

The journal is curated by the push-project workflow. This module never changes
it; an explicit refresh may update only the machine-owned PR-state cache.
"""

from __future__ import annotations

import datetime as dt
import json
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .github.client import GitHubClient, GitHubError
from .github.snapshot import iso, parse_ts
from .storage import Paths, read_json, write_json

OUTCOMES = frozenset({"spec", "chunk", "amend", "aborted", "parked", "empty_focus"})
PR_STATES = ("open", "merged", "closed", "unknown")
NONE_BUCKET = "(none)"


def build_push_report(
    *,
    paths: Paths | None = None,
    since: str | dt.date | None = None,
    refresh: bool = False,
    client: GitHubClient | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Aggregate push runs and resolve linked PRs from the local cache."""
    paths = paths or Paths.resolve()
    now = now or dt.datetime.now(dt.timezone.utc)
    since_date = _since_date(since)
    records, journal = _read_journal(paths.push_runs_file, since_date)
    cache, cache_error = _read_cache(paths.push_prs_file)
    refresh_errors: list[dict[str, Any]] = []

    pr_refs = _unique_pr_refs(records)
    if refresh:
        cache, refresh_errors = _refresh_cache(
            cache, pr_refs, client or GitHubClient(), now
        )
        write_json(paths.push_prs_file, cache)

    items = []
    state_counts = Counter({state: 0 for state in PR_STATES})
    cached_prs = cache.get("pull_requests") or {}
    for url, repo, number in pr_refs:
        cached = cached_prs.get(url) if isinstance(cached_prs, dict) else None
        state = _cached_state(cached)
        state_counts[state] += 1
        items.append({
            "url": url,
            "repo": repo,
            "number": number,
            "state": state,
            "fetched_at": cached.get("fetched_at") if isinstance(cached, dict) else None,
        })

    total_prs = len(items)
    merged = state_counts["merged"]
    return {
        "since": since_date.isoformat() if since_date else None,
        "journal": journal,
        "runs": {
            "total": len(records),
            "by_host": _counts(records, "host"),
            "by_outcome": _counts(records, "outcome"),
            "by_project": _counts(records, "project"),
            "by_gear": _counts(records, "gear"),
        },
        "pull_requests": {
            "total": total_prs,
            "by_state": {state: state_counts[state] for state in PR_STATES},
            "merge_rate": {
                "merged": merged,
                "total": total_prs,
                "percent": round(100 * merged / total_prs, 1) if total_prs else None,
            },
            "cache_fetched_at": cache.get("fetched_at"),
            "cache_error": cache_error,
            "refresh_errors": refresh_errors,
            "items": items,
        },
    }


def _read_journal(
    path: Path, since: dt.date | None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    records: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            malformed.append({"line": number, "error": "invalid JSON"})
            continue
        error = _record_error(raw)
        if error:
            malformed.append({"line": number, "error": error})
            continue
        timestamp = parse_ts(raw["ts"])
        if since and timestamp.date() < since:
            continue
        records.append(raw)
    return records, {
        "path": str(path),
        "total_lines": len(lines),
        "valid_runs": len(records),
        "malformed_count": len(malformed),
        "malformed": malformed,
    }


def _record_error(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return "expected a JSON object"
    if not isinstance(raw.get("host"), str) or not raw["host"].strip():
        return "host must be a non-empty string"
    if raw.get("outcome") not in OUTCOMES:
        return "outcome is missing or unknown"
    if parse_ts(raw.get("ts")) is None:
        return "ts must be an ISO-8601 timestamp"
    if raw.get("project") is not None and not isinstance(raw["project"], str):
        return "project must be a string or null"
    gear = raw.get("gear")
    if isinstance(gear, bool) or gear not in {None, 1, 2}:
        return "gear must be 1, 2, or null"
    if raw.get("pr") is not None and _parse_pr_url(raw["pr"]) is None:
        return "pr must be a GitHub pull-request URL or null"
    return None


def _unique_pr_refs(records: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    found: dict[str, tuple[str, str, int]] = {}
    for record in records:
        if record.get("pr") is None:
            continue
        parsed = _parse_pr_url(record["pr"])
        if parsed:
            repo, number = parsed
            found.setdefault(record["pr"], (record["pr"], repo, number))
    return [found[url] for url in sorted(found)]


def _parse_pr_url(value: Any) -> tuple[str, int] | None:
    if not isinstance(value, str):
        return None
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    if len(parts) != 4 or parts[2] != "pull":
        return None
    try:
        number = int(parts[3])
    except ValueError:
        return None
    if not parts[0] or not parts[1] or number < 1:
        return None
    return f"{parts[0]}/{parts[1]}", number


def _counts(records: list[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(
        NONE_BUCKET if record.get(field) is None else str(record[field])
        for record in records
    )
    return dict(sorted(counts.items()))


def _since_date(value: str | dt.date | None) -> dt.date | None:
    if value is None or isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("--since must be YYYY-MM-DD") from None


def _read_cache(path: Path) -> tuple[dict[str, Any], str | None]:
    try:
        raw = read_json(path, {})
    except (OSError, json.JSONDecodeError) as exc:
        return {"fetched_at": None, "pull_requests": {}}, str(exc)
    if not isinstance(raw, dict) or not isinstance(raw.get("pull_requests", {}), dict):
        return {"fetched_at": None, "pull_requests": {}}, "invalid cache shape"
    return {
        "fetched_at": raw.get("fetched_at"),
        "pull_requests": dict(raw.get("pull_requests") or {}),
    }, None


def _refresh_cache(
    cache: dict[str, Any],
    pr_refs: list[tuple[str, str, int]],
    client: GitHubClient,
    now: dt.datetime,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cached_prs = cache.setdefault("pull_requests", {})
    errors = []
    for url, repo, number in pr_refs:
        try:
            raw = client.get_pull(repo, number)
        except GitHubError as exc:
            errors.append({
                "url": url,
                "error": str(exc),
                "kept_cached": _cached_state(cached_prs.get(url)) != "unknown",
            })
            continue
        cached_prs[url] = {
            "state": _github_state(raw),
            "fetched_at": iso(now),
        }
    cache["fetched_at"] = iso(now)
    return cache, errors


def _github_state(raw: Any) -> str:
    if not isinstance(raw, dict):
        return "unknown"
    if raw.get("merged_at"):
        return "merged"
    return raw.get("state") if raw.get("state") in {"open", "closed"} else "unknown"


def _cached_state(raw: Any) -> str:
    if not isinstance(raw, dict) or raw.get("state") not in PR_STATES:
        return "unknown"
    return raw["state"]
