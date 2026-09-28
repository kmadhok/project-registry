from __future__ import annotations

import datetime as dt
import json

import pytest

from project_registry.cli import main
from project_registry.health import build_health, health_headline
from project_registry.storage import Paths


def timestamp(value):
    return dt.datetime.fromisoformat(value).replace(tzinfo=dt.timezone.utc)


def event(kind, ts, run_id="r1", **extra):
    return {
        "ts": ts + "+00:00", "run_id": run_id, "host": "builder-host",
        "type": kind, "project_id": "ai-engineering-markets",
        "chunk_id": None, "pr_url": None, "tag": None, "reason": None,
        "detail": None, **extra,
    }


def journal(tmp_path, events):
    paths = Paths(tmp_path)
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    paths.build_runs_file.write_text("".join(json.dumps(e) + "\n" for e in events))
    return paths


def test_empty_journal(tmp_path):
    health = build_health(Paths(tmp_path), now=timestamp("2026-09-28T13:00:00"))
    assert health["status"] == "never"
    assert health["weekdays_missed"] is None
    assert health["last_run"] is None
    assert health["hours_since_last_run"] is None
    assert health["journal_events"] == 0
    assert health["last_event_at"] is None
    assert health["headline"] == "NEVER · no runs in the journal"


def test_completed_run_today(tmp_path):
    paths = journal(tmp_path, [
        event("run_started", "2026-09-28T12:00:00"),
        *[event("merged", "2026-09-28T12:10:00", chunk_id=str(i)) for i in range(3)],
        event("run_finished", "2026-09-28T12:30:00", outcome="completed"),
    ])
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["status"] == "ok"
    assert health["weekdays_missed"] == 0
    assert health["hours_since_last_run"] == 1.0
    assert health["last_run"] == {
        "run_id": "r1", "project_id": "ai-engineering-markets", "host": "builder-host",
        "started_at": "2026-09-28T12:00:00+00:00",
        "finished_at": "2026-09-28T12:30:00+00:00", "outcome": "completed", "merged": 3,
    }
    assert health["journal_events"] == 5
    assert health["last_event_at"] == "2026-09-28T12:30:00+00:00"
    assert health["headline"] == "OK · ran today · ai-engineering-markets · completed · 3 merged"
    assert health_headline(json.loads(json.dumps(health))) == health["headline"]


@pytest.mark.parametrize("hour,status,missed", [(13, "ok", 0), (14, "late", 1), (15, "late", 1)])
def test_friday_run_monday_grace(tmp_path, hour, status, missed):
    paths = journal(tmp_path, [
        event("run_started", "2026-09-25T12:00:00"),
        event("run_finished", "2026-09-25T12:30:00", outcome="blocked_by_policy"),
    ])
    health = build_health(paths, now=timestamp(f"2026-09-28T{hour}:00:00"))
    assert (health["status"], health["weekdays_missed"]) == (status, missed)
    if status == "ok":
        assert health["headline"] == "OK · ran Fri Sep 25 · ai-engineering-markets · blocked_by_policy · 0 merged"
    else:
        assert health["headline"] == "LATE · no run since Fri Sep 25 (1 weekday missed) · last: ai-engineering-markets blocked_by_policy"


def test_three_weeks_stalled(tmp_path):
    paths = journal(tmp_path, [
        event("run_started", "2026-08-28T12:00:00"),
        event("run_finished", "2026-08-28T12:30:00", outcome="blocked_by_policy"),
    ])
    health = build_health(paths, now=timestamp("2026-09-28T16:00:00"))
    assert health["status"] == "stalled"
    assert health["weekdays_missed"] == 21
    assert health["headline"] == "STALLED · no run since Aug 28 (21 weekdays missed) · last: ai-engineering-markets blocked_by_policy"


def test_unfinished_run(tmp_path):
    paths = journal(tmp_path, [event("run_started", "2026-09-28T12:00:00")])
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["last_run"]["outcome"] is None
    assert health["last_run"]["finished_at"] is None
    assert "crashed?" in health["headline"]


def test_stop_and_lease(tmp_path):
    paths = journal(tmp_path, [])
    paths.build_stop_file.touch()
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["stop_file"] is True
    assert health["headline"].endswith("STOP present")
    paths.build_lease_file.write_text(json.dumps({"run_id": "r2", "project_id": "project"}))
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["lease"] == {"run_id": "r2", "project_id": "project"}
    assert health["headline"].endswith("lease held by r2")


@pytest.mark.parametrize("raw", ["{bad", "[]", "null", "", '{"run_id": []}'])
def test_malformed_lease(tmp_path, raw):
    paths = journal(tmp_path, [])
    paths.build_lease_file.write_text(raw)
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["lease"] == {"run_id": None, "project_id": None}


def test_orphans_malformed_lines_and_out_of_order_events(tmp_path):
    paths = journal(tmp_path, [
        event("run_finished", "2026-09-28T12:30:00", outcome="completed"),
        event("run_started", "2026-09-28T12:00:00"),
        event("run_started", "2026-09-25T12:00:00", run_id="older"),
        event("run_finished", "2026-09-28T12:50:00", run_id="orphan", outcome="completed"),
    ])
    with paths.build_runs_file.open("a") as handle:
        handle.write('not json\n{}\n')
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["last_run"]["run_id"] == "r1"
    assert health["last_run"]["outcome"] == "completed"
    assert health["journal_events"] == 3
    assert health["last_event_at"] == "2026-09-28T12:30:00+00:00"


@pytest.mark.parametrize("now,expected", [
    ("2026-09-25T13:00:00", "2026-09-28T12:00:00+00:00"),
    ("2026-09-29T09:00:00", "2026-09-29T12:00:00+00:00"),
    ("2026-09-29T12:00:00", "2026-09-30T12:00:00+00:00"),
])
def test_next_expected(tmp_path, now, expected):
    assert build_health(Paths(tmp_path), now=timestamp(now))["next_expected"] == expected


def test_custom_schedule(tmp_path):
    paths = journal(tmp_path, [event("run_started", "2026-09-25T12:00:00")])
    health = build_health(paths, now=timestamp("2026-09-28T15:00:00"), expected_hour_utc=15, grace_hours=1)
    assert health["weekdays_missed"] == 0
    assert health["next_expected"] == "2026-09-29T15:00:00+00:00"


def test_build_health_cli(tmp_path, capsys):
    assert main(["--root", str(tmp_path), "build-health", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "never"
    assert main(["--root", str(tmp_path), "build-health"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "NEVER · no runs in the journal"
    assert [line.split(":", 1)[0] for line in lines[1:]] == [
        "last_run", "weekdays_missed", "next_expected", "stop_file", "lease", "journal_events",
    ]
