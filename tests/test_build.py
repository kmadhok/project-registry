"""Autonomous build journal, state, breaker, and report behavior."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest
import yaml

from project_registry.build import (
    EVENT_TYPES,
    BuildError,
    ProjectBuildState,
    append_event,
    apply_run_to_state,
    build_report,
    load_state,
    parse_verdict,
    read_events,
    resume_project,
    save_state,
    verdict_allows_merge,
)
from project_registry.storage import append_jsonl


NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.timezone.utc)
ROOT = Path(__file__).resolve().parents[1]


def event(event_type: str, *, ts: str = "2026-08-22T12:00:00+00:00", **values):
    return {
        "ts": ts,
        "run_id": "run-1",
        "host": "mac",
        "type": event_type,
        "project_id": "alpha",
        "chunk_id": None,
        "pr_url": None,
        "tag": None,
        "outcome": None,
        "reason": None,
        "detail": None,
        **values,
    }


def seed_report_journal(paths):
    records = [
        event("run_started"),
        event("chunk_started", chunk_id="c1", ts="2026-08-22T12:00:00+00:00"),
        event("merged", chunk_id="c1", ts="2026-08-22T12:10:00+00:00"),
        event("chunk_started", chunk_id="c2", ts="2026-08-22T13:00:00+00:00"),
        event("merged", chunk_id="c2", ts="2026-08-22T13:30:00+00:00"),
        event("chunk_rejected", chunk_id="c3", reason="review"),
        event("chunk_rejected", chunk_id="c4", reason="verify"),
        event("chunk_skipped", chunk_id="c5", reason="policy"),
        event("reverted", chunk_id="c1"),
        event("guard_denied", reason="forbidden_path"),
        event("needs_intent"),
        event("run_finished", outcome="completed", ts="2026-08-22T14:00:00+00:00"),
    ]
    for record in records:
        append_event(paths, record)


@pytest.mark.parametrize("bad", [
    {"run_id": "r", "host": "h", "type": "unknown"},
    {"run_id": "r", "host": "h", "type": "run_finished"},
    {"run_id": "r", "host": "h", "type": "run_finished", "outcome": "bad"},
    {"host": "h", "type": "run_started"},
])
def test_append_event_rejects_invalid_records(paths, bad):
    with pytest.raises(BuildError):
        append_event(paths, bad, now=NOW)


def test_append_event_accepts_every_event_type(paths):
    for event_type in sorted(EVENT_TYPES):
        record = {"run_id": f"r-{event_type}", "host": "host", "type": event_type}
        if event_type == "run_finished":
            record["outcome"] = "completed"
        written = append_event(paths, record, now=NOW)
        assert written["type"] == event_type
        assert written["ts"] == NOW.isoformat()
    events, meta = read_events(paths)
    assert len(events) == len(EVENT_TYPES)
    assert meta["malformed_count"] == 0


def test_report_aggregates_chunks_reasons_reverts_guards_and_timing(paths):
    seed_report_journal(paths)
    report = build_report(paths)
    assert report["runs"] == {
        "total": 1,
        "by_outcome": {"completed": 1},
        "by_host": {"mac": 1},
        "by_project": {"alpha": 1},
    }
    assert report["chunks"]["started"] == 2
    assert report["chunks"]["merged"] == 2
    assert report["chunks"]["rejected_by_reason"] == {"review": 1, "verify": 1}
    assert report["chunks"]["skipped_by_reason"] == {"policy": 1}
    assert report["reverts"] == 1
    assert report["guard_denials"] == 1
    assert report["needs_intent_events"] == 1
    assert report["minutes_per_merged_chunk"] == {
        "count": 2, "mean": 20.0, "median": 20.0,
    }


def test_report_counts_valid_legacy_push_runs(paths):
    append_jsonl(paths.push_runs_file, {
        "ts": NOW.isoformat(), "host": "old", "outcome": "chunk",
        "project": "alpha", "gear": 1, "pr": None,
    })
    assert build_report(paths)["legacy"] == {"runs": 1}


def test_breaker_pauses_after_three_consecutive_rejections():
    events = [event("chunk_rejected", chunk_id=f"c{i}") for i in range(3)]
    state = apply_run_to_state(ProjectBuildState(), events)
    assert state.consecutive_failures == 3
    assert state.paused_reason == "consecutive_failures"


def test_merge_resets_consecutive_failure_count():
    events = [
        event("chunk_rejected"), event("chunk_rejected"),
        event("merged"), event("chunk_rejected"),
    ]
    state = apply_run_to_state(ProjectBuildState(), events)
    assert state.consecutive_failures == 1
    assert state.paused_reason is None
    assert state.chunks_merged_total == 1


def test_revert_and_broken_baselines_pause_immediately():
    reverted = apply_run_to_state(ProjectBuildState(), [event("reverted")])
    assert reverted.paused_reason == "revert"

    broken = apply_run_to_state(ProjectBuildState(), [
        event("run_finished", outcome="contract_broken")
    ])
    assert broken.paused_reason == "contract_broken"


def test_resume_clears_pause_and_records_event(paths):
    save_state(paths, {"alpha": ProjectBuildState(
        consecutive_failures=4, paused_reason="revert", paused_at=NOW.isoformat()
    )})
    resumed = resume_project(paths, "alpha", now=NOW)
    assert resumed.consecutive_failures == 0
    assert resumed.paused_reason is None
    assert load_state(paths)["alpha"] == resumed
    events, _ = read_events(paths)
    assert events[0]["type"] == "resumed"
    assert events[0]["run_id"] == f"manual-{NOW.isoformat()}"


def test_malformed_journal_lines_are_reported_not_fatal(paths):
    paths.build_runs_file.parent.mkdir(parents=True)
    paths.build_runs_file.write_text(
        json.dumps(event("run_started")) + "\nnot json\n{}\n", encoding="utf-8"
    )
    events, meta = read_events(paths)
    assert len(events) == 1
    assert meta["malformed_count"] == 2
    assert [item["line"] for item in meta["malformed"]] == [2, 3]
    assert build_report(paths)["journal"]["malformed_count"] == 2


@pytest.mark.parametrize("text", [
    'Review complete. {"verdict":"approve","reasons":[],"risk_flags":[],"classes_seen":["none"]}',
    '```json\n{"verdict":"reject","reasons":["tests failed"],"risk_flags":["red"],"classes_seen":["ci"]}\n```',
])
def test_parse_verdict_accepts_embedded_or_fenced_json(text):
    verdict = parse_verdict(text)
    assert verdict.verdict in {"approve", "reject"}


def test_parse_verdict_uses_last_json_object():
    text = (
        '{"verdict":"reject","reasons":["old"],"risk_flags":[],"classes_seen":["none"]}'
        " revised to "
        '{"verdict":"approve","reasons":[],"risk_flags":[],"classes_seen":["plan"]}'
    )
    assert parse_verdict(text).verdict == "approve"


@pytest.mark.parametrize("payload", [
    {"verdict": "maybe", "reasons": [], "risk_flags": [], "classes_seen": []},
    {"verdict": "reject", "risk_flags": [], "classes_seen": []},
    {"verdict": "reject", "reasons": [], "risk_flags": [], "classes_seen": []},
    {"verdict": "approve", "reasons": [], "risk_flags": [], "classes_seen": ["docs"]},
    {"verdict": "approve", "reasons": [], "risk_flags": [], "classes_seen": [], "extra": True},
])
def test_parse_verdict_rejects_nonconforming_objects(payload):
    with pytest.raises(BuildError):
        parse_verdict(json.dumps(payload))


def test_parse_verdict_rejects_output_without_json():
    with pytest.raises(BuildError):
        parse_verdict("approve")


@pytest.mark.parametrize(("name", "allowed"), [
    ("approve", True),
    ("request_changes", False),
    ("reject", False),
])
def test_verdict_allows_merge_only_for_approval(name, allowed):
    reasons = [] if name == "approve" else ["reason"]
    verdict = parse_verdict(json.dumps({
        "verdict": name,
        "reasons": reasons,
        "risk_flags": [],
        "classes_seen": ["none"],
    }))
    assert verdict_allows_merge(verdict) is allowed


def test_builder_agent_frontmatter_and_prompt_contracts():
    agents = {}
    for name in ("build-planner", "build-reviewer"):
        text = (ROOT / ".claude" / "agents" / f"{name}.md").read_text(encoding="utf-8")
        opening, frontmatter, prompt = text.split("---", 2)
        assert opening == ""
        metadata = yaml.safe_load(frontmatter)
        assert metadata["name"]
        assert metadata["description"]
        assert metadata["tools"]
        agents[name] = (metadata, prompt)

    reviewer_metadata, reviewer_prompt = agents["build-reviewer"]
    assert not ({"Write", "Edit", "NotebookEdit"} & set(reviewer_metadata["tools"]))
    for literal in ("forbidden_paths", "personal_data", "request_changes"):
        assert literal in reviewer_prompt
    planner_prompt = agents["build-planner"][1]
    assert "Verified-missing" in planner_prompt
    assert "needs_intent" in planner_prompt
