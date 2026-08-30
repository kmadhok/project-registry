"""Atomic leasing and autonomous build run lifecycle."""

from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from project_registry.build import (
    BuildError,
    ProjectBuildState,
    append_event,
    apply_run_to_state,
    begin_run,
    build_env,
    confirm_writeback,
    finish_run,
    load_state,
    read_events,
    reconcile,
    reconcile_done,
    record_event,
    registry_checkout_status,
    save_state,
)
from project_registry.github.snapshot import Snapshot
from project_registry.storage import load_registry, read_json, write_json


NOW = dt.datetime(2026, 8, 22, 12, 0, tzinfo=dt.timezone.utc)


def ready_project(write_project, project_id="builder"):
    write_project(
        id=project_id,
        name="Builder",
        purpose="Ship it",
        desired_outcome="It ships",
        repo="owner/builder",
        brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build", "allow": ["ci"]},
    )


def test_digest_lists_owner_inbox(paths, write_project):
    ready_project(write_project)
    write_project(id="vague", name="Vague", purpose="p", repo="o/vague",
                  automation={"mode": "build"})
    start(paths, run_id="run")
    result = finish_run(paths, "run", outcome="completed", now=NOW,
                        registry=load_registry(paths), snapshot=Snapshot())
    digest = open(result["digest_path"], encoding="utf-8").read()
    assert "## Needs the owner" in digest
    assert "needs_intent" in digest and "vague" in digest


def start(paths, *, now=NOW, project_id=None, force_named=False, run_id=None):
    return begin_run(
        paths,
        host="mac",
        project_id=project_id,
        force_named=force_named,
        now=now,
        run_id=run_id,
        registry=load_registry(paths),
        snapshot=Snapshot(),
    )


def lifecycle_event(event_type, *, run_id="run-1", ts=NOW.isoformat(), **values):
    return {"type": event_type, "run_id": run_id, "ts": ts, **values}


@pytest.mark.parametrize("classes", [
    ["generated_data", "dependencies"],
    "generated_data, dependencies",
])
def test_blocked_policy_finish_records_waiting_on(classes):
    finish_ts = "2026-08-22T12:05:00+00:00"
    state = apply_run_to_state(ProjectBuildState(), [
        lifecycle_event(
            "chunk_skipped", reason="blocked_by_policy",
            detail={"classes": classes},
        ),
        lifecycle_event("run_finished", outcome="blocked_by_policy", ts=finish_ts),
    ])

    assert state.waiting_on == {
        "kind": "blocked_by_policy",
        "classes": ["dependencies", "generated_data"],
        "since": finish_ts,
        "run_id": "run-1",
    }


def test_needs_intent_finish_records_waiting_on_without_classes():
    state = apply_run_to_state(ProjectBuildState(), [
        lifecycle_event("run_finished", outcome="needs_intent"),
    ])

    assert state.waiting_on == {
        "kind": "needs_intent",
        "classes": [],
        "since": NOW.isoformat(),
        "run_id": "run-1",
    }


def test_later_completed_run_clears_waiting_on():
    waiting = ProjectBuildState(waiting_on={
        "kind": "needs_intent", "classes": [], "since": NOW.isoformat(),
        "run_id": "run-1",
    })
    state = apply_run_to_state(waiting, [
        lifecycle_event("run_finished", run_id="run-2", outcome="completed"),
    ])

    assert state.waiting_on is None


def test_waiting_on_state_validation_and_round_trip(paths):
    assert ProjectBuildState.from_dict({}).waiting_on is None
    with pytest.raises(BuildError, match="waiting_on kind"):
        ProjectBuildState.from_dict({
            "waiting_on": {
                "kind": "bogus", "classes": [], "since": NOW.isoformat(),
                "run_id": "run-1",
            },
        })

    waiting = ProjectBuildState(waiting_on={
        "kind": "blocked_by_policy", "classes": ["dependencies"],
        "since": NOW.isoformat(), "run_id": "run-1",
    })
    save_state(paths, {"builder": waiting})
    assert load_state(paths)["builder"] == waiting


def test_begin_run_excludes_waiting_owner_unless_named_and_forced(
    paths, write_project
):
    ready_project(write_project)
    waiting = ProjectBuildState(waiting_on={
        "kind": "blocked_by_policy", "classes": ["generated_data"],
        "since": NOW.isoformat(), "run_id": "blocked-run",
    })
    save_state(paths, {"builder": waiting})

    bare = start(paths, run_id="bare")
    assert bare["outcome"] == "no_candidate"
    assert bare["by_state"]["waiting_owner"] == 1

    named = start(paths, project_id="builder", run_id="named")
    assert named["outcome"] == "no_candidate"
    assert "waiting on owner" in named["reason"]

    forced = start(
        paths, project_id="builder", force_named=True, run_id="forced"
    )
    assert forced["lease"]["project_id"] == "builder"


def test_second_begin_reports_lease_held_without_changing_lease(paths, write_project):
    ready_project(write_project)
    first = start(paths, run_id="first")
    lease_before = paths.build_lease_file.read_bytes()
    second = start(paths, run_id="second")
    assert second["outcome"] == "lease_held"
    assert second["lease"]["run_id"] == first["run_id"]
    assert paths.build_lease_file.read_bytes() == lease_before
    events, _ = read_events(paths)
    assert any(
        item["run_id"] == "second" and item.get("outcome") == "lease_held"
        for item in events
    )


def test_expired_run_reconciles_then_next_begin_can_start(paths, write_project):
    ready_project(write_project)
    first = start(paths, run_id="dead")
    record_event(paths, first["run_id"], {
        "type": "pr_opened", "chunk_id": "c1",
        "pr_url": "https://github.com/owner/builder/pull/7",
        "detail": {"pr_number": 7, "branch": "push/dead-1-fix"},
    })
    lease = read_json(paths.build_lease_file)
    lease["ttl_seconds"] = 1
    write_json(paths.build_lease_file, lease)

    result = start(paths, now=NOW + dt.timedelta(seconds=2), run_id="next")
    assert result["outcome"] == "reconciling"
    assert result["actions"] == [
        {"action": "close_pr", "repo": "owner/builder", "number": 7},
        {"action": "delete_branch", "repo": "owner/builder", "branch": "push/dead-1-fix"},
    ]
    events, _ = read_events(paths)
    assert [(e["type"], e.get("outcome")) for e in events[-2:]] == [
        ("crashed", None), ("run_finished", "crashed")
    ]
    assert reconcile_done(paths)["reconciled"] is True
    assert start(paths, run_id="next")["run_id"] == "next"


def test_checkout_preflight_dirty_ignored_paths_and_branch(paths, write_project):
    ready_project(write_project)
    subprocess.run(["git", "init", "-b", "main"], cwd=paths.root, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=paths.root)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=paths.root)
    subprocess.run(["git", "add", "."], cwd=paths.root, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=paths.root, check=True,
                   capture_output=True)
    (paths.root / "registry" / "dirty.txt").write_text("dirty", encoding="utf-8")
    assert start(paths, run_id="dirty")["outcome"] == "registry_dirty"
    assert not paths.build_lease_file.exists()
    (paths.root / "registry" / "dirty.txt").unlink()
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    (paths.build_dir / "machine.txt").write_text("allowed", encoding="utf-8")
    assert registry_checkout_status(paths.root)["clean"] is True
    allowed = start(paths, run_id="allowed")
    assert allowed["run_id"] == "allowed"
    finish_run(paths, "allowed", outcome="completed", now=NOW)
    confirm_writeback(paths, "allowed", now=NOW)
    subprocess.run(["git", "switch", "-c", "topic"], cwd=paths.root, check=True,
                   capture_output=True)
    assert start(paths, run_id="branch")["outcome"] == "registry_dirty"


def test_checkout_preflight_ignores_builder_filed_proposals(paths, write_project):
    # Run e7eb4af7 (2026-08-26) filed a needs_intent proposal that was left
    # uncommitted on the build host; the next start must not park on it.
    ready_project(write_project)
    subprocess.run(["git", "init", "-b", "main"], cwd=paths.root, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=paths.root)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=paths.root)
    subprocess.run(["git", "add", "."], cwd=paths.root, check=True)
    subprocess.run(["git", "commit", "-m", "seed"], cwd=paths.root, check=True,
                   capture_output=True)
    proposals = paths.root / "data" / "proposals"
    proposals.mkdir(parents=True, exist_ok=True)
    (proposals / "builder-20260826122745.json").write_text("{}\n", encoding="utf-8")
    status = registry_checkout_status(paths.root)
    assert status["clean"] is True and status["dirty_paths"] == []
    assert start(paths, run_id="proposal-ok")["run_id"] == "proposal-ok"


def test_finalize_pending_blocks_begin_until_writeback_confirmed(paths, write_project):
    ready_project(write_project)
    start(paths, run_id="run")
    finish_run(paths, "run", outcome="completed", now=NOW)
    refused = start(paths, run_id="next")
    assert refused["outcome"] == "finalize_pending"
    assert refused["actions"] == [{"action": "confirm_writeback", "run_id": "run"}]
    assert reconcile(paths)["actions"] == refused["actions"]
    assert confirm_writeback(paths, "run", now=NOW)["writeback_confirmed"] is True
    assert not paths.build_lease_file.exists()
    events, _ = read_events(paths)
    assert events[-1]["type"] == "writeback_confirmed"
    assert start(paths, run_id="next")["run_id"] == "next"


def test_finish_is_idempotent_for_state_application(paths, write_project):
    ready_project(write_project)
    start(paths, run_id="run")
    record_event(paths, "run", {
        "type": "merged", "chunk_id": "c1", "tag": "checkpoint/run-1",
    })
    first = finish_run(paths, "run", outcome="aborted", now=NOW)
    second = finish_run(paths, "run", outcome="aborted", now=NOW)
    assert first["state_after"]["chunks_merged_total"] == 1
    assert second["state_after"]["chunks_merged_total"] == 1
    assert first["state_after"]["consecutive_failures"] == 1
    assert second["state_after"]["consecutive_failures"] == 1


def add_guard_denial(paths, run_id="run"):
    append_event(paths, {
        "run_id": run_id,
        "host": "mac",
        "type": "guard_denied",
        "project_id": "builder",
        "reason": "non_push_branch",
        "detail": {"command": "git push origin main"},
    }, now=NOW)


def test_guard_denial_prevents_completed_finish(paths, write_project):
    ready_project(write_project)
    start(paths, run_id="run")
    add_guard_denial(paths)

    with pytest.raises(
        BuildError,
        match="run run has 1 guard denial\\(s\\); finish with --outcome aborted",
    ):
        finish_run(paths, "run", outcome="completed", now=NOW)


def test_guard_denial_can_finish_aborted_with_digest(paths, write_project):
    ready_project(write_project)
    start(paths, run_id="run")
    add_guard_denial(paths)

    result = finish_run(paths, "run", outcome="aborted", now=NOW)

    lease = read_json(paths.build_lease_file)
    assert lease["status"] == "finalize_pending"
    digest = open(result["digest_path"], encoding="utf-8").read()
    assert "## Guard denials" in digest
    assert "- non_push_branch" in digest


def test_blocked_policy_finish_requires_recorded_blocked_items(
    paths, write_project
):
    ready_project(write_project)
    start(paths, run_id="run")

    with pytest.raises(BuildError) as exc_info:
        finish_run(paths, "run", outcome="blocked_by_policy", now=NOW)

    assert "run run" in str(exc_info.value)
    assert "chunk_skipped --chunk-id" in str(exc_info.value)

    append_event(paths, {
        "run_id": "run",
        "host": "mac",
        "type": "chunk_skipped",
        "project_id": "builder",
        "chunk_id": "4",
        "reason": "blocked_by_policy",
        "detail": {"classes": "generated_data"},
    }, now=NOW)
    result = finish_run(paths, "run", outcome="blocked_by_policy", now=NOW)

    assert result["state_after"]["waiting_on"]["classes"] == ["generated_data"]


def test_run_without_guard_denials_can_finish_completed(paths, write_project):
    ready_project(write_project)
    start(paths, run_id="run")

    finish_run(paths, "run", outcome="completed", now=NOW)

    assert read_json(paths.build_lease_file)["finalize"]["outcome"] == "completed"


def test_named_needs_intent_refused_unless_forced(paths, write_project):
    write_project(
        id="vague", name="Vague", repo="owner/vague",
        automation={"mode": "build"},
    )
    refused = start(paths, project_id="vague", run_id="refused")
    assert refused["outcome"] == "no_candidate"
    assert "vague is needs_intent" in refused["reason"]
    forced = start(
        paths, project_id="vague", force_named=True, run_id="forced"
    )
    assert forced["outcome"] == "forced_named"
    assert forced["candidate"]["state"] == "needs_intent"


def test_event_counters_and_finish_digest(paths, write_project):
    ready_project(write_project)
    result = start(paths, run_id="run")
    with pytest.raises(BuildError):
        record_event(paths, "wrong", {"type": "chunk_started", "chunk_id": "c1"})
    record_event(paths, "run", {
        "type": "pr_opened", "chunk_id": "c1",
        "pr_url": "https://github.com/owner/builder/pull/9",
        "detail": {"pr_number": 9, "branch": "push/run-1-work"},
    })
    record_event(paths, "run", {"type": "verify_passed", "chunk_id": "c1"})
    record_event(paths, "run", {
        "type": "review_verdict", "chunk_id": "c1",
        "detail": {"verdict": "approve"},
    })
    recorded = record_event(paths, "run", {
        "type": "merged", "chunk_id": "c1", "tag": "checkpoint/run-1",
        "detail": {"merge_sha": "abc123"},
    })
    assert recorded["lease_summary"]["prs_open"] == 0
    assert recorded["lease_summary"]["merges"] == 1
    lease = read_json(paths.build_lease_file)
    assert lease["chunks"]["c1"]["verify"] == "passed"
    assert lease["chunks"]["c1"]["verdict"] == "approve"
    finished = finish_run(paths, result["run_id"], outcome="completed", now=NOW)
    assert finished["state_after"]["chunks_merged_total"] == 1
    digest = open(finished["digest_path"], encoding="utf-8").read()
    assert "https://github.com/owner/builder/pull/9" in digest
    assert "git revert abc123" in digest
    lease = read_json(paths.build_lease_file)
    assert lease["status"] == "finalize_pending"
    assert lease["finalize"]["outcome"] == "completed"
    assert lease["finalize"]["digest_path"] == finished["digest_path"]


def test_reconcile_lists_stale_builder_pr_without_mutation(paths, write_project):
    ready_project(write_project)
    start(paths, run_id="dead")
    lease = read_json(paths.build_lease_file)
    lease["ttl_seconds"] = 1
    write_json(paths.build_lease_file, lease)
    calls = []

    def fake(repo):
        calls.append(repo)
        return [{"number": 12, "headRefName": "push/old-1-x"}]

    result = reconcile(
        paths, now=NOW + dt.timedelta(seconds=2), list_open_builder_prs=fake
    )
    assert calls == ["owner/builder"]
    assert result["actions"] == [
        {"action": "close_pr", "repo": "owner/builder", "number": 12},
        {"action": "delete_branch", "repo": "owner/builder", "branch": "push/old-1-x"},
    ]


def test_build_env_detects_overridden_hosts(paths, tmp_path, monkeypatch):
    mac = tmp_path / "mac"
    pc = tmp_path / "pc"
    monkeypatch.setenv("BUILD_ENV_MAC_ROOT", str(mac))
    monkeypatch.setenv("BUILD_ENV_PC_ROOT", str(pc))
    assert build_env(paths.root)["host_kind"] == "unknown"
    pc.mkdir()
    assert build_env(paths.root)["host_kind"] == "pc"
    mac.mkdir()
    assert build_env(paths.root)["host_kind"] == "mac"

    monkeypatch.setenv("BUILD_HOST_LABEL", "buildbox")
    assert build_env(paths.root)["host"] == "buildbox"

    monkeypatch.delenv("BUILD_HOST_LABEL")
    monkeypatch.setattr("project_registry.build_runs.platform.system", lambda: "Linux")
    monkeypatch.setattr(
        "project_registry.build_runs.socket.gethostname",
        lambda: "Instance-20250830.local",
    )
    assert build_env(paths.root)["host"] == "instance-20250830"


def test_generated_run_id_sanitizes_host(paths, write_project):
    ready_project(write_project)
    result = begin_run(
        paths,
        host="Build_Box.example!",
        now=NOW,
        registry=load_registry(paths),
        snapshot=Snapshot(),
    )
    assert re.fullmatch(r"[A-Za-z0-9-]+", result["run_id"])


def test_atomic_begin_race_issues_exactly_one_lease(paths, write_project):
    ready_project(write_project)
    registry = load_registry(paths)

    def race(run_id):
        return begin_run(
            paths, host="mac", now=NOW, run_id=run_id,
            registry=registry, snapshot=Snapshot(),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(race, ["one", "two"]))
    assert sum("run_id" in result for result in results) == 1
    assert sum(result.get("outcome") == "lease_held" for result in results) == 1


def test_stop_file_journals_stopped(paths, write_project):
    ready_project(write_project)
    paths.build_stop_file.parent.mkdir(parents=True)
    paths.build_stop_file.write_text("stop\n", encoding="utf-8")
    assert start(paths, run_id="stopped")["outcome"] == "stopped"
    assert not paths.build_lease_file.exists()
    events, _ = read_events(paths)
    assert events[-1]["outcome"] == "stopped"
