from __future__ import annotations

import datetime as dt
import json

from project_registry.build import ProjectBuildState, append_event, save_state
from project_registry.cli import main
from project_registry.inbox import owner_inbox
from project_registry.proposals import propose_update
from project_registry.storage import load_registry, write_json

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.timezone.utc)


def ready(write_project, pid="builder", **extra):
    write_project(
        id=pid, name=pid, purpose="Ship it", desired_outcome="It ships",
        repo=f"owner/{pid}", brief={"done_criteria": ["tests pass"]},
        automation={"mode": "build"}, **extra,
    )


def kinds(result):
    return sorted(item["kind"] for item in result["items"])


def test_empty_registry_has_empty_inbox(paths):
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    assert result["items"] == []
    assert result["count"] == 0


def test_pending_proposal_is_listed_with_apply_action(paths, write_project):
    ready(write_project)
    proposal = propose_update(
        load_registry(paths), "builder", {"brief.done_criteria": ["tests pass", "docs"]},
        rationale="x", paths=paths, now=NOW,
    )
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "proposal_pending")
    assert item["project_id"] == "builder"
    assert proposal.id in item["action"]
    assert "proposal-apply" in item["action"]


def test_incomplete_brief_is_needs_intent(paths, write_project):
    write_project(id="vague", name="vague", purpose="p", repo="o/vague",
                  automation={"mode": "build"})
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "needs_intent")
    assert item["project_id"] == "vague"
    assert "registry propose vague --set brief." in item["action"]


def test_paused_project_is_listed(paths, write_project):
    ready(write_project)
    save_state(paths, {"builder": ProjectBuildState(paused_reason="consecutive_failures")})
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "paused")
    assert "consecutive_failures" in item["summary"]
    assert item["action"] == "registry build resume builder"


def test_failed_last_run_is_listed_with_digest_path(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "crashed", "detail": {"summary": "quota"}}, now=NOW)
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "run_failed")
    assert "crashed" in item["summary"] and "quota" in item["summary"]
    assert item["action"].endswith("data/build/digests/r1.md")


def test_blocked_by_policy_skip_is_listed(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "chunk_skipped", "project_id": "builder",
                         "chunk_id": "4", "reason": "blocked_by_policy",
                         "detail": {"classes": ["personal_data"]}}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "completed"}, now=NOW)
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "blocked_by_policy")
    assert "personal_data" in item["summary"]
    assert "automation.allow" in item["action"]


def test_finalize_pending_lease_is_listed(paths, write_project):
    ready(write_project)
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.build_lease_file, {"run_id": "r9", "host": "x", "project_id": "builder",
                                        "status": "finalize_pending"})
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "finalize_pending")
    assert item["action"] == "registry build finish r9 --confirm-writeback"


def test_successful_run_produces_no_items(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "merged", "project_id": "builder",
                         "chunk_id": "1", "pr_url": "https://github.com/owner/builder/pull/1",
                         "tag": "checkpoint/r1-1"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "completed"}, now=NOW)
    assert owner_inbox(paths, load_registry(paths), now=NOW)["items"] == []


def test_owner_inbox_cli_json_and_table(paths, write_project, capsys):
    ready(write_project)
    save_state(paths, {"builder": ProjectBuildState(paused_reason="revert")})
    code = main(["--root", str(paths.root), "owner-inbox", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["count"] == 1 and payload["items"][0]["kind"] == "paused"
    code = main(["--root", str(paths.root), "owner-inbox"])
    out = capsys.readouterr().out
    assert "paused" in out and "registry build resume builder" in out


def test_resumed_after_failed_run_clears_run_failed(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "crashed", "detail": {"summary": "quota"}}, now=NOW)
    append_event(paths, {"run_id": "manual-1", "host": "cli", "type": "resumed", "project_id": "builder"}, now=NOW)
    assert "run_failed" not in kinds(owner_inbox(paths, load_registry(paths), now=NOW))


def test_blocked_by_policy_action_uses_comma_list_syntax(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "chunk_skipped", "project_id": "builder",
                         "chunk_id": "4", "reason": "blocked_by_policy",
                         "detail": {"classes": ["personal_data", "ci"]}}, now=NOW)
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "blocked_by_policy")
    assert "--set automation.allow=ci,personal_data" in item["action"]
