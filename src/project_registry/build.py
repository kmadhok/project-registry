"""Machine-owned autonomous build journal, project state, and reporting."""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass, replace
from typing import Any, Iterable
from urllib.parse import urlparse

from .github.snapshot import parse_ts
from .push_runs import _read_journal as _read_push_journal
from .push_runs import _since_date
from .storage import Paths, append_jsonl, read_json, write_json

EVENT_TYPES = frozenset({
    "run_started", "candidate_selected", "contract_bootstrapped",
    "contract_repaired", "chunk_started", "pr_opened", "verify_passed",
    "verify_failed", "review_verdict", "merged", "merge_conflict", "reverted",
    "chunk_rejected", "chunk_skipped", "guard_denied", "needs_intent",
    "reconciled", "crashed", "stopped", "resumed", "run_finished",
})

RUN_OUTCOMES = frozenset({
    "completed", "budget_exhausted", "roadmap_done", "needs_intent",
    "blocked_by_policy", "no_candidate", "lease_held", "registry_dirty",
    "preflight_failed", "clone_failed", "contract_broken", "baseline_red",
    "codex_unavailable", "aborted", "stopped", "crashed", "paused",
    "shadow_completed", "forced_named",
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
    result = replace(state)
    latest_ts = result.last_run_at
    latest_run_id = result.last_run_id
    last_outcome = result.last_outcome
    for event in events_for_run:
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
        elif event_type == "run_finished":
            last_outcome = event.get("outcome")
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
