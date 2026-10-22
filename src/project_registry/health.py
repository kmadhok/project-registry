"""Read-only builder liveness derived from the run journal and UTC schedule."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from .build_runs import read_events
from .storage import Paths

UTC = dt.timezone.utc


def _timestamp(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _read_lease(paths: Paths) -> dict[str, Any] | None:
    if not paths.build_lease_file.exists():
        return None
    try:
        lease = json.loads(paths.build_lease_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lease = None
    if not isinstance(lease, dict):
        lease = {}
    return {
        key: lease.get(key) if isinstance(lease.get(key), str) else None
        for key in ("run_id", "project_id")
    }


def _next_expected(now: dt.datetime, hour: int) -> dt.datetime:
    candidate = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    while candidate <= now or candidate.weekday() >= 5:
        candidate += dt.timedelta(days=1)
    return candidate


def _weekdays_missed(
    last_date: dt.date, now: dt.datetime, expected_hour: int, grace_hours: int,
) -> int:
    missed = 0
    day = last_date + dt.timedelta(days=1)
    while day <= now.date():
        deadline = dt.datetime.combine(day, dt.time(expected_hour), tzinfo=UTC)
        deadline += dt.timedelta(hours=grace_hours)
        if day.weekday() < 5 and (day < now.date() or now >= deadline):
            missed += 1
        day += dt.timedelta(days=1)
    return missed


def preflight_attempts(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize unstarted attempts; standdowns count across the whole journal.

    Refusal counts cover only attempts strictly after the latest run start (or
    all attempts when no run started). last_attempt retains historical evidence.
    """
    starts = [event for event in events if event["type"] == "run_started"]
    run_ids = {event["run_id"] for event in starts}
    last_start = max((_timestamp(event["ts"]) for event in starts), default=None)
    attempts = []
    standdowns = 0
    counts: dict[str, int] = {}
    for event in sorted(events, key=lambda event: _timestamp(event["ts"])):
        if event["type"] != "run_finished" or event["run_id"] in run_ids:
            continue
        detail = event.get("detail") or {}
        if event.get("outcome") == "stopped" and detail.get("reason") == "inactive_host":
            standdowns += 1
            continue
        attempt = {key: event.get(key) for key in
                   ("run_id", "host", "ts", "outcome", "project_id")}
        attempt["reason"] = detail.get("reason") or event.get("reason")
        attempt["summary"] = detail.get("summary")
        attempt["detail"] = detail
        attempts.append(attempt)
        if last_start is None or _timestamp(event["ts"]) > last_start:
            outcome = event["outcome"]
            counts[outcome] = counts.get(outcome, 0) + 1
    return {
        "last_attempt": attempts[-1] if attempts else None,
        "refusals_since_last_run": counts,
        "standdowns": standdowns,
    }


def build_health(
    paths: Paths, *, now: dt.datetime, expected_hour_utc: int = 12, grace_hours: int = 2,
) -> dict[str, Any]:
    """Describe whether a started run exists for each elapsed weekday window."""
    now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    events, _ = read_events(paths)
    attempts = preflight_attempts(events)
    starts = [event for event in events if event["type"] == "run_started"]
    run_ids = {event["run_id"] for event in starts}
    events = [event for event in events if event["run_id"] in run_ids]
    events.sort(key=lambda event: _timestamp(event["ts"]))
    last_run = None
    hours_since = None
    missed = None
    status = "never"
    if starts:
        start = max(starts, key=lambda event: _timestamp(event["ts"]))
        run_events = [event for event in events if event["run_id"] == start["run_id"]]
        finish = next((event for event in reversed(run_events)
                       if event["type"] == "run_finished"), None)
        last_run = {
            "run_id": start["run_id"], "project_id": start.get("project_id"),
            "host": start["host"], "started_at": start["ts"],
            "finished_at": finish["ts"] if finish else None,
            "outcome": finish["outcome"] if finish else None,
            "merged": sum(event["type"] == "merged" for event in run_events),
        }
        started_at = _timestamp(start["ts"])
        hours_since = (now - started_at).total_seconds() / 3600
        missed = _weekdays_missed(started_at.date(), now, expected_hour_utc, grace_hours)
        status = "ok" if missed == 0 else "late" if missed == 1 else "stalled"
    health = {
        **attempts,
        "last_run": last_run,
        "hours_since_last_run": hours_since,
        "weekdays_missed": missed,
        "status": status,
        "stop_file": paths.build_stop_file.exists(),
        "lease": _read_lease(paths),
        "journal_events": len(events),
        "last_event_at": events[-1]["ts"] if events else None,
        "next_expected": _next_expected(now, expected_hour_utc).isoformat(),
    }
    health["headline"] = health_headline(health)
    return health


def health_headline(health: dict[str, Any]) -> str:
    """Render a one-line summary using only the serializable health snapshot."""
    status = health["status"].upper()
    run = health["last_run"]
    if run is None:
        headline = "NEVER · no runs in the journal"
    else:
        started = _timestamp(run["started_at"])
        # Recover the snapshot's date without consulting the wall clock.
        now = started + dt.timedelta(hours=health["hours_since_last_run"])
        date = f"{started:%a %b} {started.day}"
        project = run["project_id"] or "-"
        outcome = run["outcome"] or "crashed?"
        if status == "OK":
            when = "today" if started.date() == now.date() else date
            headline = f"OK · ran {when} · {project} · {outcome} · {run['merged']} merged"
        else:
            missed = health["weekdays_missed"]
            if status == "STALLED":
                date = f"{started:%b} {started.day}"
            weekdays = "weekday" if missed == 1 else "weekdays"
            headline = (
                f"{status} · no run since {date} ({missed} {weekdays} missed)"
                f" · last: {project} {outcome}"
            )
    attempt = health.get("last_attempt")
    if status in {"LATE", "STALLED", "NEVER"} and attempt and (
        run is None or _timestamp(attempt["ts"]) > _timestamp(run["started_at"])
    ):
        attempted = _timestamp(attempt["ts"])
        reason = attempt.get("reason") or attempt["outcome"]
        if attempt["outcome"] == "stopped" and not attempt.get("reason"):
            reason = "stopped (STOP file)"
        reason = " ".join(str(reason).split())
        headline += f" · last attempt {attempted:%b} {attempted.day}: {reason}"
    if health["stop_file"]:
        headline += " · STOP present"
    if health["lease"] is not None:
        headline += f" · lease held by {health['lease']['run_id']}"
    return headline
