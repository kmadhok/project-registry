"""Machine-owned autonomous build journal, state, reporting, and run lifecycle."""

from __future__ import annotations

import datetime as dt
import json
import os
import platform
import re
import socket
import statistics
import subprocess
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from .github.snapshot import parse_ts
from .push_runs import _read_journal as _read_push_journal
from .push_runs import _since_date
from .storage import Paths, append_jsonl, read_json, write_json

__all__ = [
    "EVENT_TYPES", "RUN_OUTCOMES", "PAUSED_REASONS", "FAILURE_OUTCOMES",
    "REVIEW_CLASS_VOCABULARY", "REVIEW_VERDICT_SCHEMA", "BuildError", "Verdict",
    "parse_verdict", "verdict_allows_merge", "BreakerConfig", "ProjectBuildState",
    "append_event", "read_events", "load_state", "save_state", "apply_run_to_state",
    "resume_project", "build_report", "registry_checkout_status", "get_build_context",
    "begin_run", "record_event", "reconcile", "reconcile_done", "finish_run",
    "confirm_writeback", "shadow_gate",
    "build_env",
]

EVENT_TYPES = frozenset({
    "run_started", "candidate_selected", "contract_bootstrapped",
    "contract_repaired", "chunk_started", "pr_opened", "verify_passed",
    "verify_failed", "review_verdict", "merged", "merge_conflict", "reverted",
    "chunk_rejected", "chunk_skipped", "guard_denied", "needs_intent",
    "reconciled", "writeback_confirmed", "crashed", "stopped", "resumed",
    "run_finished",
})

RUN_OUTCOMES = frozenset({
    "completed", "budget_exhausted", "roadmap_done", "needs_intent",
    "blocked_by_policy", "no_candidate", "lease_held", "registry_dirty",
    "preflight_failed", "clone_failed", "contract_broken", "baseline_red",
    "codex_unavailable", "aborted", "stopped", "crashed", "paused",
    "shadow_completed", "forced_named", "finalize_pending",
})

PAUSED_REASONS = frozenset({
    "consecutive_failures", "revert", "contract_broken", "baseline_red", "owner",
})
FAILURE_OUTCOMES = frozenset({"aborted", "codex_unavailable", "crashed"})
NONE_BUCKET = "(none)"

REVIEW_CLASS_VOCABULARY = frozenset({
    "dependencies", "ci", "generated_data", "public_api", "migrations",
    "personal_data", "plan", "contract", "none",
})

REVIEW_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"enum": ["approve", "request_changes", "reject"]},
        "reasons": {"type": "array", "items": {"type": "string"}},
        "risk_flags": {"type": "array", "items": {"type": "string"}},
        "classes_seen": {
            "type": "array",
            "items": {"enum": sorted(REVIEW_CLASS_VOCABULARY)},
        },
    },
    "required": ["verdict", "reasons", "risk_flags", "classes_seen"],
    "additionalProperties": False,
    "allOf": [{
        "if": {"properties": {"verdict": {"const": "approve"}}},
        "else": {"properties": {"reasons": {"minItems": 1}}},
    }],
}


class BuildError(RuntimeError):
    """A build journal or state record is invalid."""


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reasons: list[str]
    risk_flags: list[str]
    classes_seen: list[str]


def parse_verdict(text: str) -> Verdict:
    """Extract and validate the last JSON object in reviewer output."""
    decoder = json.JSONDecoder()
    candidate: Any = None
    for position, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidate = value
    if candidate is None:
        raise BuildError("review verdict contains no JSON object")

    required = {"verdict", "reasons", "risk_flags", "classes_seen"}
    unknown = set(candidate) - required
    if unknown:
        raise BuildError(f"review verdict has unknown keys: {sorted(unknown)}")
    missing = required - set(candidate)
    if missing:
        raise BuildError(f"review verdict is missing keys: {sorted(missing)}")

    verdict = candidate["verdict"]
    if not isinstance(verdict, str) or verdict not in {
        "approve", "request_changes", "reject",
    }:
        raise BuildError("review verdict is unknown")
    for field in ("reasons", "risk_flags", "classes_seen"):
        value = candidate[field]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise BuildError(f"review verdict {field} must be a list of strings")
    if verdict != "approve" and not candidate["reasons"]:
        raise BuildError("review verdict reasons must be non-empty unless approved")
    unknown_classes = set(candidate["classes_seen"]) - REVIEW_CLASS_VOCABULARY
    if unknown_classes:
        raise BuildError(f"review verdict has unknown classes: {sorted(unknown_classes)}")
    return Verdict(
        verdict=verdict,
        reasons=list(candidate["reasons"]),
        risk_flags=list(candidate["risk_flags"]),
        classes_seen=list(candidate["classes_seen"]),
    )


def verdict_allows_merge(verdict: Verdict) -> bool:
    """Return whether an independent review permits merging the chunk."""
    return verdict.verdict == "approve"


@dataclass(frozen=True)
class BreakerConfig:
    max_consecutive_failures: int = 3


@dataclass(frozen=True)
class ProjectBuildState:
    last_run_at: str | None = None
    last_success_at: str | None = None
    consecutive_failures: int = 0
    paused_reason: str | None = None
    paused_at: str | None = None
    chunks_merged_total: int = 0
    needs_intent_proposal_id: str | None = None
    last_run_id: str | None = None
    last_outcome: str | None = None
    waiting_on: dict | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> "ProjectBuildState":
        if not isinstance(raw, dict):
            raise BuildError("project build state must be an object")
        values = {field: raw.get(field) for field in cls.__dataclass_fields__}
        values["consecutive_failures"] = raw.get("consecutive_failures", 0)
        values["chunks_merged_total"] = raw.get("chunks_merged_total", 0)
        if isinstance(values["consecutive_failures"], bool) or not isinstance(
            values["consecutive_failures"], int
        ):
            raise BuildError("consecutive_failures must be an integer")
        if isinstance(values["chunks_merged_total"], bool) or not isinstance(
            values["chunks_merged_total"], int
        ):
            raise BuildError("chunks_merged_total must be an integer")
        if values["paused_reason"] not in PAUSED_REASONS | {None}:
            raise BuildError("paused_reason is unknown")
        waiting_on = values["waiting_on"]
        if waiting_on is not None:
            if not isinstance(waiting_on, dict):
                raise BuildError("waiting_on must be an object or null")
            required = {"kind", "classes", "since", "run_id"}
            if set(waiting_on) != required:
                raise BuildError(
                    "waiting_on must contain exactly kind, classes, since, and run_id"
                )
            if waiting_on["kind"] not in {"blocked_by_policy", "needs_intent"}:
                raise BuildError("waiting_on kind must be blocked_by_policy or needs_intent")
            classes = waiting_on["classes"]
            if not isinstance(classes, list) or any(
                not isinstance(item, str) for item in classes
            ):
                raise BuildError("waiting_on classes must be a list of strings")
            if classes != sorted(set(classes)):
                raise BuildError("waiting_on classes must be sorted and unique")
            for field in ("since", "run_id"):
                if not isinstance(waiting_on[field], str) or not waiting_on[field].strip():
                    raise BuildError(f"waiting_on {field} must be a non-empty string")
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utc_iso(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat()


def append_event(
    paths: Paths, event: dict[str, Any], now: dt.datetime | None = None
) -> dict[str, Any]:
    """Validate and append one normalized event to the build journal."""
    if not isinstance(event, dict):
        raise BuildError("event must be an object")
    normalized = {
        "ts": event.get("ts") or _utc_iso(now or dt.datetime.now(dt.timezone.utc)),
        "run_id": event.get("run_id"),
        "host": event.get("host"),
        "type": event.get("type"),
        "project_id": event.get("project_id"),
        "chunk_id": event.get("chunk_id"),
        "pr_url": event.get("pr_url"),
        "tag": event.get("tag"),
        "outcome": event.get("outcome"),
        "reason": event.get("reason"),
        "detail": event.get("detail"),
    }
    error = _event_error(normalized)
    if error:
        raise BuildError(error)
    if normalized["type"] != "run_finished" and normalized["outcome"] is None:
        normalized.pop("outcome")
    append_jsonl(paths.build_runs_file, normalized)
    return normalized


def read_events(
    paths: Paths, since: str | dt.date | None = None
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read valid events while reporting malformed journal lines."""
    since_date = _since_date(since)
    lines = (
        paths.build_runs_file.read_text(encoding="utf-8").splitlines()
        if paths.build_runs_file.exists() else []
    )
    events: list[dict[str, Any]] = []
    malformed: list[dict[str, Any]] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            malformed.append({"line": number, "error": "invalid JSON"})
            continue
        error = _event_error(raw)
        if error:
            malformed.append({"line": number, "error": error})
            continue
        timestamp = parse_ts(raw["ts"])
        if since_date and timestamp.date() < since_date:
            continue
        events.append(raw)
    return events, {
        "path": str(paths.build_runs_file),
        "total_lines": len(lines),
        "valid_events": len(events),
        "malformed_count": len(malformed),
        "malformed": malformed,
    }


def _event_error(raw: Any) -> str | None:
    if not isinstance(raw, dict):
        return "expected a JSON object"
    if not isinstance(raw.get("run_id"), str) or not raw["run_id"].strip():
        return "run_id must be a non-empty string"
    if not isinstance(raw.get("host"), str) or not raw["host"].strip():
        return "host must be a non-empty string"
    if raw.get("type") not in EVENT_TYPES:
        return "type is missing or unknown"
    timestamp = parse_ts(raw.get("ts"))
    if timestamp is None or timestamp.utcoffset() != dt.timedelta(0):
        return "ts must be an ISO-8601 UTC timestamp"
    outcome = raw.get("outcome")
    if raw["type"] == "run_finished":
        if outcome not in RUN_OUTCOMES:
            return "run_finished outcome is missing or unknown"
    elif outcome is not None:
        return "outcome is only valid for run_finished"
    for field in ("project_id", "chunk_id", "tag", "reason"):
        if raw.get(field) is not None and not isinstance(raw[field], str):
            return f"{field} must be a string or null"
    if raw.get("pr_url") is not None and not _is_github_pr_url(raw["pr_url"]):
        return "pr_url must be a GitHub pull-request URL or null"
    if raw.get("detail") is not None and not isinstance(raw["detail"], dict):
        return "detail must be an object or null"
    return None


def _is_github_pr_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    parts = [part for part in parsed.path.split("/") if part]
    return (
        parsed.scheme == "https" and parsed.netloc.lower() == "github.com"
        and len(parts) == 4 and parts[2] == "pull" and parts[3].isdigit()
        and int(parts[3]) > 0
    )


def load_state(paths: Paths) -> dict[str, ProjectBuildState]:
    raw = read_json(paths.build_state_file, {})
    if not isinstance(raw, dict):
        raise BuildError("build state must be an object keyed by project id")
    return {str(project_id): ProjectBuildState.from_dict(value)
            for project_id, value in raw.items()}


def save_state(paths: Paths, state: dict[str, ProjectBuildState]) -> None:
    write_json(paths.build_state_file, {
        project_id: value.to_dict() for project_id, value in sorted(state.items())
    })


def apply_run_to_state(
    state: ProjectBuildState,
    events_for_run: Iterable[dict[str, Any]],
    *,
    config: BreakerConfig = BreakerConfig(),
) -> ProjectBuildState:
    """Purely fold one run's ordered events into a project's state."""
    events = list(events_for_run)
    run_id = next((event.get("run_id") for event in events if event.get("run_id")), None)
    if run_id is not None and state.last_run_id == run_id:
        return state
    result = replace(state)
    latest_ts = result.last_run_at
    latest_run_id = result.last_run_id
    last_outcome = result.last_outcome
    blocked_classes: set[str] = set()
    for event in events:
        event_type = event.get("type")
        event_ts = event.get("ts")
        latest_ts = event_ts or latest_ts
        latest_run_id = event.get("run_id") or latest_run_id
        if event_type == "chunk_rejected":
            result = replace(result, consecutive_failures=result.consecutive_failures + 1)
        elif event_type == "merged":
            result = replace(
                result,
                consecutive_failures=0,
                chunks_merged_total=result.chunks_merged_total + 1,
                last_success_at=event_ts,
            )
        elif event_type == "reverted":
            result = replace(result, paused_reason="revert", paused_at=event_ts)
        elif event_type == "chunk_skipped" and event.get("reason") == "blocked_by_policy":
            classes = (event.get("detail") or {}).get("classes")
            if isinstance(classes, str):
                blocked_classes.update(
                    item.strip() for item in classes.split(",") if item.strip()
                )
            elif isinstance(classes, list):
                blocked_classes.update(
                    item.strip() for item in classes
                    if isinstance(item, str) and item.strip()
                )
        elif event_type == "run_finished":
            last_outcome = event.get("outcome")
            waiting_on = None
            if last_outcome in {"blocked_by_policy", "needs_intent"}:
                waiting_on = {
                    "kind": last_outcome,
                    "classes": sorted(blocked_classes),
                    "since": event_ts,
                    "run_id": event.get("run_id"),
                }
            result = replace(result, waiting_on=waiting_on)
            if last_outcome in FAILURE_OUTCOMES:
                result = replace(
                    result, consecutive_failures=result.consecutive_failures + 1
                )
            if last_outcome in {"contract_broken", "baseline_red"}:
                result = replace(
                    result, paused_reason=last_outcome, paused_at=event_ts
                )
        if (
            result.consecutive_failures >= config.max_consecutive_failures
            and result.paused_reason is None
        ):
            result = replace(
                result, paused_reason="consecutive_failures", paused_at=event_ts
            )
    return replace(
        result,
        last_run_at=latest_ts,
        last_run_id=latest_run_id,
        last_outcome=last_outcome,
    )


def resume_project(
    paths: Paths, project_id: str, now: dt.datetime | None = None
) -> ProjectBuildState:
    now = now or dt.datetime.now(dt.timezone.utc)
    timestamp = _utc_iso(now)
    states = load_state(paths)
    resumed = replace(
        states.get(project_id, ProjectBuildState()),
        paused_reason=None,
        paused_at=None,
        consecutive_failures=0,
    )
    states[project_id] = resumed
    save_state(paths, states)
    append_event(paths, {
        "run_id": f"manual-{timestamp}",
        "host": "cli",
        "type": "resumed",
        "project_id": project_id,
    }, now=now)
    return resumed


def _counts(records: Iterable[dict[str, Any]], field: str) -> dict[str, int]:
    counts = Counter(
        NONE_BUCKET if record.get(field) is None else str(record[field])
        for record in records
    )
    return dict(sorted(counts.items()))


def build_report(
    paths: Paths, *, since: str | dt.date | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Aggregate build activity, breaker state, and legacy push-run count."""
    del now  # Reserved for time-relative report fields.
    since_date = _since_date(since)
    events, journal = read_events(paths, since=since_date)
    finished = [event for event in events if event["type"] == "run_finished"]
    started = [event for event in events if event["type"] == "chunk_started"]
    merged = [event for event in events if event["type"] == "merged"]
    rejected = [event for event in events if event["type"] == "chunk_rejected"]
    skipped = [event for event in events if event["type"] == "chunk_skipped"]

    starts = {
        (event["run_id"], event.get("chunk_id")): parse_ts(event["ts"])
        for event in started if event.get("chunk_id") is not None
    }
    durations = []
    for event in merged:
        start = starts.get((event["run_id"], event.get("chunk_id")))
        end = parse_ts(event["ts"])
        if start is not None and end is not None and end >= start:
            durations.append((end - start).total_seconds() / 60)

    legacy, _legacy_meta = _read_push_journal(paths.push_runs_file, since_date)
    states = load_state(paths)
    paused = [
        {"project_id": project_id, **state.to_dict()}
        for project_id, state in sorted(states.items()) if state.paused_reason is not None
    ]
    return {
        "since": since_date.isoformat() if since_date else None,
        "journal": journal,
        "runs": {
            "total": len(finished),
            "by_outcome": _counts(finished, "outcome"),
            "by_host": _counts(finished, "host"),
            "by_project": _counts(finished, "project_id"),
        },
        "chunks": {
            "started": len(started),
            "merged": len(merged),
            "rejected_by_reason": _counts(rejected, "reason"),
            "skipped_by_reason": _counts(skipped, "reason"),
        },
        "reverts": sum(event["type"] == "reverted" for event in events),
        "guard_denials": sum(event["type"] == "guard_denied" for event in events),
        "needs_intent_events": sum(event["type"] == "needs_intent" for event in events),
        "minutes_per_merged_chunk": {
            "count": len(durations),
            "mean": statistics.mean(durations) if durations else None,
            "median": statistics.median(durations) if durations else None,
        },
        "paused_projects": paused,
        "legacy": {"runs": len(legacy)},
    }


# -- run lifecycle -------------------------------------------------------


def registry_checkout_status(root: Path) -> dict[str, Any]:
    """Return the registry checkout state, ignoring machine build output."""
    root = Path(root)
    probe = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"], cwd=root,
        text=True, capture_output=True, check=False,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        return {"is_git": False, "branch": None, "clean": True, "dirty_paths": []}
    branch_result = subprocess.run(
        ["git", "branch", "--show-current"], cwd=root,
        text=True, capture_output=True, check=False,
    )
    status_result = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root,
        text=True, capture_output=True, check=False,
    )
    if status_result.returncode != 0:
        raise BuildError(f"cannot inspect registry checkout: {status_result.stderr.strip()}")
    dirty: list[str] = []
    for line in status_result.stdout.splitlines():
        value = line[3:] if len(line) >= 4 else line
        if " -> " in value:
            value = value.split(" -> ", 1)[1]
        value = value.strip().strip('"')
        # Machine-written run output and builder-filed proposals are swept into
        # the next write-back; they never block a run.
        if (
            value == "DASHBOARD.md"
            or value.startswith("data/build/")
            or value.startswith("data/proposals/")
        ):
            continue
        dirty.append(value)
    return {
        "is_git": True,
        "branch": branch_result.stdout.strip() or None,
        "clean": not dirty,
        "dirty_paths": dirty,
    }


def _now(value: dt.datetime | None) -> dt.datetime:
    value = value or dt.datetime.now(dt.timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)


def _host_label(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower())
    return normalized.strip("-")[:20] or "unknown"


def _run_id(host: str, now: dt.datetime, supplied: str | None = None) -> str:
    return supplied or (
        f"{now.strftime('%Y%m%dT%H%M%SZ')}-{_host_label(host)}-{os.urandom(2).hex()}"
    )


def _read_lease(paths: Paths) -> dict[str, Any] | None:
    raw = read_json(paths.build_lease_file)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BuildError("build lease must be an object")
    return raw


def _lease_expired(lease: dict[str, Any], now: dt.datetime) -> bool:
    started = parse_ts(lease.get("started_at"))
    ttl = lease.get("ttl_seconds")
    if started is None or not isinstance(ttl, int):
        return True
    return now > started + dt.timedelta(seconds=ttl)


def _create_lease(paths: Paths, lease: dict[str, Any]) -> bool:
    paths.build_lease_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            paths.build_lease_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(lease, handle, indent=2)
        handle.write("\n")
    return True


def _attempt_finished(
    paths: Paths, run_id: str, host: str, outcome: str, now: dt.datetime,
    *, project_id: str | None = None, detail: dict[str, Any] | None = None,
) -> None:
    append_event(paths, {
        "run_id": run_id, "host": host, "type": "run_finished",
        "project_id": project_id, "outcome": outcome, "detail": detail,
    }, now=now)


def get_build_context(
    paths: Paths, registry: Any, snapshot: Any, project_id: str,
    now: dt.datetime | dt.date,
) -> dict[str, Any]:
    """Assemble the authoritative context supplied to a build run."""
    from .automation import classify
    from .contracts import contract_expectations
    from .proposals import last_owner_action

    project = registry.get(project_id)
    if project is None:
        raise BuildError(f"unknown project id: {project_id}")
    states = load_state(paths)
    eligibility = classify(
        project, states.get(project_id), snapshot.get(project.repo), now,
        owner_action_at=last_owner_action(paths).get(project_id),
    ).to_dict()
    events, _ = read_events(paths)
    summaries: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        if event.get("project_id") == project_id:
            grouped.setdefault(event["run_id"], []).append(event)
    for current_run, run_events in grouped.items():
        started = next(
            (item["ts"] for item in run_events if item["type"] == "run_started"),
            run_events[0]["ts"],
        )
        finished = next(
            (item for item in reversed(run_events) if item["type"] == "run_finished"),
            None,
        )
        summaries.append({
            "run_id": current_run,
            "started": started,
            "outcome": finished.get("outcome") if finished else None,
            "merges": sum(item["type"] == "merged" for item in run_events),
        })
    summaries.sort(key=lambda item: item["started"], reverse=True)
    state = states.get(project_id)
    return {
        "project_id": project.id,
        "name": project.name,
        "repo": project.repo,
        "purpose": project.purpose,
        "desired_outcome": project.desired_outcome,
        "brief": project.brief.to_dict(),
        "automation": project.automation.to_dict(),
        "eligibility": eligibility,
        "contract_expectations": contract_expectations(project),
        "last_runs": summaries[:5],
        "build_state": state.to_dict() if state else None,
        "do_not_read": [
            "data/understanding/*.json — evidence briefs are stale hints; clone is code truth"
        ],
    }


def begin_run(
    paths: Paths, *, host: str, project_id: str | None = None,
    force_named: bool = False, ttl_seconds: int = 10800,
    now: dt.datetime | None = None, registry: Any = None, snapshot: Any = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Preflight, select a candidate, and atomically acquire the build lease."""
    from .automation import build_queue, classify
    from .github.sync import load_snapshot
    from .proposals import last_owner_action
    from .storage import load_registry

    current = _now(now)
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds < 1:
        raise BuildError("ttl_seconds must be an integer >= 1")
    attempt_id = _run_id(host, current, run_id)
    existing = _read_lease(paths)
    if existing is not None and existing.get("status") == "finalize_pending":
        return {
            "outcome": "finalize_pending",
            "actions": [{"action": "confirm_writeback", "run_id": existing["run_id"]}],
            "lease": existing,
        }
    checkout = registry_checkout_status(paths.root)
    detail = dict(checkout)
    if not checkout["is_git"]:
        detail["preflight"] = "not_a_git_checkout"
    elif not checkout["clean"] or checkout["branch"] not in {"main", "master"}:
        _attempt_finished(
            paths, attempt_id, host, "registry_dirty", current,
            project_id=project_id, detail=detail,
        )
        return {"outcome": "registry_dirty", "detail": detail}
    if paths.build_stop_file.exists():
        _attempt_finished(paths, attempt_id, host, "stopped", current, project_id=project_id)
        return {"outcome": "stopped"}

    if existing is not None:
        if _lease_expired(existing, current):
            result = reconcile(paths, now=current)
            return {"outcome": "reconciling", **result}
        _attempt_finished(
            paths, attempt_id, host, "lease_held", current,
            project_id=project_id, detail={"held_by": existing.get("run_id")},
        )
        return {"outcome": "lease_held", "lease": existing}

    registry = registry or load_registry(paths)
    snapshot = snapshot or load_snapshot(paths)
    states = load_state(paths)
    owner_actions = last_owner_action(paths)
    forced = False
    if project_id is not None:
        project = registry.get(project_id)
        if project is None:
            raise BuildError(f"unknown project id: {project_id}")
        eligibility = classify(
            project, states.get(project.id), snapshot.get(project.repo), current.date(),
            owner_action_at=owner_actions.get(project.id),
        ).to_dict()
        if eligibility["state"] != "ready":
            if not force_named:
                reason = f"{project.id} is {eligibility['state']}: " + "; ".join(
                    eligibility["reasons"]
                )
                _attempt_finished(
                    paths, attempt_id, host, "no_candidate", current,
                    project_id=project.id, detail={"reason": reason},
                )
                return {"outcome": "no_candidate", "reason": reason}
            forced = True
    else:
        queue = build_queue(
            registry, snapshot, states, current.date(), owner_actions=owner_actions
        )
        selected = next(
            (item for item in queue["candidates"] if item["state"] == "ready"), None
        )
        if selected is None:
            _attempt_finished(
                paths, attempt_id, host, "no_candidate", current,
                detail={"by_state": queue["by_state"]},
            )
            return {"outcome": "no_candidate", "by_state": queue["by_state"]}
        project_id = selected["project_id"]
        project = registry.require(project_id)
        eligibility = selected

    automation = project.automation
    lease = {
        "run_id": attempt_id,
        "host": host,
        "pid": os.getpid(),
        "project_id": project.id,
        "repo": project.repo,
        "dry_run": eligibility["dry_run"],
        "started_at": _utc_iso(current),
        "ttl_seconds": ttl_seconds,
        "status": "active",
        "prs_open": 0,
        "merges": 0,
        "chunks": {},
        "allow": [item.value for item in automation.allow],
        "budget": {
            "chunks_per_run": automation.budget.chunks_per_run,
            "minutes_per_run": automation.budget.minutes_per_run,
        },
        "contract_forbidden_paths": [],
        "reconcile_targets": [],
    }
    if not _create_lease(paths, lease):
        held = _read_lease(paths)
        _attempt_finished(
            paths, attempt_id, host, "lease_held", current,
            project_id=project.id, detail={"held_by": held.get("run_id") if held else None},
        )
        return {"outcome": "lease_held", "lease": held}
    append_event(paths, {
        "run_id": attempt_id, "host": host, "type": "run_started",
        "project_id": project.id,
        "detail": {
            **({"preflight": "not_a_git_checkout"} if not checkout["is_git"] else {}),
            **({"forced_named": True} if forced else {}),
        } or None,
    }, now=current)
    append_event(paths, {
        "run_id": attempt_id, "host": host, "type": "candidate_selected",
        "project_id": project.id, "detail": eligibility,
    }, now=current)
    context = get_build_context(paths, registry, snapshot, project.id, current)
    candidate = {
        **context,
        "state": eligibility["state"],
        "reasons": eligibility["reasons"],
        "rank_key": eligibility["rank_key"],
        "dry_run": eligibility["dry_run"],
    }
    result = {"run_id": attempt_id, "lease": lease, "candidate": candidate}
    if forced:
        result["outcome"] = "forced_named"
    return result


def record_event(paths: Paths, run_id: str, event: dict[str, Any]) -> dict[str, Any]:
    """Record one event for the leased run and update guard-facing counters."""
    lease = _read_lease(paths)
    if lease is None or lease.get("run_id") != run_id:
        raise BuildError("build lease does not match run_id")
    if lease.get("status") == "finalize_pending" and event.get("type") != "run_finished":
        raise BuildError("run is finalize_pending; only finish is allowed")
    payload = dict(event)
    payload.update({
        "run_id": run_id,
        "host": lease["host"],
        "project_id": lease.get("project_id"),
    })
    event_type = payload.get("type")
    detail = payload.get("detail") or {}
    chunk_id = payload.get("chunk_id")
    if event_type in {
        "pr_opened", "verify_passed", "verify_failed", "review_verdict",
        "merged", "chunk_rejected", "chunk_skipped",
    } and not chunk_id:
        raise BuildError(f"{event_type} requires chunk_id")
    chunks = lease.setdefault("chunks", {})
    chunk = chunks.setdefault(chunk_id, {
        "pr_number": None, "pr_url": None, "branch": None, "verify": None,
        "verdict": None, "merged": False, "tag": None,
    }) if chunk_id else None
    if event_type == "pr_opened":
        pr_url = payload.get("pr_url") or detail.get("pr_url")
        number = detail.get("pr_number")
        if number is None and pr_url:
            number = int(pr_url.rstrip("/").rsplit("/", 1)[1])
        was_open = (
            bool(chunk.get("pr_number")) and bool(chunk.get("branch"))
            and not chunk.get("merged")
        )
        chunk.update({
            "pr_number": number, "pr_url": pr_url,
            "branch": detail.get("branch"),
        })
        if not was_open:
            lease["prs_open"] += 1
    elif event_type in {"verify_passed", "verify_failed"}:
        chunk["verify"] = "passed" if event_type == "verify_passed" else "failed"
    elif event_type == "review_verdict":
        verdict = detail.get("verdict")
        if verdict not in {"approve", "request_changes", "reject"}:
            raise BuildError("review_verdict detail.verdict is invalid")
        chunk["verdict"] = verdict
    elif event_type == "merged":
        was_open = (
            bool(chunk.get("pr_number")) and bool(chunk.get("branch"))
            and not chunk.get("merged")
        )
        chunk["merged"] = True
        chunk["tag"] = payload.get("tag") or detail.get("tag")
        if was_open and lease["prs_open"] > 0:
            lease["prs_open"] -= 1
        lease["merges"] += 1
    elif event_type in {"chunk_rejected", "chunk_skipped"}:
        if chunk.get("pr_number") and chunk.get("branch") and not chunk.get("merged"):
            lease["prs_open"] = max(0, lease["prs_open"] - 1)
            chunk["branch"] = None
    written = append_event(paths, payload)
    write_json(paths.build_lease_file, lease)
    events, _ = read_events(paths)
    return {
        "recorded": True,
        "seq": len(events),
        "lease_summary": {
            "run_id": run_id, "status": lease["status"],
            "prs_open": lease["prs_open"], "merges": lease["merges"],
        },
        "event": written,
    }


def reconcile(
    paths: Paths, *, now: dt.datetime | None = None,
    list_open_builder_prs: Any = None,
) -> dict[str, Any]:
    """Mark a dead run crashed and return cleanup actions; never mutate GitHub."""
    current = _now(now)
    lease = _read_lease(paths)
    if lease is None:
        return {"actions": [], "note": "nothing to reconcile"}
    if lease.get("status") == "finalize_pending":
        return {
            "actions": [{"action": "confirm_writeback", "run_id": lease["run_id"]}],
            "lease": lease,
        }
    if lease.get("status") == "reconciling":
        return {"actions": lease.get("reconcile_actions", []), "lease": lease}
    if not _lease_expired(lease, current):
        return {"actions": [], "note": "active lease has not expired", "lease": lease}

    append_event(paths, {
        "run_id": lease["run_id"], "host": lease["host"], "type": "crashed",
        "project_id": lease.get("project_id"),
    }, now=current)
    append_event(paths, {
        "run_id": lease["run_id"], "host": lease["host"], "type": "run_finished",
        "project_id": lease.get("project_id"), "outcome": "crashed",
    }, now=current)
    events, _ = read_events(paths)
    run_events = [item for item in events if item["run_id"] == lease["run_id"]]
    states = load_state(paths)
    project_id = lease.get("project_id")
    if project_id:
        states[project_id] = apply_run_to_state(
            states.get(project_id, ProjectBuildState()), run_events
        )
        save_state(paths, states)

    targets: dict[str, int | None] = {}
    for chunk in lease.get("chunks", {}).values():
        if chunk.get("pr_number") and not chunk.get("merged") and chunk.get("branch"):
            targets[chunk["branch"]] = chunk["pr_number"]
    if callable(list_open_builder_prs) and lease.get("repo"):
        for item in list_open_builder_prs(lease["repo"]):
            branch = item.get("headRefName")
            if isinstance(branch, str) and branch.startswith("push/"):
                targets.setdefault(branch, item.get("number"))
    actions: list[dict[str, Any]] = []
    for branch, number in sorted(targets.items()):
        if number is not None:
            actions.append({
                "action": "close_pr", "repo": lease.get("repo"), "number": number,
            })
        actions.append({
            "action": "delete_branch", "repo": lease.get("repo"), "branch": branch,
        })
    lease.update({
        "status": "reconciling",
        "reconcile_targets": sorted(targets),
        "reconcile_actions": actions,
    })
    write_json(paths.build_lease_file, lease)
    return {"actions": actions, "lease": lease}


def reconcile_done(paths: Paths, *, now: dt.datetime | None = None) -> dict[str, Any]:
    lease = _read_lease(paths)
    if lease is None:
        return {"reconciled": False, "note": "nothing to reconcile"}
    if lease.get("status") != "reconciling":
        raise BuildError("lease is not reconciling")
    append_event(paths, {
        "run_id": lease["run_id"], "host": lease["host"], "type": "reconciled",
        "project_id": lease.get("project_id"),
        "detail": {"targets": lease.get("reconcile_targets", [])},
    }, now=_now(now))
    paths.build_lease_file.unlink()
    return {"reconciled": True, "run_id": lease["run_id"]}


def _write_digest(
    paths: Paths, lease: dict[str, Any], events: list[dict[str, Any]], outcome: str,
    summary: str | None, needs_intent: list[str], inbox: dict[str, Any] | None = None,
) -> Path:
    lines = [
        f"# Build run {lease['run_id']}", "",
        f"- Project: {lease.get('project_id')}", f"- Outcome: {outcome}",
        f"- Merges: {lease.get('merges', 0)}", "",
    ]
    if lease.get("paused_reason"):
        lines.insert(4, f"- Paused reason: {lease['paused_reason']}")
    if summary:
        lines.extend(["## Summary", "", summary, ""])
    merged = [item for item in events if item["type"] == "merged"]
    if merged:
        lines.extend(["## Merged chunks", ""])
        for item in merged:
            detail = item.get("detail") or {}
            chunk = lease.get("chunks", {}).get(item.get("chunk_id"), {})
            lines.append(
                f"- {item.get('chunk_id')}: "
                f"{item.get('pr_url') or detail.get('pr_url') or chunk.get('pr_url') or 'PR unknown'}"
                f" — tag {item.get('tag') or detail.get('tag') or chunk.get('tag') or 'none'}"
            )
            if detail.get("merge_sha"):
                lines.append(f"  - Revert: `git revert {detail['merge_sha']}`")
        lines.append("")
    rejected = [item for item in events if item["type"] in {"chunk_rejected", "chunk_skipped"}]
    if rejected:
        lines.extend(["## Rejections and skips", ""])
        for item in rejected:
            detail = item.get("detail") or {}
            lines.append(
                f"- {item.get('chunk_id')}: "
                f"{item.get('reason') or detail.get('reason') or 'unspecified'}"
            )
        lines.append("")
    denials = [item for item in events if item["type"] == "guard_denied"]
    if denials:
        lines.extend(["## Guard denials", ""])
        lines.extend(f"- {item.get('reason') or 'unspecified'}" for item in denials)
        lines.append("")
    if needs_intent:
        lines.extend(["## Projects needing intent", ""])
        lines.extend(f"- {project_id}" for project_id in needs_intent)
        lines.append("")
    if inbox and inbox.get("items"):
        lines.extend(["## Needs the owner", ""])
        for item in inbox["items"]:
            lines.append(f"- [{item['kind']}] {item['project_id'] or '-'}: {item['summary']}")
            lines.append(f"  - `{item['action']}`")
        lines.append("")
    target = paths.build_digests_dir / f"{lease['run_id']}.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target


def finish_run(
    paths: Paths, run_id: str, *, outcome: str, summary: str | None = None,
    now: dt.datetime | None = None, registry: Any = None, snapshot: Any = None,
) -> dict[str, Any]:
    """Finalize a leased run and retain its lease until registry write-back."""
    from .automation import build_queue
    from .github.sync import load_snapshot

    lease = _read_lease(paths)
    if lease is None or lease.get("run_id") != run_id:
        raise BuildError("build lease does not match run_id")
    if outcome not in RUN_OUTCOMES:
        raise BuildError(f"unknown run outcome: {outcome}")
    current = _now(now)
    events, _ = read_events(paths)
    finished_event = next((
        item for item in events
        if item["run_id"] == run_id and item["type"] == "run_finished"
    ), None)
    if finished_event is None:
        denial_count = sum(
            item["run_id"] == run_id and item["type"] == "guard_denied"
            for item in events
        )
        if denial_count and outcome not in {"aborted", "crashed"}:
            raise BuildError(
                f"run {run_id} has {denial_count} guard denial(s); "
                "finish with --outcome aborted"
            )
        finished_event = append_event(paths, {
            "run_id": run_id, "host": lease["host"], "type": "run_finished",
            "project_id": lease.get("project_id"), "outcome": outcome,
            "detail": {"summary": summary} if summary else None,
        }, now=current)
    else:
        outcome = finished_event["outcome"]
        summary = (finished_event.get("detail") or {}).get("summary")
    events, _ = read_events(paths)
    run_events = [item for item in events if item["run_id"] == run_id]
    states = load_state(paths)
    project_id = lease.get("project_id")
    prior_state = states.get(project_id, ProjectBuildState())
    state_after = apply_run_to_state(prior_state, run_events)
    if project_id is not None and state_after != prior_state:
        states[project_id] = state_after
        save_state(paths, states)
    lease["paused_reason"] = state_after.paused_reason
    needs_intent: list[str] = []
    if registry is not None:
        from .proposals import last_owner_action
        snapshot = snapshot or load_snapshot(paths)
        queue = build_queue(
            registry, snapshot, states, current.date(),
            owner_actions=last_owner_action(paths),
        )
        needs_intent = [
            item["project_id"] for item in queue["candidates"]
            if item["state"] == "needs_intent"
        ]
    inbox = None
    if registry is not None:
        from .inbox import owner_inbox
        inbox = owner_inbox(paths, registry, now=current, snapshot=snapshot)
    digest = _write_digest(
        paths, lease, run_events, outcome, summary, needs_intent, inbox=inbox
    )
    lease.update({
        "status": "finalize_pending",
        "finalize": {
            "outcome": outcome,
            "digest_path": str(digest),
            "finished_at": finished_event["ts"],
        },
    })
    lease.pop("finalize_outcome", None)
    lease.pop("finalize_summary", None)
    write_json(paths.build_lease_file, lease)
    return {
        "state_after": state_after.to_dict(),
        "digest_path": str(digest),
        "circuit_breaker": {
            "paused_reason": state_after.paused_reason,
            "consecutive_failures": state_after.consecutive_failures,
        },
    }


def confirm_writeback(
    paths: Paths, run_id: str, *, now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Release a finalized lease after its registry commit has been pushed."""
    lease = _read_lease(paths)
    if (
        lease is None
        or lease.get("run_id") != run_id
        or lease.get("status") != "finalize_pending"
    ):
        raise BuildError("build lease is not finalize_pending for run_id")
    paths.build_lease_file.unlink()
    append_event(paths, {
        "run_id": run_id,
        "host": lease["host"],
        "type": "writeback_confirmed",
        "project_id": lease.get("project_id"),
        "detail": {"digest_path": (lease.get("finalize") or {}).get("digest_path")},
    }, now=_now(now))
    return {"writeback_confirmed": True, "run_id": run_id}


def build_env(root: Path) -> dict[str, Any]:
    """Resolve host-specific autonomous-builder paths without side effects."""
    root = Path(root).resolve()
    mac_root = Path(os.environ.get(
        "BUILD_ENV_MAC_ROOT", "/Users/kanumadhok/Documents/Claude/Projects/project-registry"
    ))
    pc_root = Path(os.environ.get(
        "BUILD_ENV_PC_ROOT", "/home/learnmsds/Github/project-registry"
    ))
    if mac_root.exists():
        host_kind, registry_root = "mac", mac_root.resolve()
    elif pc_root.exists():
        host_kind, registry_root = "pc", pc_root.resolve()
    else:
        host_kind, registry_root = "unknown", root
    configured_label = os.environ.get("BUILD_HOST_LABEL")
    if configured_label:
        host = _host_label(configured_label)
    elif platform.system() == "Darwin":
        host = "mac"
    else:
        host = _host_label(socket.gethostname().split(".")[0])
    registry_cli = (
        ".venv/bin/registry" if (registry_root / ".venv/bin/registry").exists()
        else f"PYTHONPATH=src python3 -m project_registry.cli --root {registry_root}"
    )
    mac_codex = Path("~/.claude/model-adapters/codex.sh").expanduser()
    pc_codex = Path("~/bin/codex").expanduser()
    codex_bin = None
    if host_kind == "mac" and mac_codex.exists():
        codex_bin = "~/.claude/model-adapters/codex.sh"
    elif host_kind == "pc" and pc_codex.exists():
        codex_bin = "~/bin/codex"
    return {
        "host": host,
        "host_kind": host_kind,
        "registry_root": str(registry_root),
        "registry_cli": registry_cli,
        "codex_bin": codex_bin,
        "workdir_base": str(registry_root.parent / "build-work"),
        "budget_defaults": {"chunks_per_run": 6, "minutes_per_run": 120},
        "ttl_seconds": 10800,
    }


def shadow_gate(paths: Paths, project_id: str, *, min_runs: int = 5) -> dict[str, Any]:
    """Evaluate whether a project's shadow-build evidence is clean enough."""
    events, _ = read_events(paths)
    run_ids = {
        event["run_id"] for event in events
        if event.get("project_id") == project_id
    }
    project_events = [event for event in events if event["run_id"] in run_ids]

    shadow_runs: list[str] = []
    for event in project_events:
        if (
            event["type"] == "run_finished"
            and event.get("outcome") == "shadow_completed"
            and event["run_id"] not in shadow_runs
        ):
            shadow_runs.append(event["run_id"])

    denials = [event for event in project_events if event["type"] == "guard_denied"]
    opened = {
        (event["run_id"], event.get("chunk_id"))
        for event in project_events if event["type"] == "pr_opened"
    }
    verdicts = {
        (event["run_id"], event.get("chunk_id"))
        for event in project_events if event["type"] == "review_verdict"
    }
    missing_verdicts = sorted(opened - verdicts)
    failure_outcomes = {
        "crashed", "registry_dirty", "aborted", "baseline_red",
        "contract_broken", "codex_unavailable",
    }
    failures = [
        event for event in project_events
        if event["type"] == "run_finished"
        and event.get("outcome") in failure_outcomes
    ]
    missing_digests = [
        run_id for run_id in shadow_runs
        if not (paths.build_digests_dir / f"{run_id}.md").exists()
    ]
    paused_reason = load_state(paths).get(project_id, ProjectBuildState()).paused_reason

    checks = [
        {
            "id": "shadow_runs",
            "pass": len(shadow_runs) >= min_runs,
            "detail": f"{len(shadow_runs)} shadow run(s); minimum {min_runs}",
        },
        {
            "id": "guard_denials",
            "pass": not denials,
            "detail": f"{len(denials)} guard denial(s)",
        },
        {
            "id": "verdict_on_every_pr",
            "pass": not missing_verdicts,
            "detail": (
                "every PR chunk has a review verdict"
                if not missing_verdicts
                else f"missing verdicts for {len(missing_verdicts)} PR chunk(s)"
            ),
        },
        {
            "id": "no_failure_outcomes",
            "pass": not failures,
            "detail": f"{len(failures)} failure outcome(s)",
        },
        {
            "id": "digest_present",
            "pass": not missing_digests,
            "detail": (
                "every shadow run has a digest"
                if not missing_digests
                else f"missing {len(missing_digests)} digest(s)"
            ),
        },
        {
            "id": "not_paused",
            "pass": paused_reason is None,
            "detail": (
                "project is not paused"
                if paused_reason is None else f"paused: {paused_reason}"
            ),
        },
    ]
    return {
        "project_id": project_id,
        "pass": all(check["pass"] for check in checks),
        "min_runs": min_runs,
        "runs_considered": shadow_runs,
        "checks": checks,
    }
