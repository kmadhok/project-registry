"""CLI behaviour, including exit codes and the dashboard (US-001, US-009, US-012)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from project_registry import cli as cli_module
from project_registry.build import ProjectBuildState, append_event, save_state
from project_registry.cli import main
from project_registry.dashboard import render_dashboard
from project_registry.github.sync import save_snapshot
from project_registry.storage import load_registry

from .conftest import NOW, make_branch, make_issue, make_pr, make_repo_state, make_snapshot


def run(paths, *args, capsys=None):
    code = main(["--root", str(paths.root), *args])
    out = capsys.readouterr().out if capsys else ""
    return code, out


def test_validate_exits_nonzero_on_errors(paths, write_project, capsys):
    write_project(id="p")  # no purpose
    code, out = run(paths, "validate", capsys=capsys)
    assert code == 1
    assert "purpose_missing" in out


def test_validate_exits_zero_when_clean(paths, write_project, capsys):
    write_project(id="p", purpose="why")
    code, out = run(paths, "validate", capsys=capsys)
    assert code == 0
    assert "OK" in out


def test_validate_contract_accepts_the_repository_contract(paths, capsys):
    contract = Path(__file__).parents[1] / ".project-meta.yaml"
    code, out = run(
        paths,
        "validate-contract",
        str(contract),
        "--project-id",
        "project-registry",
        "--json",
        capsys=capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["ok"] is True
    assert payload["contract"]["schema"] == 1


def test_validate_spec_exit_codes(paths, write_project, tmp_path, capsys):
    write_project(id="target", automation={"allow": []})
    ready = tmp_path / "ready.md"
    ready.write_text(
        "## Remaining work\n"
        "- [ ] Add a bounded test\n"
        "      Acceptance: the test passes\n"
        "      Tests: tests/test_one.py\n"
        "      Size: S\n"
        "      Classes: none\n"
        "      Verified-missing: the test file is absent\n"
        "      Criteria: 1\n",
        encoding="utf-8",
    )
    code, out = run(paths, "validate-spec", "target", str(ready), "--json", capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["next_ready_index"] == 1
    assert payload["items"][0]["criteria"] == [1]

    blocked = tmp_path / "blocked.md"
    blocked.write_text("## Remaining work\n- [ ] Legacy item\n", encoding="utf-8")
    code, out = run(paths, "validate-spec", "target", str(blocked), "--json", capsys=capsys)
    assert code == 1
    assert json.loads(out)["next_ready_index"] is None

    done = tmp_path / "done.md"
    done.write_text("## Remaining work\n- [x] Finished\n", encoding="utf-8")
    code, _ = run(paths, "validate-spec", "target", str(done), capsys=capsys)
    assert code == 0


def test_outcome_status_text_json_and_unknown_project(paths, write_project, tmp_path, capsys):
    write_project(id="target", brief={"done_criteria": ["Ship the result"]})
    spec = tmp_path / "SPEC.md"
    spec.write_text("## Remaining work\n- [x] Shipped\n      Criteria: 1\n", encoding="utf-8")

    code, out = run(paths, "outcome-status", "target", str(spec), capsys=capsys)
    assert code == 0
    assert "STATUS" in out and "ITEMS" in out and "CRITERION" in out
    assert "criteria: 1/1 met" in out

    code, out = run(paths, "outcome-status", "target", str(spec), "--json", capsys=capsys)
    assert code == 0
    assert json.loads(out)["summary"]["met"] == 1
    code, _ = run(paths, "outcome-status", "missing", str(spec), capsys=capsys)
    assert code != 0


def test_json_output_is_machine_readable(paths, write_project, capsys):
    write_project(id="p", purpose="why", lifecycle="maintained")
    code, out = run(paths, "list", "--json", capsys=capsys)
    assert code == 0
    assert json.loads(out)[0]["id"] == "p"


def test_brief_status_table_and_json(paths, write_project, capsys):
    write_project(
        id="p", name="Project P", purpose="why", desired_outcome="result",
        repo="owner/p", lifecycle="now", automation={"mode": "build"},
        brief={"done_criteria": ["Tests pass"], "reviewed": "2026-08-01"},
    )
    code, out = run(paths, "brief-status", capsys=capsys)
    assert code == 0
    assert "PROJECT" in out
    assert "AUTOMATION" in out
    assert "Project P" in out

    code, out = run(paths, "brief-status", "--json", capsys=capsys)
    assert code == 0
    payload = json.loads(out)
    assert payload["projects"][0]["project_id"] == "p"
    assert payload["summary"]["complete"] == 1


def test_list_filters(paths, write_project, capsys):
    write_project(id="a", purpose="p", lifecycle="reference")
    write_project(id="b", purpose="p", lifecycle="maintained")
    _, out = run(paths, "list", "--lifecycle", "reference", "--json", capsys=capsys)
    assert [r["id"] for r in json.loads(out)] == ["a"]


def test_show_renders_a_project(paths, write_project, capsys):
    write_project(id="p", name="Thing", purpose="why", active=True,
                  next_action={"description": "Decide the format", "reviewed": "2026-07-01"})
    code, out = run(paths, "show", "p", capsys=capsys)
    assert code == 0
    assert "Thing" in out
    assert "Decide the format" in out


def test_show_renders_branch_summary_and_partial_warning(paths, write_project, capsys):
    write_project(id="p", name="Thing", purpose="why", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state(
        "owner/repo",
        branches=[
            make_branch("old"),
            make_branch("reviewed", open_pr_numbers=[3]),
        ],
        branches_fetched=True,
    )), paths)
    _, out = run(paths, "show", "p", capsys=capsys)
    assert "2 branches (1 stale, 1 open-PR heads)" in out

    save_snapshot(make_snapshot(make_repo_state(
        "owner/repo",
        branches=[make_branch("partial")],
        branches_fetched=False,
        branches_partial=True,
        branches_error="branch list has 1251 entries; truncated after 1000",
    )), paths)
    _, out = run(paths, "show", "p", capsys=capsys)
    assert "(branches: branch list has 1251 entries; truncated after 1000)" in out
    assert "1 branches" not in out


def test_unknown_project_exits_with_an_error(paths, capsys):
    code, _ = run(paths, "show", "ghost", capsys=capsys)
    assert code == 2


def test_next_actions_missing_flag(paths, write_project, capsys):
    write_project(id="p", purpose="why", active=True)
    _, out = run(paths, "next-actions", "--missing", "--json", capsys=capsys)
    assert json.loads(out)[0]["project_id"] == "p"


def test_work_queue_shows_both_priority_columns(paths, write_project, capsys):
    write_project(id="p", purpose="why", active=True, priority="high",
                  next_action={"description": "Decide the format", "reviewed": "2026-07-01"})
    _, out = run(paths, "work-queue", capsys=capsys)
    assert "HUMAN PRIORITY" in out
    assert "GH URGENCY" in out
    assert "why:" in out


def test_work_queue_labels_unrecorded_priority(paths, write_project, capsys):
    write_project(id="p", purpose="why", active=True,
                  next_action={"description": "Decide the format", "reviewed": "2026-07-01"})
    _, out = run(paths, "work-queue", capsys=capsys)
    assert "not recorded" in out


def test_prs_command_reports_evidence_age(paths, write_project, capsys):
    write_project(id="p", purpose="why", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(repo="owner/repo", number=2)
    ])), paths)
    code, out = run(paths, "prs", capsys=capsys)
    assert code == 0
    assert "GitHub evidence fetched" in out
    assert "owner/repo#2" in out


def test_prs_draft_filter(paths, write_project, capsys):
    write_project(id="p", purpose="why", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(repo="owner/repo", number=1, draft=True),
        make_pr(repo="owner/repo", number=2, draft=False),
    ])), paths)
    _, out = run(paths, "prs", "--draft", "--json", capsys=capsys)
    assert [r["number"] for r in json.loads(out)["pull_requests"]] == [1]


def test_issues_command(paths, write_project, capsys):
    write_project(id="p", purpose="why", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state("owner/repo", issues=[
        make_issue(repo="owner/repo", number=5, labels=["blocked"])
    ])), paths)
    _, out = run(paths, "issues", "--json", capsys=capsys)
    assert json.loads(out)["issues"][0]["number"] == 5


def test_attention_command_explains_each_signal(paths, write_project, capsys):
    write_project(id="p", purpose="why", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(repo="owner/repo", number=1, ci_state="failure")
    ])), paths)
    _, out = run(paths, "attention", capsys=capsys)
    assert "rule: ci_failing" in out
    assert "url:  https://github.com/" in out


def test_attention_json_preserves_repo_scope_without_none_reference(
    paths, write_project, capsys
):
    write_project(id="p", purpose="why", repo="owner/repo")
    save_snapshot(make_snapshot(make_repo_state(
        "owner/repo",
        branches=[make_branch("old")],
        branches_fetched=True,
    )), paths)
    _, out = run(paths, "attention", "--json", capsys=capsys)
    items = json.loads(out)
    branch = next(item for item in items if item["rule_id"] == "stale_branches")
    assert branch["number"] is None
    assert branch["kind"] == "branch"
    assert "#None" not in out


def test_sync_no_branches_never_calls_the_branch_client(
    paths, write_project, capsys, monkeypatch
):
    write_project(id="p", purpose="why", repo="owner/repo")

    class RecordingClient:
        instances = []

        def __init__(self):
            self.branch_calls = 0
            self.instances.append(self)

        def get_repo(self, full_name):
            return {
                "full_name": full_name,
                "default_branch": "main",
                "html_url": f"https://github.com/{full_name}",
            }

        def list_open_pulls(self, full_name):
            return []

        def list_open_issues(self, full_name):
            return []

        def list_branch_nodes(self, full_name):
            self.branch_calls += 1
            return [], False, 0

    monkeypatch.setattr(cli_module, "GitHubClient", RecordingClient)
    code, _ = run(paths, "sync", "--no-branches", capsys=capsys)
    assert code == 0
    assert RecordingClient.instances[0].branch_calls == 0


def test_mismatches_exit_nonzero_on_errors(paths, write_project, capsys):
    write_project(id="p", purpose="why", repo="owner/gone")
    save_snapshot(make_snapshot(make_repo_state("owner/other")), paths)
    code, out = run(paths, "mismatches", capsys=capsys)
    assert code == 1
    assert "repo_missing" in out
    assert "Nothing was changed automatically" in out


def test_rules_command_lists_the_catalogue(paths, capsys):
    code, out = run(paths, "rules", capsys=capsys)
    assert code == 0
    assert "ci_failing" in out
    assert "stale_branches" in out
    assert "archived_but_github_active" in out


def test_dashboard_writes_a_file(paths, write_project, capsys):
    write_project(id="p", purpose="why", lifecycle="maintained", active=True,
                  last_reviewed="2026-07-20",
                  next_action={"description": "Renew the certificate", "reviewed": "2026-07-20"})
    code, out = run(paths, "dashboard", capsys=capsys)
    assert code == 0
    assert paths.dashboard_file.exists()
    assert "# Portfolio Dashboard" in paths.dashboard_file.read_text(encoding="utf-8")


def test_portfolio_markdown_to_stdout(paths, write_project, capsys):
    write_project(id="p", name="Thing", purpose="why", public=True,
                  descriptions={"public_safe": "a safe summary"})
    code, out = run(paths, "portfolio", "-o", "-", capsys=capsys)
    assert code == 0
    assert "## Thing" in out
    assert "a safe summary" in out


def test_propose_then_apply_round_trip(paths, write_project, capsys):
    write_project(id="p", purpose="original")

    _, out = run(paths, "propose", "p", "--set", "purpose=updated", "--json", capsys=capsys)
    proposal_id = json.loads(out)["id"]
    assert load_registry(paths).require("p").purpose == "original"

    code, out = run(paths, "proposal-apply", proposal_id, capsys=capsys)
    assert code == 2, "apply without --approve must be refused"
    assert load_registry(paths).require("p").purpose == "original"

    code, _ = run(paths, "proposal-apply", proposal_id, "--approve", capsys=capsys)
    assert code == 0
    assert load_registry(paths).require("p").purpose == "updated"


def test_propose_prints_the_diff_and_says_nothing_changed(paths, write_project, capsys):
    write_project(id="p", purpose="original")
    _, out = run(paths, "propose", "p", "--set", "purpose=updated", capsys=capsys)
    assert "before: 'original'" in out
    assert "is unchanged on disk" in out


def test_record_review_updates_the_date(paths, write_project, capsys):
    write_project(id="p", purpose="why", last_reviewed="2026-01-01")
    code, _ = run(paths, "record-review", "p", "--date", "2026-07-25", capsys=capsys)
    assert code == 0
    assert str(load_registry(paths).require("p").last_reviewed) == "2026-07-25"


def test_audit_log_command(paths, write_project, capsys):
    write_project(id="p", purpose="why", last_reviewed="2026-01-01")
    run(paths, "record-review", "p", "--date", "2026-07-25", capsys=capsys)
    _, out = run(paths, "audit", capsys=capsys)
    assert "apply_proposal" in out


def test_bad_set_syntax_is_reported(paths, write_project, capsys):
    write_project(id="p", purpose="why")
    code, _ = run(paths, "propose", "p", "--set", "nonsense", capsys=capsys)
    assert code == 2


def test_no_command_prints_help(paths, capsys):
    code, out = run(paths, capsys=capsys)
    assert code == 0
    assert "usage:" in out


def test_build_report_and_resume_commands(paths, capsys):
    append_event(paths, {
        "run_id": "run-1", "host": "mac", "type": "run_finished",
        "project_id": "alpha", "outcome": "completed",
    }, now=NOW)
    code, out = run(paths, "build-report", "--json", capsys=capsys)
    assert code == 0
    assert json.loads(out)["runs"]["total"] == 1

    code, out = run(paths, "build", "resume", "alpha", "--json", capsys=capsys)
    assert code == 0
    state = json.loads(out)
    assert state["project_id"] == "alpha"
    assert state["paused_reason"] is None


def test_notify_inbox_dry_run_needs_no_digest(paths, capsys):
    code, out = run(paths, "notify", "--inbox", "--dry-run", capsys=capsys)
    assert code == 0
    assert out.splitlines()[0] == "Nothing needed"


def test_notify_requires_exactly_one_source(paths):
    with pytest.raises(SystemExit) as missing:
        main(["--root", str(paths.root), "notify"])
    assert missing.value.code != 0

    with pytest.raises(SystemExit) as conflicting:
        main(["--root", str(paths.root), "notify", "--run", "x", "--inbox"])
    assert conflicting.value.code != 0


def test_request_run_json_unconfigured(paths, write_project, capsys, monkeypatch):
    write_project(id="builder", purpose="p")
    monkeypatch.delenv("REGISTRY_NTFY_TOPIC", raising=False)
    monkeypatch.delenv("REGISTRY_NTFY_COMMAND_TOPIC", raising=False)
    code, out = run(paths, "request-run", "builder", "--json", capsys=capsys)
    assert code == 0
    assert json.loads(out)["configured"] is False


def test_request_run_unknown_project_is_clean_error(paths, capsys):
    code = main(["--root", str(paths.root), "request-run", "ghost"])
    captured = capsys.readouterr()
    assert code != 0
    assert "not found" in captured.err.lower() or "unknown" in captured.err.lower()


def test_build_queue_and_readiness_commands(paths, write_project, capsys):
    write_project(
        id="builder", purpose="Ship it", desired_outcome="It ships",
        repo="owner/builder", brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "shadow"},
    )
    code, out = run(paths, "build-queue", "--json", capsys=capsys)
    assert code == 0
    queue = json.loads(out)
    assert queue["ready_count"] == 1
    assert queue["candidates"][0]["dry_run"] is True

    code, out = run(paths, "build-readiness", "builder", "--json", capsys=capsys)
    assert code == 0
    result = json.loads(out)
    assert result["brief"] == {"complete": True, "missing": []}
    assert result["policy"]["mode"] == "shadow"


def test_build_commands_show_waiting_owner(paths, write_project, capsys):
    write_project(
        id="waiting", purpose="Ship it", desired_outcome="It ships",
        repo="owner/waiting", brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build"},
    )
    write_project(
        id="ready", purpose="Ship it", desired_outcome="It ships",
        repo="owner/ready", brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build"},
    )
    save_state(paths, {"waiting": ProjectBuildState(waiting_on={
        "kind": "blocked_by_policy", "classes": ["generated_data"],
        "since": "2026-08-20T12:00:00+00:00", "run_id": "blocked-run",
    })})

    code, out = run(paths, "build-readiness", "waiting", capsys=capsys)
    assert code == 0
    assert "waiting on" in out

    code, out = run(
        paths, "build-queue", "--state", "waiting_owner", capsys=capsys
    )
    assert code == 0
    assert "waiting" in out
    assert "\nready " not in out

    write_project(
        id="waiting", purpose="Ship it", desired_outcome="It ships",
        repo="owner/waiting", brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build", "allow": ["generated_data"]},
    )
    code, out = run(paths, "build-readiness", "waiting", capsys=capsys)
    assert code == 0
    assert "waiting: ready" in out
    assert "waiting on" not in out
    code, out = run(
        paths, "build-readiness", "waiting", "--json", capsys=capsys
    )
    assert code == 0
    assert json.loads(out)["waiting_on"]["kind"] == "blocked_by_policy"


def test_build_lifecycle_commands_start_context_finish(paths, write_project, capsys):
    write_project(
        id="builder", name="Builder", purpose="Ship it",
        desired_outcome="It ships", repo="owner/builder",
        brief={"done_criteria": ["Tests pass"]},
        automation={"mode": "build"},
    )
    code, out = run(
        paths, "build", "context", "builder", "--json", capsys=capsys
    )
    assert code == 0
    assert json.loads(out)["project_id"] == "builder"

    code, out = run(
        paths, "build", "start", "--host", "mac", "--project", "builder",
        "--json", capsys=capsys,
    )
    assert code == 0
    started = json.loads(out)
    assert started["candidate"]["project_id"] == "builder"

    code, out = run(
        paths, "build", "finish", started["run_id"], "--outcome", "completed",
        "--json", capsys=capsys,
    )
    assert code == 0
    assert json.loads(out)["state_after"]["last_outcome"] == "completed"
    assert paths.build_lease_file.exists()
    code, out = run(
        paths, "build", "finish", started["run_id"], "--confirm-writeback",
        "--json", capsys=capsys,
    )
    assert code == 0
    assert json.loads(out)["writeback_confirmed"] is True
    assert not paths.build_lease_file.exists()


# -- dashboard rendering --------------------------------------------------


def test_dashboard_contains_every_section(paths, write_project):
    write_project(id="p", purpose="why", repo="owner/repo", lifecycle="now",
                  desired_outcome="done", active=True, last_reviewed="2026-07-20",
                  next_action={"description": "Decide the format", "reviewed": "2026-07-20"},
                  accomplishments=[{"date": "2026-06-01", "summary": "shipped v1"}],
                  relationships=[{"kind": "related", "target": "p2"}])
    write_project(id="p2", purpose="why")
    snapshot = make_snapshot(make_repo_state("owner/repo", pull_requests=[
        make_pr(repo="owner/repo", number=1, ci_state="failure")
    ]))
    text = render_dashboard(load_registry(paths), snapshot, now=NOW)

    for heading in (
        "## Registry health", "## Lifecycle", "## Active projects", "## Work queue",
        "## Build",
        "## Missing next actions", "## Open pull requests", "## Needs attention",
        "## Registry / GitHub mismatches", "## Review queue",
        "## Recent accomplishments", "## Relationships",
    ):
        assert heading in text


def test_dashboard_build_section_groups_actionable_states(paths, write_project):
    write_project(
        id="ready", purpose="Ship", desired_outcome="Shipped", repo="owner/ready",
        brief={"done_criteria": ["Tests pass"]}, automation={"mode": "build"},
    )
    write_project(
        id="intent", purpose="Choose", desired_outcome="Chosen", repo="owner/intent",
        brief={"done_criteria": []}, automation={"mode": "build"},
    )
    write_project(
        id="paused", purpose="Wait", desired_outcome="Resume", repo="owner/paused",
        brief={"done_criteria": ["Tests pass"]}, automation={"mode": "build"},
        blocked_by="owner approval",
    )
    write_project(
        id="spec", purpose="Specify", desired_outcome="Specified", repo="owner/spec",
        brief={"done_criteria": ["Spec is complete"]},
        automation={"mode": "spec_only"},
    )
    text = render_dashboard(load_registry(paths), make_snapshot(), now=NOW)
    assert "**#1 ready** `ready`" in text
    assert "**spec only** `spec`" in text
    assert "**needs intent** `intent` — brief.done_criteria" in text
    assert "**paused** `paused` — owner approval" in text
    assert "manual only;" in text and "ineligible." in text


def test_dashboard_states_evidence_age(paths, write_project):
    write_project(id="p", purpose="why")
    text = render_dashboard(load_registry(paths), make_snapshot(), now=NOW)
    assert "GitHub evidence just now" in text


def test_dashboard_flags_a_missing_next_action(paths, write_project):
    write_project(id="p", purpose="why", active=True)
    text = render_dashboard(load_registry(paths), make_snapshot(), now=NOW)
    assert "**— none —**" in text
    assert "active project with no next action" in text


def test_dashboard_escapes_pipes_in_titles(paths, write_project):
    write_project(id="p", purpose="why", active=True,
                  next_action={"description": "Choose a | b", "reviewed": "2026-07-20"})
    text = render_dashboard(load_registry(paths), make_snapshot(), now=NOW)
    assert "Choose a \\| b" in text


def test_dashboard_renders_repo_scoped_branch_signal_without_none(paths, write_project):
    write_project(id="p", purpose="why")
    snapshot = make_snapshot(make_repo_state(
        "stranger/repo",
        branches=[make_branch("old")],
        branches_fetched=True,
    ))
    text = render_dashboard(load_registry(paths), snapshot, now=NOW)
    assert "[stranger/repo](https://github.com/stranger/repo)" in text
    assert "#None" not in text
