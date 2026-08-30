from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).parents[1]
RUN_ALL = ROOT / "scripts" / "faults" / "run-all.sh"
BUILD_OBSERVE = ROOT / ".claude" / "skills" / "build-observe" / "SKILL.md"
REAL_FAULTS = ("F1", "F3", "F9", "F14", "F15", "F16", "F20", "F21", "F24")


def test_fault_harness_passes_real_faults(tmp_path: Path) -> None:
    scratch = tmp_path / "fault-scratch"
    env = os.environ.copy()
    env["FAULT_TMP"] = str(scratch)
    result = subprocess.run(
        ["bash", str(RUN_ALL)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = {
        line.split("|", 1)[0].strip(): line.split("|", 1)[1].strip()
        for line in result.stdout.splitlines()
        if "|" in line
    }
    for fault_id in REAL_FAULTS:
        assert rows.get(fault_id) == "PASS", result.stdout


def test_build_observe_skill_contains_safety_contract() -> None:
    assert BUILD_OBSERVE.exists()
    content = BUILD_OBSERVE.read_text(encoding="utf-8")
    for required in ("--force-named", "never run bare", "paused", "gh repo delete"):
        assert required in content
