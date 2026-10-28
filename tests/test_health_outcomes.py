"""Health follows how the last run ended; the heartbeat says why."""

from __future__ import annotations

import json

import pytest

from project_registry.cli import main
from project_registry.health import build_health, health_headline
from project_registry.notify import build_health_notification

from .test_health import event, journal, timestamp

EMPTY_INBOX = {"count": 0, "items": []}
NEXT = "2026-09-29T12:00:00+00:00"


@pytest.mark.parametrize("outcome,status", [
    ("completed", "ok"), ("roadmap_done", "ok"), ("budget_exhausted", "ok"),
    ("no_candidate", "ok"), ("needs_intent", "ok"), ("blocked_by_policy", "ok"),
    ("aborted", "warn"), ("contract_broken", "warn"), ("crashed", "warn"),
    ("baseline_red", "warn"), ("codex_unavailable", "warn"),
])
def test_status_matrix_on_schedule(tmp_path, outcome, status):
    paths = journal(tmp_path, [
        event("run_started", "2026-09-28T12:00:00"),
        event("run_finished", "2026-09-28T12:30:00", outcome=outcome,
              detail={"summary": "guard denied a piped command in chunk 4"}),
    ])
    health = build_health(paths, now=timestamp("2026-09-28T13:00:00"))
    assert health["status"] == status
    assert health["headline"].startswith(
        f"{status.upper()} · ran today · ai-engineering-markets · {outcome} · 0 merged")
    warned = health["headline"].endswith(" · guard denied a piped command in chunk 4")
    assert warned == (status == "warn")
    assert health_headline(json.loads(json.dumps(health))) == health["headline"]


def test_kayak_contract_broken_run_is_not_ok(tmp_path):
    # 2026-10-16 on kayak the heartbeat read "OK · ran today · ... contract_broken".
    paths = journal(tmp_path, [
        event("run_started", "2026-10-16T12:04:18", project_id="interview-prep-prod"),
        event("run_finished", "2026-10-16T12:16:00", project_id="interview-prep-prod",
              outcome="contract_broken"),
    ])
    health = build_health(paths, now=timestamp("2026-10-16T13:00:00"))
    assert health["status"] == "warn"
    assert health["headline"] == "WARN · ran today · interview-prep-prod · contract_broken · 0 merged"


def test_missed_windows_outrank_warn(tmp_path):
    paths = journal(tmp_path, [
        event("run_started", "2026-09-24T12:00:00"),
        event("run_finished", "2026-09-24T12:30:00", outcome="aborted"),
    ])
    health = build_health(paths, now=timestamp("2026-09-28T15:00:00"))
    assert health["status"] == "stalled"
    assert health["headline"].startswith("STALLED · no run since Sep 24")


LIVE_LEASE = {"run_id": "r1", "project_id": "ai-engineering-markets",
              "started_at": "2026-09-28T12:00:00Z", "ttl_seconds": 10800}


def test_unfinished_run_warns_only_without_live_lease(tmp_path):
    paths = journal(tmp_path, [event("run_started", "2026-09-28T12:00:00")])
    now = timestamp("2026-09-28T13:00:00")
    health = build_health(paths, now=now)
    assert health["status"] == "warn"
    assert "crashed?" in health["headline"]
    paths.build_lease_file.write_text(json.dumps(LIVE_LEASE))
    assert build_health(paths, now=now)["status"] == "ok"


def test_expired_lease_does_not_hide_crashed_run(tmp_path):
    # A crashed run leaves its lease behind; after the TTL it explains nothing.
    paths = journal(tmp_path, [event("run_started", "2026-09-28T12:00:00")])
    paths.build_lease_file.write_text(json.dumps(LIVE_LEASE))
    health = build_health(paths, now=timestamp("2026-09-28T16:00:00"))
    assert health["status"] == "warn"
    assert health["headline"].startswith("WARN · ran today · ai-engineering-markets · crashed?")
    assert health["lease"] == {"run_id": "r1", "project_id": "ai-engineering-markets"}


@pytest.mark.parametrize("raw", [
    "{}", "not json", "[]",
    json.dumps({**LIVE_LEASE, "run_id": "someone-else"}),
    json.dumps({k: v for k, v in LIVE_LEASE.items() if k != "ttl_seconds"}),
    json.dumps({**LIVE_LEASE, "started_at": "garbage"}),
])
def test_malformed_or_foreign_lease_does_not_hide_unfinished_run(tmp_path, raw):
    paths = journal(tmp_path, [event("run_started", "2026-09-28T12:00:00")])
    paths.build_lease_file.write_text(raw)
    assert build_health(paths, now=timestamp("2026-09-28T13:00:00"))["status"] == "warn"


def test_warn_notification_carries_exit_code_and_tail():
    notification = build_health_notification(
        {"status": "warn", "headline": "WARN · ran today · p · aborted · 0 merged",
         "next_expected": NEXT},
        EMPTY_INBOX, exit_code=0,
        log_tail=["one", "", "two", "three", "four", "five", "six   spaced"],
    )
    assert notification["priority"] == 4
    assert notification["tags"] == ["warning"]
    assert notification["body"].split("\n") == [
        "next expected: Tue Sep 29 12:00 UTC", "builder exit: 0", "log tail:",
        "  two", "  three", "  four", "  five", "  six spaced", "Nothing needs you.",
    ]


def test_ok_with_nonzero_exit_is_raised_and_shows_tail():
    notification = build_health_notification(
        {"status": "ok", "headline": "OK · ran today", "next_expected": NEXT},
        EMPTY_INBOX, exit_code=1,
        log_tail=["run-build: git pull or evidence refresh failed; skipping the run"],
    )
    assert notification["priority"] == 4
    assert notification["tags"] == ["warning"]
    assert "builder exit: 1" in notification["body"]
    assert "skipping the run" in notification["body"]


def test_clean_ok_hides_tail():
    notification = build_health_notification(
        {"status": "ok", "headline": "OK · ran today", "next_expected": NEXT},
        EMPTY_INBOX, exit_code=0, log_tail=["all done"],
    )
    assert notification["priority"] == 3
    assert notification["tags"] == ["white_check_mark"]
    assert notification["body"] == (
        "next expected: Tue Sep 29 12:00 UTC\nbuilder exit: 0\nNothing needs you.")


def test_notify_health_cli_reads_log_tail(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REGISTRY_NTFY_TOPIC", raising=False)
    journal(tmp_path, [
        event("run_started", "2026-09-28T12:00:00"),
        event("run_finished", "2026-09-28T12:30:00", outcome="aborted",
              detail={"summary": "guard denied a piped command"}),
    ])
    tail = tmp_path / "run.log.tail"
    tail.write_text("line 1\nguard denied: pipe into python3\n")
    assert main(["--root", str(tmp_path), "notify", "--health", "--dry-run", "--json",
                 "--exit-code", "3", "--log-tail", str(tail)]) == 0
    message = json.loads(capsys.readouterr().out)["message"]
    assert "builder exit: 3" in message
    assert "  guard denied: pipe into python3" in message


def test_notify_health_cli_missing_log_tail(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("REGISTRY_NTFY_TOPIC", raising=False)
    missing = tmp_path / "gone.tail"
    assert main(["--root", str(tmp_path), "notify", "--health", "--dry-run", "--json",
                 "--exit-code", "1", "--log-tail", str(missing)]) == 0
    message = json.loads(capsys.readouterr().out)["message"]
    assert f"(log tail unreadable: {missing})" in message


def test_warn_headline_keeps_later_refusal(tmp_path):
    paths = journal(tmp_path, [
        event("run_started", "2026-09-28T12:00:00"),
        event("run_finished", "2026-09-28T12:30:00", outcome="aborted",
              detail={"summary": "guard denied"}),
        event("run_finished", "2026-09-28T13:00:00", run_id="refused",
              outcome="registry_dirty", detail={"reason": "registry_dirty"}),
    ])
    health = build_health(paths, now=timestamp("2026-09-28T13:30:00"))
    assert health["status"] == "warn"
    assert health["last_run"]["summary"] == "guard denied"
    assert health["headline"] == (
        "WARN · ran today · ai-engineering-markets · aborted · 0 merged · guard denied"
        " · last attempt Sep 28: registry_dirty")


def test_log_tail_lines_are_truncated_to_160_chars():
    notification = build_health_notification(
        {"status": "ok", "headline": "OK · ran today", "next_expected": NEXT},
        EMPTY_INBOX, exit_code=2, log_tail=["x" * 400],
    )
    assert f"  {'x' * 160}" in notification["body"].split("\n")
    assert "x" * 161 not in notification["body"]


def test_heartbeat_without_exit_code_is_unchanged():
    notification = build_health_notification(
        {"status": "ok", "headline": "OK · ran today", "next_expected": NEXT}, EMPTY_INBOX)
    assert notification["body"] == "next expected: Tue Sep 29 12:00 UTC\nNothing needs you."
    assert (notification["priority"], notification["tags"]) == (3, ["white_check_mark"])
