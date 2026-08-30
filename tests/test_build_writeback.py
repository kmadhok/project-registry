"""Atomic registry writeback behavior."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

import pytest

from project_registry.build_ops import writeback
from project_registry.build_runs import BuildError, begin_run, finish_run, read_events
from project_registry.cli import main
from project_registry.github.snapshot import Snapshot
from project_registry.storage import Paths, load_registry, read_json


NOW = dt.datetime(2026, 8, 30, 12, 0, tzinfo=dt.timezone.utc)


def git(directory: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args], check=check,
        capture_output=True, text=True,
    ).stdout


@pytest.fixture
def finalized_registry(tmp_path: Path) -> tuple[Paths, Path, str]:
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(origin)],
        check=True, capture_output=True,
    )
    root = tmp_path / "registry"
    subprocess.run(["git", "clone", str(origin), str(root)], check=True, capture_output=True)
    git(root, "config", "user.email", "registry@example.test")
    git(root, "config", "user.name", "Registry Test")
    (root / "registry" / "projects").mkdir(parents=True)
    (root / "registry" / "projects" / "builder.yaml").write_text(
        "id: builder\nname: Builder\npurpose: Ship it\ndesired_outcome: It ships\n"
        "repo: owner/builder\nbrief:\n  done_criteria:\n    - Tests pass\n"
        "automation:\n  mode: build\n  allow:\n    - ci\n",
        encoding="utf-8",
    )
    (root / "data").mkdir()
    (root / ".gitignore").write_text("data/build/lease.json\n", encoding="utf-8")
    (root / "README.md").write_text("seed\n", encoding="utf-8")
    git(root, "add", ".")
    git(root, "commit", "-m", "seed")
    git(root, "push", "-u", "origin", "main")
    paths = Paths(root)
    run_id = "run-writeback"
    begin_run(
        paths, host="test", run_id=run_id, now=NOW,
        registry=load_registry(paths), snapshot=Snapshot(),
    )
    finish_run(
        paths, run_id, outcome="completed", now=NOW,
        registry=load_registry(paths), snapshot=Snapshot(),
    )
    return paths, origin, run_id


def test_writeback_happy_path_and_idempotence(finalized_registry) -> None:
    paths, origin, run_id = finalized_registry
    result = writeback(paths, run_id)

    assert result["committed"] is True
    assert result["pushed"] is result["confirmed"] is True
    assert result["pending_commit"] is False
    assert len(result["commits"]) == 2
    assert git(origin, "log", "-2", "--format=%s", "main").splitlines() == [
        f"build: confirm writeback {run_id}",
        f"build: {run_id} builder completed",
    ]
    assert not paths.build_lease_file.exists()
    assert read_events(paths)[0][-1]["type"] == "writeback_confirmed"
    with pytest.raises(BuildError, match="missing"):
        writeback(paths, run_id)


def test_writeback_rebases_non_fast_forward(finalized_registry, tmp_path: Path) -> None:
    paths, origin, run_id = finalized_registry
    other = tmp_path / "other"
    subprocess.run(["git", "clone", str(origin), str(other)], check=True, capture_output=True)
    git(other, "config", "user.email", "other@example.test")
    git(other, "config", "user.name", "Other")
    (other / "other.txt").write_text("other\n", encoding="utf-8")
    git(other, "add", "other.txt")
    git(other, "commit", "-m", "unrelated")
    git(other, "push", "origin", "main")

    result = writeback(paths, run_id)

    assert result["pushed"] is result["confirmed"] is True
    assert "unrelated" in git(origin, "log", "--format=%s", "main").splitlines()


def test_writeback_push_failure_preserves_lease(finalized_registry, tmp_path: Path) -> None:
    paths, _origin, run_id = finalized_registry
    git(paths.root, "remote", "set-url", "origin", str(tmp_path / "missing.git"))

    result = writeback(paths, run_id)

    assert result["pushed"] is result["confirmed"] is False
    assert read_json(paths.build_lease_file)["status"] == "finalize_pending"
    assert not any(event["type"] == "writeback_confirmed" for event in read_events(paths)[0])


def test_writeback_scope_leaves_changes_unstaged(finalized_registry) -> None:
    paths, _origin, run_id = finalized_registry
    (paths.root / "README.md").write_text("modified\n", encoding="utf-8")
    untracked = paths.root / "registry" / "scratch.txt"
    untracked.write_text("untracked\n", encoding="utf-8")

    with pytest.raises(BuildError, match="registry_commit_scope: README.md"):
        writeback(paths, run_id)

    assert git(paths.root, "diff", "--cached", "--name-only") == ""
    assert " M README.md" in git(paths.root, "status", "--short")
    assert untracked.exists()


def test_writeback_second_push_failure(
    finalized_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths, _origin, run_id = finalized_registry
    from project_registry import build_ops

    real_push = build_ops._push_registry_main
    calls = 0

    def fail_second(push_paths):
        nonlocal calls
        calls += 1
        if calls == 2:
            return subprocess.CompletedProcess([], 1, "", "second push failed")
        return real_push(push_paths)

    monkeypatch.setattr(build_ops, "_push_registry_main", fail_second)
    result = writeback(paths, run_id)

    assert result["confirmed"] is True
    assert result["pending_commit"] is True
    assert not paths.build_lease_file.exists()


def test_writeback_cli_json_and_missing_lease(finalized_registry, capsys) -> None:
    paths, _origin, run_id = finalized_registry
    code = main([
        "--root", str(paths.root), "build", "writeback", run_id, "--json",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["confirmed"] is True

    code = main([
        "--root", str(paths.root), "build", "writeback", run_id, "--json",
    ])
    assert code == 2
    assert "build error:" in capsys.readouterr().err
