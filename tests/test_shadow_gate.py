from __future__ import annotations

import json

from project_registry.build import (
    ProjectBuildState,
    append_event,
    save_state,
    shadow_gate,
)
from project_registry.cli import main

from .conftest import NOW


def add_event(paths, run_id: str, event_type: str, **fields) -> None:
    append_event(paths, {
        "run_id": run_id,
        "host": "buildbox",
        "type": event_type,
        "project_id": "builder",
        **fields,
    }, now=NOW)


def clean_shadow_runs(paths, count: int = 5, *, start: int = 0) -> None:
    for index in range(start, start + count):
        run_id = f"shadow-{index}"
        add_event(
            paths,
            run_id,
            "pr_opened",
            chunk_id="chunk-1",
            pr_url=f"https://github.com/owner/builder/pull/{index + 1}",
        )
        add_event(paths, run_id, "review_verdict", chunk_id="chunk-1")
        add_event(paths, run_id, "run_finished", outcome="shadow_completed")
        paths.build_digests_dir.mkdir(parents=True, exist_ok=True)
        (paths.build_digests_dir / f"{run_id}.md").write_text(
            "digest\n", encoding="utf-8"
        )


def check(result: dict, check_id: str) -> dict:
    return next(item for item in result["checks"] if item["id"] == check_id)


def test_clean_project_passes_shadow_gate(paths):
    clean_shadow_runs(paths)

    result = shadow_gate(paths, "builder")

    assert result["pass"] is True
    assert result["runs_considered"] == [f"shadow-{index}" for index in range(5)]
    assert [item["id"] for item in result["checks"]] == [
        "shadow_runs",
        "guard_denials",
        "verdict_on_every_pr",
        "no_failure_outcomes",
        "digest_present",
        "not_paused",
    ]
    assert all(item["pass"] for item in result["checks"])


def test_guard_denial_fails_shadow_gate(paths):
    clean_shadow_runs(paths)
    add_event(paths, "shadow-0", "guard_denied", reason="non_push_branch")

    result = shadow_gate(paths, "builder")

    assert check(result, "guard_denials")["pass"] is False
    assert result["pass"] is False


def test_pr_without_verdict_fails_shadow_gate(paths):
    clean_shadow_runs(paths)
    add_event(
        paths,
        "shadow-0",
        "pr_opened",
        chunk_id="chunk-2",
        pr_url="https://github.com/owner/builder/pull/99",
    )

    result = shadow_gate(paths, "builder")

    assert check(result, "verdict_on_every_pr")["pass"] is False


def test_too_few_shadow_runs_fails_shadow_gate(paths):
    clean_shadow_runs(paths, count=4)

    result = shadow_gate(paths, "builder", min_runs=5)

    assert check(result, "shadow_runs")["pass"] is False


def test_missing_digest_fails_shadow_gate(paths):
    clean_shadow_runs(paths)
    (paths.build_digests_dir / "shadow-2.md").unlink()

    result = shadow_gate(paths, "builder")

    assert check(result, "digest_present")["pass"] is False


def test_paused_project_fails_shadow_gate(paths):
    clean_shadow_runs(paths)
    save_state(paths, {"builder": ProjectBuildState(paused_reason="owner")})

    assert check(shadow_gate(paths, "builder"), "not_paused")["pass"] is False


def test_shadow_gate_cli_json_exit_codes(paths, write_project, capsys):
    write_project(id="builder", purpose="Ship it")
    clean_shadow_runs(paths, count=4)

    code = main([
        "--root", str(paths.root), "build-readiness", "builder",
        "--shadow-gate", "--json",
    ])
    failed = json.loads(capsys.readouterr().out)
    assert code == 1
    assert failed == shadow_gate(paths, "builder")

    clean_shadow_runs(paths, count=1, start=4)
    code = main([
        "--root", str(paths.root), "build-readiness", "builder",
        "--shadow-gate", "--min-runs", "5", "--json",
    ])
    passed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert passed["pass"] is True
