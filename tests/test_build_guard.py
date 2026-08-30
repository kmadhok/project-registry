from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


GUARD = Path(__file__).parents[1] / "scripts" / "build-guard.py"
SETTINGS = Path(__file__).parents[1] / ".claude" / "settings.json"


def write_lease(root: Path, *, prs_open: int = 0, dry_run: bool = False) -> dict:
    lease = {
        "run_id": "R1",
        "host": "test-host",
        "project_id": "target",
        "repo": "kmadhok/target",
        "status": "running",
        "prs_open": prs_open,
        "dry_run": dry_run,
        "chunks": {
            "c1": {
                "pr_number": 7,
                "branch": "push/R1-1-x",
                "verify": "passed",
                "verdict": "approve",
                "merged": False,
            },
            "c2": {
                "pr_number": 8,
                "branch": "push/R1-2-y",
                "verify": "passed",
                "verdict": "reject",
                "merged": False,
            },
        },
        "reconcile_targets": [],
        "contract_forbidden_paths": [],
    }
    build_dir = root / "data" / "build"
    build_dir.mkdir(parents=True, exist_ok=True)
    (build_dir / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
    return lease


def run_guard(
    root: Path,
    command: str | None = None,
    *,
    cwd: Path | None = None,
    tool_name: str = "Bash",
    tool_input: dict | None = None,
) -> subprocess.CompletedProcess[str]:
    payload = {
        "session_id": "test-session",
        "cwd": str(cwd or root),
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input if tool_input is not None else {"command": command},
    }
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(root)
    return subprocess.run(
        [sys.executable, str(GUARD)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def assert_denied(result: subprocess.CompletedProcess[str], rule_id: str) -> None:
    assert result.returncode == 2, result.stderr
    assert rule_id in result.stderr
    assert result.stderr.startswith("build-guard:")


def init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "guard@example.test"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Guard Test"], cwd=path, check=True)
    (path / "README.md").write_text("test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=path, check=True)


def test_no_lease_is_inert(tmp_path: Path) -> None:
    result = run_guard(tmp_path, "git push origin main")
    assert result.returncode == 0
    assert run_guard(
        tmp_path, tool_name="Write",
        tool_input={"file_path": str(tmp_path / "registry/projects/x.yaml")},
    ).returncode == 0


def test_settings_register_file_write_guard() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    entries = settings["hooks"]["PreToolUse"]
    write_entry = next(
        entry for entry in entries
        if entry["matcher"] == "Write|Edit|MultiEdit|NotebookEdit"
    )
    bash_entry = next(entry for entry in entries if entry["matcher"] == "Bash")
    assert write_entry["hooks"] == bash_entry["hooks"]


def test_no_lease_allows_even_malformed_hook_json(tmp_path: Path) -> None:
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(GUARD)],
        input="{bad json",
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0


def test_file_writes_are_scoped_while_lease_is_active(tmp_path: Path) -> None:
    lease = write_lease(tmp_path)
    clone = tmp_path.parent / f"{tmp_path.name}-clone"
    clone.mkdir()
    lease["contract_forbidden_paths"] = [".env"]
    (tmp_path / "data" / "build" / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
    assert_denied(run_guard(
        tmp_path, tool_name="Write",
        tool_input={"file_path": str(tmp_path / "registry/projects/x.yaml")},
    ), "direct_write_scope")
    assert_denied(run_guard(
        tmp_path, tool_name="Edit", tool_input={"file_path": str(clone / ".env")},
    ), "direct_write_scope")
    assert run_guard(
        tmp_path, cwd=clone, tool_name="Write", tool_input={"file_path": "src/x.py"},
    ).returncode == 0


@pytest.mark.parametrize(
    ("command", "rule_id"),
    [
        ("git push origin main", "non_push_branch"),
        ("git push --force origin push/R1-1-x", "force_push"),
        ("git push -f", "force_push"),
        ("git push origin +push/R1-1-x", "force_push"),
        ("git push origin push/OTHER-1-x", "non_push_branch"),
        ("git push --delete origin feature", "branch_delete"),
    ],
)
def test_target_push_denials(tmp_path: Path, command: str, rule_id: str) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(run_guard(tmp_path, command, cwd=clone), rule_id)


def test_git_dash_c_push_to_main_is_denied(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(
        run_guard(tmp_path, f"git -C {shlex_quote(clone)} push origin main"),
        "non_push_branch",
    )


def test_git_global_config_is_consumed_before_push_rules(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(run_guard(tmp_path, "git -c a=b push origin main", cwd=clone), "non_push_branch")
    assert_denied(run_guard(tmp_path, "git -c a=b", cwd=clone), "git_parse")


@pytest.mark.parametrize(
    "command",
    ["git push origin push/R1-1-x", "git push -u origin push/R1-3-z"],
)
def test_target_push_branch_is_allowed(tmp_path: Path, command: str) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert run_guard(tmp_path, command, cwd=clone).returncode == 0


def test_push_remote_must_match_leased_repo(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(
        run_guard(tmp_path, "git push attacker push/R1-1-x", cwd=clone),
        "push_remote",
    )
    assert run_guard(
        tmp_path, "git push https://github.com/KMADHOK/TARGET.git push/R1-1-x", cwd=clone,
    ).returncode == 0
    assert_denied(run_guard(tmp_path, "git push attacker main"), "push_remote")


@pytest.mark.parametrize(
    "command",
    [
        "git push origin checkpoint/R1-1",
        "git push origin refs/tags/checkpoint/R1-2",
        "git push origin tag checkpoint/R1-3",
    ],
)
def test_target_checkpoint_tag_push_is_allowed(tmp_path: Path, command: str) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert run_guard(tmp_path, command, cwd=clone).returncode == 0


@pytest.mark.parametrize(
    ("command", "rule_id"),
    [
        ("git push origin checkpoint/OTHER-1", "tag_push_scope"),
        ("git push origin --tags", "tag_push_scope"),
        ("git push origin --follow-tags", "tag_push_scope"),
    ],
)
def test_target_tag_push_scope(tmp_path: Path, command: str, rule_id: str) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(run_guard(tmp_path, command, cwd=clone), rule_id)


@pytest.mark.parametrize(
    ("branch", "expected", "rule_id"),
    [("push/R1-1-x", 0, None), ("main", 2, "non_push_branch")],
)
def test_bare_push_resolves_current_branch(
    tmp_path: Path, branch: str, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    init_repo(clone, branch)
    result = run_guard(tmp_path, "git push", cwd=clone)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


@pytest.mark.parametrize(
    ("command", "expected", "rule_id"),
    [
        ("git push origin main", 0, None),
        ("git push origin push/R1-1-x", 2, "registry_push_branch"),
    ],
)
def test_registry_push_branch_policy(
    tmp_path: Path, command: str, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, command)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


@pytest.mark.parametrize(
    ("staged_path", "expected", "rule_id"),
    [
        ("registry/projects/x.yaml", 2, "registry_commit_scope"),
        ("data/build/runs.jsonl", 0, None),
    ],
)
def test_registry_commit_scope(
    tmp_path: Path, staged_path: str, expected: int, rule_id: str | None
) -> None:
    init_repo(tmp_path)
    write_lease(tmp_path)
    path = tmp_path / staged_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", staged_path], cwd=tmp_path, check=True)
    result = run_guard(tmp_path, "git commit -m guarded")
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


def test_registry_commit_allows_builder_filed_proposals(tmp_path: Path) -> None:
    # A needs_intent run files data/proposals/<id>.json via `registry propose`;
    # the write-back must be able to commit it or it never reaches the owner.
    init_repo(tmp_path)
    write_lease(tmp_path)
    proposal = tmp_path / "data" / "proposals" / "builder-20260826122745.json"
    proposal.parent.mkdir(parents=True, exist_ok=True)
    proposal.write_text("{}\n", encoding="utf-8")
    subprocess.run(["git", "add", "data/proposals"], cwd=tmp_path, check=True)
    assert run_guard(tmp_path, "git commit -m guarded").returncode == 0
    curated = tmp_path / "registry" / "projects" / "builder.yaml"
    curated.parent.mkdir(parents=True, exist_ok=True)
    curated.write_text("id: builder\n", encoding="utf-8")
    subprocess.run(["git", "add", "registry"], cwd=tmp_path, check=True)
    assert_denied(run_guard(tmp_path, "git commit -m guarded"), "registry_commit_scope")


@pytest.mark.parametrize(
    ("command", "prs_open", "expected", "rule_id"),
    [
        ("gh pr create --head push/R1-1-x", 0, 0, None),
        ("gh pr create --head push/R1-1-x", 1, 2, "one_pr_per_chunk"),
        ("gh pr create --head feature", 0, 2, "pr_head_branch"),
        ("gh pr create --head push/R1-1-x --repo other/repo", 0, 2, "wrong_repo"),
    ],
)
def test_pr_create_policy(
    tmp_path: Path, command: str, prs_open: int, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path, prs_open=prs_open)
    result = run_guard(tmp_path, command)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


@pytest.mark.parametrize(("branch", "expected"), [("main", 2), ("push/R1-1-x", 0)])
def test_pr_create_without_head_resolves_branch_even_with_repo(
    tmp_path: Path, branch: str, expected: int,
) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    init_repo(clone, branch)
    result = run_guard(tmp_path, "gh pr create --repo kmadhok/target", cwd=clone)
    assert result.returncode == expected
    if expected:
        assert "pr_head_branch" in result.stderr


@pytest.mark.parametrize(
    ("command", "expected", "rule_id"),
    [
        ("gh pr merge 7 --squash", 0, None),
        ("gh pr merge 7", 2, "merge_method"),
        ("gh pr merge 8 --squash", 2, "unverified_merge"),
        ("gh pr merge 9 --squash", 2, "unverified_merge"),
        ("gh pr merge 7 --squash --admin", 2, "merge_admin"),
    ],
)
def test_pr_merge_policy(
    tmp_path: Path, command: str, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, command)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


@pytest.mark.parametrize(
    ("dry_run", "expected", "rule_id"),
    [(True, 2, "shadow_mode"), (False, 0, None)],
)
def test_pr_merge_shadow_mode(
    tmp_path: Path, dry_run: bool, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path, dry_run=dry_run)
    result = run_guard(tmp_path, "gh pr merge 7 --squash")
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


@pytest.mark.parametrize(
    ("command", "expected", "rule_id"),
    [
        ("gh pr close 12", 2, "foreign_pr"),
        ("gh pr close 8", 0, None),
        ("gh pr close 7", 2, "pr_close_policy"),
    ],
)
def test_pr_close_policy(
    tmp_path: Path, command: str, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, command)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


def test_invalid_review_can_close_but_cannot_merge(tmp_path: Path) -> None:
    lease = write_lease(tmp_path)
    lease["chunks"]["c1"]["verdict"] = "invalid"
    (tmp_path / "data" / "build" / "lease.json").write_text(
        json.dumps(lease), encoding="utf-8"
    )

    closed = run_guard(
        tmp_path, "gh pr close 7 --repo kmadhok/target"
    )
    assert closed.returncode == 0
    merged = run_guard(
        tmp_path, "gh pr merge 7 --squash --repo kmadhok/target"
    )
    assert_denied(merged, "unverified_merge")


@pytest.mark.parametrize(
    ("command", "expected", "rule_id"),
    [
        ("gh issue close 3", 2, "issue_mutation"),
        ("gh repo delete x", 2, "repo_mutation"),
        ("gh api -X PATCH repos/x", 2, "api_mutation"),
        ("gh api -XPOST repos/x", 2, "api_mutation"),
        ("gh api repos/x -f state=open", 2, "api_mutation"),
        ("gh api repos/x", 0, None),
        ("gh release upload v1 artifact.zip", 2, "release_mutation"),
        ("gh secret list", 2, "secret_mutation"),
        ("registry proposal-apply x --approve", 2, "registry_write_tool"),
        ("registry propose x --set brief.done_criteria=a", 0, None),
        ("registry propose x --set lifecycle=now", 2, "intent_only_proposal"),
        ("registry record-review x", 2, "registry_write_tool"),
        ("python3 -m project_registry.cli import-github", 2, "registry_write_tool"),
        ("registry build resume", 2, "breaker_bypass"),
        ("cat .env", 2, "secret_read"),
        ("cat private/secret-store/value.txt", 2, "secret_read"),
        ("cat README.md", 0, None),
        ("printenv | grep TOKEN", 2, "secret_env"),
    ],
)
def test_command_rules(
    tmp_path: Path, command: str, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, command)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


def test_compound_command_evaluates_every_segment(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(
        run_guard(tmp_path, "git status && git push origin main", cwd=clone),
        "non_push_branch",
    )


def test_push_redirections_are_removed_before_evaluation(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    init_repo(clone, branch="push/R1-1-x")

    allowed = [
        'git push -u origin "push/R1-1-x" 2>&1',
        'git push origin "push/R1-1-x" > /tmp/out.log 2>&1',
    ]
    for command in allowed:
        assert run_guard(
            tmp_path, f"cd {shlex_quote(clone)} && {command}"
        ).returncode == 0

    assert_denied(
        run_guard(tmp_path, "git push origin main 2>&1", cwd=clone),
        "non_push_branch",
    )
    assert_denied(
        run_guard(
            tmp_path,
            'git push --force origin "push/R1-1-x" 2>/dev/null',
            cwd=clone,
        ),
        "force_push",
    )


def test_pr_body_command_substitution_heredocs_follow_receiver_policy(
    tmp_path: Path,
) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    init_repo(clone, branch="push/R1-1-x")
    prefix = (
        f"cd {shlex_quote(clone)} && gh pr create "
        '--head "push/R1-1-x" --title "t" --body '
    )

    cat_body = "$(cat <<'EOF'\nbody line\ngit push origin main\nEOF\n)"
    assert run_guard(tmp_path, prefix + f'"{cat_body}"').returncode == 0

    python_body = "$(python3 <<'EOF'\ngh pr merge 1 --squash\nEOF\n)"
    assert_denied(
        run_guard(tmp_path, prefix + f'"{python_body}"'),
        "indirect_invocation",
    )


@pytest.mark.parametrize(
    ("command", "expected", "rule_id"),
    [
        (
            'python3 -c "import subprocess; subprocess.run([\'git\',\'push\',\'origin\',\'main\'])"',
            2,
            "indirect_invocation",
        ),
        ('bash -c "gh pr merge 7 --squash"', 2, "indirect_invocation"),
        ("python3 - <<'EOF'\n# git push origin main\nEOF", 2, "indirect_invocation"),
        ("python3 -m pytest -q", 0, None),
        ("bash scripts/run.sh", 0, None),
    ],
)
def test_indirect_invocation_policy(
    tmp_path: Path, command: str, expected: int, rule_id: str | None
) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, command)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


def test_plain_file_heredoc_is_allowed(tmp_path: Path) -> None:
    write_lease(tmp_path)
    command = (
        "cat > /tmp/task-spec.md << 'EOF'\n"
        "# Task\n"
        "- run tests\n"
        "- mentions git push and gh pr merge in prose\n"
        "EOF"
    )
    assert run_guard(tmp_path, command).returncode == 0


def test_interpreter_heredoc_body_is_scanned(tmp_path: Path) -> None:
    write_lease(tmp_path)
    command = (
        "python3 - << 'EOF'\n"
        "# Task\n"
        "- run tests\n"
        "- mentions git push and gh pr merge in prose\n"
        "EOF"
    )
    assert_denied(run_guard(tmp_path, command), "indirect_invocation")


def test_tee_heredoc_body_is_data(tmp_path: Path) -> None:
    write_lease(tmp_path)
    command = "tee /tmp/x.md << EOF\nmentions gh pr merge in prose\nEOF"
    assert run_guard(tmp_path, command).returncode == 0


def test_heredoc_body_is_not_tokenized_and_following_segments_are_checked(
    tmp_path: Path,
) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    command = (
        "cat > x << EOF\n"
        "it's data && still data | with an unmatched ' quote\n"
        "EOF\n"
        "git push origin main"
    )
    assert_denied(run_guard(tmp_path, command, cwd=clone), "non_push_branch")


def test_unterminated_heredoc_body_is_data_to_end_of_input(tmp_path: Path) -> None:
    write_lease(tmp_path)
    command = "cat > x << EOF\nit's data && gh pr merge 7 | still data"
    assert run_guard(tmp_path, command).returncode == 0


def test_cd_prefix_controls_branch_policy(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    clone.mkdir()
    assert_denied(
        run_guard(tmp_path, f"cd {shlex_quote(clone)} && git push origin main"),
        "non_push_branch",
    )


def test_contract_forbidden_path_cannot_be_read_or_staged(tmp_path: Path) -> None:
    lease = write_lease(tmp_path)
    lease["contract_forbidden_paths"] = ["private/config.json"]
    (tmp_path / "data" / "build" / "lease.json").write_text(json.dumps(lease), encoding="utf-8")
    assert_denied(run_guard(tmp_path, "cat private/config.json"), "secret_read")
    assert_denied(run_guard(tmp_path, "git add private/config.json"), "forbidden_path_stage")


def test_broad_add_checks_changed_forbidden_paths(tmp_path: Path) -> None:
    write_lease(tmp_path)
    clone = tmp_path / "clone"
    init_repo(clone)
    lease = json.loads((tmp_path / "data/build/lease.json").read_text(encoding="utf-8"))
    lease["contract_forbidden_paths"] = ["secrets/**"]
    (tmp_path / "data/build/lease.json").write_text(json.dumps(lease), encoding="utf-8")
    assert run_guard(tmp_path, "git add .", cwd=clone).returncode == 0
    secret = clone / "secrets/token.txt"
    secret.parent.mkdir()
    secret.write_text("not-a-real-secret\n", encoding="utf-8")
    assert_denied(run_guard(tmp_path, "git add .", cwd=clone), "forbidden_path_stage")


def test_broad_add_fails_closed_when_status_cannot_run(tmp_path: Path) -> None:
    lease = write_lease(tmp_path)
    lease["contract_forbidden_paths"] = ["**/.env"]
    (tmp_path / "data/build/lease.json").write_text(json.dumps(lease), encoding="utf-8")
    clone = tmp_path / "not-a-repo"
    clone.mkdir()
    assert_denied(run_guard(tmp_path, "git add .", cwd=clone), "forbidden_path_stage")


def test_finalize_pending_allows_registry_push_only(tmp_path: Path) -> None:
    lease = write_lease(tmp_path)
    lease["status"] = "finalize_pending"
    (tmp_path / "data/build/lease.json").write_text(json.dumps(lease), encoding="utf-8")
    clone = tmp_path / "clone"
    clone.mkdir()
    assert run_guard(tmp_path, "git push origin main").returncode == 0
    assert_denied(
        run_guard(tmp_path, "git push origin push/R1-1-x", cwd=clone), "finalize_only"
    )
    assert_denied(
        run_guard(tmp_path, "gh pr create --head push/R1-1-x", cwd=clone), "finalize_only"
    )
    assert_denied(
        run_guard(tmp_path, "gh pr merge 7 --squash", cwd=clone), "finalize_only"
    )


def test_registry_destroy_is_denied(tmp_path: Path) -> None:
    write_lease(tmp_path)
    assert_denied(run_guard(tmp_path, f"rm -rf {shlex_quote(tmp_path / 'data')}"), "registry_destroy")


@pytest.mark.parametrize(
    ("tool_name", "tool_input", "expected", "rule_id"),
    [
        ("mcp__project-registry__record_project_review", {"project_id": "x"}, 2, "registry_write_tool"),
        (
            "mcp__project-registry__propose_project_update",
            {"project_id": "x", "changes": {"brief.non_goals": "x"}},
            0,
            None,
        ),
        (
            "mcp__project-registry__propose_project_update",
            {"project_id": "x", "changes": {"priority": "high"}},
            2,
            "intent_only_proposal",
        ),
    ],
)
def test_mcp_rules(
    tmp_path: Path,
    tool_name: str,
    tool_input: dict,
    expected: int,
    rule_id: str | None,
) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, tool_name=tool_name, tool_input=tool_input)
    assert result.returncode == expected
    if rule_id:
        assert rule_id in result.stderr


def test_malformed_lease_fails_closed(tmp_path: Path) -> None:
    build_dir = tmp_path / "data" / "build"
    build_dir.mkdir(parents=True)
    (build_dir / "lease.json").write_text("{not json", encoding="utf-8")
    assert_denied(run_guard(tmp_path, "git status"), "guard_error")


def test_denial_appends_guard_event(tmp_path: Path) -> None:
    write_lease(tmp_path)
    result = run_guard(tmp_path, "git push origin push/OTHER-1-x")
    assert_denied(result, "registry_push_branch")
    events = [
        json.loads(line)
        for line in (tmp_path / "data" / "build" / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["type"] == "guard_denied"
    assert events[-1]["reason"] == "registry_push_branch"
    assert events[-1]["run_id"] == "R1"
    assert events[-1]["detail"]["command"] == "git push origin push/OTHER-1-x"


def shlex_quote(path: Path) -> str:
    """Quote a test path without importing project code."""
    import shlex

    return shlex.quote(str(path))
