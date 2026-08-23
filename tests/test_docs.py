"""Documentation stays synchronized with the executable CLI and MCP surfaces."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from project_registry.cli import build_parser
from project_registry.mcp.server import TOOLS


ROOT = Path(__file__).parents[1]
DOC_ENTRYPOINTS = (ROOT / "CLAUDE.md", ROOT / "README.md")


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, set[str]]:
    top = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    build_parser = top.choices["build"]
    build = next(
        action
        for action in build_parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return {"registry": set(top.choices), "build": set(build.choices)}


def _documented_registry_commands(path: Path) -> list[tuple[str, str]]:
    commands: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(
            r"\s*registry\s+([a-z][a-z-]*)(?:\s+([a-z][a-z-]*))?",
            line,
        )
        if match is None:
            continue
        top, nested = match.groups()
        commands.append(("registry", top))
        if top == "build" and nested:
            commands.append(("build", nested))
    return commands


def test_documented_registry_commands_exist_in_cli_parser():
    available = _subcommands(build_parser())
    for path in DOC_ENTRYPOINTS:
        documented = _documented_registry_commands(path)
        assert documented, path
        for namespace, command in documented:
            assert command in available[namespace], (path.name, namespace, command)


def test_readme_mcp_tool_count_matches_server():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"(?m)^(\d+) tools:", readme)
    assert match is not None
    assert int(match.group(1)) == len(TOOLS)


def test_autonomous_builder_adr_records_all_decisions():
    path = ROOT / "docs" / "ADRs" / "ADR-006-autonomous-builder.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    for number in range(1, 10):
        assert f"AD-{number}" in text


def test_retired_launcher_is_absent_from_current_documentation():
    # These immutable superpowers records describe the superseded system and
    # are retained verbatim. No current documentation may direct readers to it.
    historical = {
        Path("docs/superpowers/plans/2026-08-22-autonomous-builder-D-implementation-plan.md"),
        Path("docs/superpowers/specs/2026-08-22-autonomous-builder-A-current-state.md"),
        Path("docs/superpowers/specs/2026-08-22-autonomous-builder-C-technical-design.md"),
    }
    paths = [*DOC_ENTRYPOINTS]
    paths.extend(path for path in (ROOT / "docs").rglob("*") if path.is_file())
    offenders = {
        path.relative_to(ROOT)
        for path in paths
        if "run-push-project-wsl.sh" in path.read_text(encoding="utf-8")
    }
    assert offenders == historical
