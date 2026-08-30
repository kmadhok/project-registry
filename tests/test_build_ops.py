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
    merge_chunk,
    open_pr,
    push_args,
    reject_chunk,
    skip_chunk,
    slugify,
)
from project_registry.build_runs import BuildError, read_events, record_event
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
        "if [ \"$1\" = pr ] && [ \"$2\" = view ]; then\n"
        "  cat \"$GH_MERGE_SHA\"\n"
        "elif [ \"$1\" = pr ] && [ \"$2\" = create ]; then\n"
        "  printf '%s\\n' 'https://github.com/kmadhok/target/pull/42'\n"
        "fi\n",
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


def approve_detail(verdict: str = "approve") -> dict:
    return {
        "verdict": verdict,
        "reasons": [] if verdict == "approve" else ["needs work"],
        "risk_flags": [],
        "classes_seen": ["none"],
        "probes": [{
            "criterion": "tests pass",
            "probe": "pytest",
            "observed": "passed",
        }],
    }


def set_chunk(paths, lease: dict, **updates) -> dict:
    value = {
        "pr_number": 42,
        "pr_url": "https://github.com/kmadhok/target/pull/42",
        "branch": "push/R1-1-add-x",
        "verify": "passed",
        "verdict": "approve",
        "merged": False,
        "tag": None,
        **updates,
    }
    write_json(paths.build_lease_file, {**lease, "chunks": {"1": value}})
    return value


def journal_verified_chunk(paths) -> None:
    record_event(paths, "R1", {"type": "verify_passed", "chunk_id": "1"})
    record_event(paths, "R1", {
        "type": "review_verdict",
        "chunk_id": "1",
        "detail": approve_detail(),
    })


def gh_calls(log: Path) -> list[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


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


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("no_verify_event", "no verify_passed"),
        ("latest_request_changes", "latest review verdict"),
        ("invalid_lease_verdict", "verdict is not approve"),
        ("already_merged", "already merged"),
        ("pr_mismatch", "pr_number mismatch"),
    ],
)
def test_merge_chunk_refuses_unverified_before_gh(
    paths, workdir: Path, fake_gh: Path, lease: dict, case: str, message: str
) -> None:
    set_chunk(paths, lease)
    if case != "no_verify_event":
        journal_verified_chunk(paths)
    if case == "latest_request_changes":
        record_event(paths, "R1", {
            "type": "review_verdict",
            "chunk_id": "1",
            "detail": approve_detail("request_changes"),
        })
        current = read_json(paths.build_lease_file)
        current["chunks"]["1"]["verdict"] = "approve"
        write_json(paths.build_lease_file, current)
    elif case == "invalid_lease_verdict":
        current = read_json(paths.build_lease_file)
        current["chunks"]["1"]["verdict"] = "invalid"
        write_json(paths.build_lease_file, current)
    elif case == "already_merged":
        current = read_json(paths.build_lease_file)
        current["chunks"]["1"]["merged"] = True
        write_json(paths.build_lease_file, current)

    requested_pr = 99 if case == "pr_mismatch" else 42
    with pytest.raises(BuildError, match=f"unverified_merge:.*{message}"):
        merge_chunk(paths, "R1", workdir, "1", requested_pr)
    assert gh_calls(fake_gh) == []


def test_merge_chunk_refuses_shadow_and_finalize_pending(
    paths, workdir: Path, fake_gh: Path, lease: dict
) -> None:
    set_chunk(paths, {**lease, "dry_run": True})
    with pytest.raises(BuildError, match="shadow run never merges"):
        merge_chunk(paths, "R1", workdir, "1", 42)
    assert gh_calls(fake_gh) == []

    set_chunk(paths, {**lease, "status": "finalize_pending"})
    with pytest.raises(BuildError, match="finalize_pending"):
        merge_chunk(paths, "R1", workdir, "1", 42)
    assert gh_calls(fake_gh) == []


def test_merge_chunk_happy_path(
    paths,
    workdir: Path,
    origin: Path,
    fake_gh: Path,
    lease: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    create_branch(paths, "R1", workdir, "1", "Add x")
    (workdir / "change.txt").write_text("change\n", encoding="utf-8")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    open_pr(paths, "R1", workdir, "1", "Add x", body)
    journal_verified_chunk(paths)

    sha = git(origin, "rev-parse", "main").strip()
    sha_file = paths.root / "merge-sha.txt"
    sha_file.write_text(f"{sha}\n", encoding="utf-8")
    monkeypatch.setenv("GH_MERGE_SHA", str(sha_file))
    fake_gh.write_text("", encoding="utf-8")

    result = merge_chunk(paths, "R1", workdir, "1", 42)

    assert gh_calls(fake_gh) == [
        "pr", "merge", "42", "--repo", "kmadhok/target", "--squash",
        "--delete-branch",
        "pr", "view", "42", "--repo", "kmadhok/target", "--json",
        "mergeCommit", "-q", ".mergeCommit.oid",
    ]
    assert result["merge_sha"] == sha
    assert result["tag"] == "checkpoint/R1-1"
    assert "checkpoint/R1-1" in git(origin, "tag", "--list").splitlines()
    assert git(origin, "rev-parse", "checkpoint/R1-1").strip() == sha
    events, _ = read_events(paths)
    assert events[-1]["type"] == "merged"
    assert events[-1]["tag"] == "checkpoint/R1-1"
    assert events[-1]["detail"]["merge_sha"] == sha
    updated = read_json(paths.build_lease_file)
    assert updated["merges"] == 1
    assert updated["chunks"]["1"]["merged"] is True
    assert git(workdir, "branch", "--show-current").strip() == "main"


def test_reject_chunk_closes_pr_and_deletes_branches(
    paths, workdir: Path, origin: Path, fake_gh: Path, lease: dict
) -> None:
    branch = create_branch(paths, "R1", workdir, "1", "Add x")["branch"]
    (workdir / "change.txt").write_text("change\n", encoding="utf-8")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    open_pr(paths, "R1", workdir, "1", "Add x", body)
    record_event(paths, "R1", {
        "type": "review_verdict",
        "chunk_id": "1",
        "detail": approve_detail("request_changes"),
    })
    fake_gh.write_text("", encoding="utf-8")

    result = reject_chunk(paths, "R1", workdir, "1", "review", 42)

    assert gh_calls(fake_gh) == [
        "pr", "close", "42", "--repo", "kmadhok/target"
    ]
    assert branch not in git(origin, "branch", "--list")
    assert branch not in git(workdir, "branch", "--list")
    assert result["closed_pr"] == 42
    events, _ = read_events(paths)
    assert events[-1]["type"] == "chunk_rejected"
    assert events[-1]["reason"] == "review"


def test_reject_chunk_refuses_approved_pr_before_gh(
    paths, workdir: Path, fake_gh: Path, lease: dict
) -> None:
    set_chunk(paths, lease)
    with pytest.raises(BuildError, match="pr_close_policy"):
        reject_chunk(paths, "R1", workdir, "1", "review", 42)
    assert gh_calls(fake_gh) == []


def test_reject_chunk_refuses_branch_outside_namespace_without_deletion(
    paths, workdir: Path, origin: Path, fake_gh: Path, lease: dict
) -> None:
    git(workdir, "checkout", "-b", "feature/x")
    git(workdir, "push", "-u", "origin", "feature/x")
    set_chunk(
        paths,
        lease,
        branch="feature/x",
        verdict="request_changes",
    )

    with pytest.raises(BuildError, match="branch outside run namespace"):
        reject_chunk(paths, "R1", workdir, "1", "review", 42)
    assert gh_calls(fake_gh) == []
    assert "feature/x" in git(origin, "branch", "--list")
    assert "feature/x" in git(workdir, "branch", "--list")


def test_reject_chunk_without_pr_deletes_local_branch_only(
    paths, workdir: Path, fake_gh: Path, lease: dict
) -> None:
    branch = create_branch(paths, "R1", workdir, "1", "Add x")["branch"]

    result = reject_chunk(paths, "R1", workdir, "1", "verify")

    assert gh_calls(fake_gh) == []
    assert branch not in git(workdir, "branch", "--list")
    assert result["closed_pr"] is None
    events, _ = read_events(paths)
    assert events[-1]["type"] == "chunk_rejected"
    assert events[-1]["reason"] == "verify"


def test_skip_chunk_requires_shadow_and_returns_to_main(
    paths, workdir: Path, lease: dict
) -> None:
    with pytest.raises(BuildError, match="skip is for shadow runs"):
        skip_chunk(paths, "R1", workdir, "1")

    write_json(paths.build_lease_file, {**lease, "dry_run": True})
    create_branch(paths, "R1", workdir, "1", "Shadow x")
    result = skip_chunk(paths, "R1", workdir, "1")

    assert result["reason"] == "shadow"
    assert git(workdir, "branch", "--show-current").strip() == "main"
    events, _ = read_events(paths)
    assert events[-1]["type"] == "chunk_skipped"
    assert events[-1]["reason"] == "shadow"


def test_new_build_ops_cli_commands_and_failures(
    paths,
    workdir: Path,
    origin: Path,
    fake_gh: Path,
    lease: dict,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    base = ["--root", str(paths.root), "build"]
    branch = create_branch(paths, "R1", workdir, "1", "Add x")["branch"]
    (workdir / "change.txt").write_text("change\n", encoding="utf-8")
    body = paths.root / "body.md"
    body.write_text("body\n", encoding="utf-8")
    open_pr(paths, "R1", workdir, "1", "Add x", body)
    journal_verified_chunk(paths)
    sha = git(origin, "rev-parse", "main").strip()
    sha_file = paths.root / "merge-sha.txt"
    sha_file.write_text(f"{sha}\n", encoding="utf-8")
    monkeypatch.setenv("GH_MERGE_SHA", str(sha_file))

    code = main([
        *base, "merge", "R1", "--chunk-id", "1", "--pr", "42",
        "--workdir", str(workdir), "--json",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["merge_sha"] == sha

    git(workdir, "checkout", "-b", "push/R1-2-reject")
    code = main([
        *base, "reject", "R1", "--chunk-id", "2", "--reason", "verify",
        "--workdir", str(workdir), "--json",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "verify"

    current = read_json(paths.build_lease_file)
    current["dry_run"] = True
    write_json(paths.build_lease_file, current)
    git(workdir, "checkout", "-b", "push/R1-3-shadow")
    code = main([
        *base, "skip", "R1", "--chunk-id", "3", "--workdir", str(workdir),
        "--json",
    ])
    assert code == 0
    assert json.loads(capsys.readouterr().out)["reason"] == "shadow"

    current = read_json(paths.build_lease_file)
    current["dry_run"] = False
    write_json(paths.build_lease_file, current)
    code = main([
        *base, "skip", "R1", "--chunk-id", "4", "--workdir", str(workdir),
    ])
    assert code == 2
    assert "build error:" in capsys.readouterr().err
    assert branch
