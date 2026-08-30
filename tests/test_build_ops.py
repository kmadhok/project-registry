"""Safe branch and pull-request operations for autonomous builds."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from project_registry.build_ops import (
    branch_name,
    create_branch,
    open_pr,
    push_args,
    slugify,
)
from project_registry.build_runs import BuildError, read_events
from project_registry.cli import main
from project_registry.storage import read_json, write_json


def git(directory: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(directory), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture
def origin(tmp_path: Path) -> Path:
    target = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "--initial-branch=main", str(target)],
        check=True,
        capture_output=True,
    )
    return target


@pytest.fixture
def workdir(tmp_path: Path, origin: Path) -> Path:
    target = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(origin), str(target)], check=True, capture_output=True
    )
    git(target, "config", "user.email", "builder@example.test")
    git(target, "config", "user.name", "Builder Test")
    (target / "README.md").write_text("seed\n", encoding="utf-8")
    git(target, "add", "README.md")
    git(target, "commit", "-m", "seed")
    git(target, "push", "-u", "origin", "main")
    return target


@pytest.fixture
def fake_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    log = tmp_path / "gh-args.log"
    script = binary_dir / "gh"
    script.write_text(
        "#!/bin/sh\n"
        "for arg in \"$@\"; do printf '%s\\n' \"$arg\" >> \"$GH_ARGS_LOG\"; done\n"
        "printf '%s\\n' 'https://github.com/kmadhok/target/pull/42'\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setenv("GH_ARGS_LOG", str(log))
    monkeypatch.setenv("PATH", f"{binary_dir}{os.pathsep}{os.environ['PATH']}")
    return log


@pytest.fixture
def lease(paths) -> dict:
    value = {
        "run_id": "R1",
        "host": "test-host",
        "project_id": "target",
        "repo": "kmadhok/target",
        "status": "active",
        "prs_open": 0,
        "merges": 0,
        "chunks": {},
        "contract_forbidden_paths": ["secrets/**", "config/prod.yaml"],
        "reconcile_targets": [],
        "dry_run": False,
    }
    write_json(paths.build_lease_file, value)
    return value


def test_slugify_and_branch_name() -> None:
    assert slugify("Add DuckDB persistence!") == "add-duckdb-persistence"
    long_slug = slugify("word-" * 12)
    assert len(long_slug) <= 40
    assert not long_slug.endswith("-")
    assert slugify("") == "chunk"
    assert branch_name("R1", "1", "Add x") == "push/R1-1-add-x"


def test_push_args_are_fixed() -> None:
    assert push_args("push/R1-1-x") == [
        "push", "-u", "origin", "push/R1-1-x"
    ]


def test_create_branch_rejects_missing_mismatched_and_finalized_leases(
    paths, workdir: Path, lease: dict
) -> None:
    paths.build_lease_file.unlink()
    before = git(workdir, "rev-parse", "HEAD").strip()
    with pytest.raises(BuildError, match="lease does not match"):
        create_branch(paths, "R1", workdir, "1", "Add x")
    assert git(workdir, "rev-parse", "HEAD").strip() == before

    write_json(paths.build_lease_file, {**lease, "run_id": "OTHER"})
    with pytest.raises(BuildError, match="lease does not match"):
        create_branch(paths, "R1", workdir, "1", "Add x")

    write_json(paths.build_lease_file, {**lease, "status": "finalize_pending"})
    with pytest.raises(BuildError, match="finalize_pending"):
        create_branch(paths, "R1", workdir, "1", "Add x")


def test_create_branch_happy_path(paths, workdir: Path, lease: dict) -> None:
    result = create_branch(paths, "R1", workdir, "1", "Add x")

    assert result["branch"] == "push/R1-1-add-x"
    assert git(workdir, "branch", "--show-current").strip() == result["branch"]
    events, _ = read_events(paths)
    assert events[-1]["type"] == "chunk_started"
    assert events[-1]["detail"]["branch"] == result["branch"]


def test_open_pr_rejects_non_namespace_branch(paths, workdir: Path, lease: dict) -> None:
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    with pytest.raises(BuildError, match="outside the run namespace: main"):
        open_pr(paths, "R1", workdir, "1", "Add x", body)


def test_open_pr_rejects_forbidden_before_staging(
    paths, workdir: Path, lease: dict
) -> None:
    create_branch(paths, "R1", workdir, "1", "Add x")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    secret = workdir / "secrets" / "token.txt"
    secret.parent.mkdir()
    secret.write_text("nope\n", encoding="utf-8")
    before = git(workdir, "rev-parse", "HEAD").strip()

    with pytest.raises(BuildError, match="secrets/token.txt"):
        open_pr(paths, "R1", workdir, "1", "Add x", body)

    assert git(workdir, "rev-parse", "HEAD").strip() == before
    assert "?? secrets/token.txt" in git(
        workdir, "status", "--porcelain", "--untracked-files=all"
    )


def test_open_pr_rejects_no_changes(paths, workdir: Path, lease: dict) -> None:
    create_branch(paths, "R1", workdir, "1", "Add x")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    with pytest.raises(BuildError, match="nothing to commit"):
        open_pr(paths, "R1", workdir, "1", "Add x", body)


def test_open_pr_happy_path(
    paths, workdir: Path, origin: Path, fake_gh: Path, lease: dict
) -> None:
    branch = create_branch(paths, "R1", workdir, "1", "Add x")["branch"]
    (workdir / "change.txt").write_text("change\n", encoding="utf-8")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")

    result = open_pr(paths, "R1", workdir, "1", "Add x", body)

    assert branch in git(origin, "branch", "--list")
    assert fake_gh.read_text(encoding="utf-8").splitlines() == [
        "pr", "create", "--repo", "kmadhok/target", "--head", branch,
        "--title", "Add x", "--body-file", str(body),
    ]
    assert result["pr_url"] == "https://github.com/kmadhok/target/pull/42"
    assert result["pr_number"] == 42
    events, _ = read_events(paths)
    assert events[-1]["type"] == "pr_opened"
    assert events[-1]["pr_url"] == result["pr_url"]
    assert events[-1]["detail"]["pr_number"] == 42
    updated = read_json(paths.build_lease_file)
    assert updated["chunks"]["1"]["pr_number"] == 42
    assert updated["chunks"]["1"]["branch"] == branch


def test_build_ops_cli_json_and_failure(
    paths, workdir: Path, fake_gh: Path, lease: dict, capsys
) -> None:
    base = ["--root", str(paths.root), "build"]
    code = main([
        *base, "branch", "R1", "--chunk-id", "1", "--title", "Add x",
        "--workdir", str(workdir), "--json",
    ])
    branch_result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert branch_result["branch"] == "push/R1-1-add-x"

    (workdir / "change.txt").write_text("change\n", encoding="utf-8")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    code = main([
        *base, "pr", "R1", "--chunk-id", "1", "--title", "Add x",
        "--body-file", str(body), "--workdir", str(workdir), "--json",
    ])
    pr_result = json.loads(capsys.readouterr().out)
    assert code == 0
    assert pr_result["pr_url"] == "https://github.com/kmadhok/target/pull/42"

    code = main([
        *base, "branch", "WRONG", "--chunk-id", "2", "--title", "No",
        "--workdir", str(workdir),
    ])
    assert code == 2
    assert "build error:" in capsys.readouterr().err
