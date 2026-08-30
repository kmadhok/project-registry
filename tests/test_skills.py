"""Guardrails embedded in repository skills and their launchers."""

import argparse
import re
import subprocess
from pathlib import Path

import yaml

from project_registry.cli import build_parser


ROOT = Path(__file__).parents[1]


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    commands: dict[str, set[str]] = {}
    top = next(
        action for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    commands["registry"] = set(top.choices)
    build = top.choices["build"]
    nested = next(
        action for action in build._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    commands["build"] = set(nested.choices)
    return commands


def _push_skill() -> tuple[Path, str]:
    path = ROOT / ".claude" / "skills" / "push-project" / "SKILL.md"
    return path, path.read_text(encoding="utf-8")


def test_push_project_skill_metadata_and_size():
    path, text = _push_skill()
    assert path.exists()
    assert len(text.splitlines()) <= 260
    frontmatter = yaml.safe_load(text.split("---", 2)[1])
    assert frontmatter["name"] == "push-project"


def test_push_project_skill_references_real_registry_subcommands():
    _, text = _push_skill()
    commands = _subcommands(build_parser())
    references = []
    for snippet in re.findall(r"`([^`]+)`", text):
        match = re.match(
            r"^(?:registry|\$REGISTRY_CLI)\s+"
            r"(?:(build)\s+)?([a-z][a-z-]*)",
            snippet.strip(),
        )
        if match:
            references.append(match.groups())
    assert references
    for build_prefix, command in references:
        namespace = "build" if build_prefix else "registry"
        assert command in commands[namespace], (namespace, command)


def test_push_project_skill_is_portable_and_complete():
    _, text = _push_skill()
    assert "/Users/" not in text
    assert "/home/" not in text
    assert "cloud routine" not in text.lower()
    for required in ("build-planner", "build-reviewer", "--squash", "checkpoint/", "STOP"):
        assert required in text
    assert "build writeback" in text
    assert "finalize_pending" in text


def test_run_build_dry_run_and_old_launcher_removed():
    launcher = ROOT / "scripts" / "run-build.sh"
    result = subprocess.run(
        ["bash", str(launcher), "--dry-run"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "claude -p" in result.stdout
    assert not (ROOT / "scripts" / "run-push-project-wsl.sh").exists()


def test_intent_refresh_skill_proposes_and_never_applies():
    skill = ROOT / ".claude" / "skills" / "intent-refresh" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    assert "registry propose" in text
    assert "brief.open_decisions" in text
    for forbidden in ("proposal-apply", "record-review"):
        lines = [line for line in text.splitlines() if forbidden in line]
        assert len(lines) <= 1
        assert lines and "never" in lines[0].lower()
