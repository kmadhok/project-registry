"""Exercise the launcher in a disposable checkout with controlled process exits."""

import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/usr/bin/env bash\n" + text)
    path.chmod(0o755)


@pytest.mark.parametrize("pull_status,claude_status,heartbeat_status,expected", [
    (0, 0, 0, 0), (1, 0, 0, 1), (0, 7, 0, 7), (0, 0, 1, 0), (0, 7, 1, 7),
])
def test_launcher_always_sends_heartbeat_and_preserves_status(
    tmp_path, pull_status, claude_status, heartbeat_status, expected,
):
    checkout = tmp_path / "checkout"
    launcher = checkout / "scripts/run-build.sh"
    launcher.parent.mkdir(parents=True)
    launcher.write_text((ROOT / "scripts/run-build.sh").read_text())
    binaries = tmp_path / "bin"
    executable(binaries / "uname", 'echo Darwin\n')
    executable(binaries / "git", f'exit {pull_status}\n')
    executable(binaries / "claude", f'echo claude >> "$CALLS"\nexit {claude_status}\n')
    executable(checkout / ".venv/bin/python",
               f'echo "$*" >> "$CALLS"\nexit {heartbeat_status}\n')
    calls = tmp_path / "calls"
    env = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}", CALLS=str(calls), GITHUB_TOKEN="")
    result = subprocess.run(["bash", str(launcher)], env=env, capture_output=True, text=True)
    assert result.returncode == expected, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert lines[-1] == "-m project_registry.cli notify --health"
    assert ("claude" in lines) == (pull_status == 0)
    if heartbeat_status:
        assert "run-build: heartbeat failed" in result.stdout
    if pull_status:
        assert "skipping the run" in result.stdout


@pytest.mark.parametrize("uname,label,active_host,runs", [
    ("Linux", "vps", "kayak", False), ("Linux", "kayak", "kayak", True),
    ("Linux", "vps", "", True), ("Darwin", "", "kayak", True),
])
def test_launcher_stands_down_on_inactive_host(tmp_path, uname, label, active_host, runs):
    checkout = tmp_path / "checkout"
    launcher = checkout / "scripts/run-build.sh"
    launcher.parent.mkdir(parents=True)
    launcher.write_text((ROOT / "scripts/run-build.sh").read_text())
    (checkout / "data/build").mkdir(parents=True)
    (checkout / "data/build/active_host").write_text(active_host + "\n")
    binaries = tmp_path / "bin"
    executable(binaries / "uname", f'echo {uname}\n')
    executable(binaries / "git", 'exit 0\n')
    executable(binaries / "claude", 'echo claude >> "$CALLS"\n')
    executable(checkout / ".venv/bin/python", 'echo "$*" >> "$CALLS"\n')
    calls = tmp_path / "calls"
    env = dict(os.environ, PATH=f"{binaries}:{os.environ['PATH']}", CALLS=str(calls), GITHUB_TOKEN="",
               BUILD_HOST_LABEL=label, HOME=str(tmp_path))
    result = subprocess.run(["bash", str(launcher)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    lines = calls.read_text().splitlines() if calls.exists() else []
    assert ("claude" in lines) == runs
    assert ("-m project_registry.cli notify --health" in lines) == runs
    if not runs:
        assert "not the active build host (kayak)" in result.stdout
