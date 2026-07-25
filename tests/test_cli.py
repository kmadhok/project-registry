"""CLI behaviour, including exit codes and the dashboard (US-001, US-009, US-012)."""

from __future__ import annotations

import json

from project_registry.cli import main
from project_registry.dashboard import render_dashboard
from project_registry.github.sync import save_snapshot
from project_registry.storage import load_registry

from .conftest import NOW, make_issue, make_pr, make_repo_state, make_snapshot


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


def test_json_output_is_machine_readable(paths, write_project, capsys):
    write_project(id="p", purpose="why", lifecycle="maintained")
    code, out = run(paths, "list", "--json", capsys=capsys)
    assert code == 0
    assert json.loads(out)[0]["id"] == "p"


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
        "## Missing next actions", "## Open pull requests", "## Needs attention",
        "## Registry / GitHub mismatches", "## Review queue",
        "## Recent accomplishments", "## Relationships",
    ):
        assert heading in text


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
