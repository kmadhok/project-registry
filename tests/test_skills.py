"""Guardrails embedded in repository skills."""

from pathlib import Path


def test_intent_refresh_skill_proposes_and_never_applies():
    skill = Path(__file__).parents[1] / ".claude" / "skills" / "intent-refresh" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text(encoding="utf-8")
    assert "registry propose" in text
    assert "brief.open_decisions" in text
    for forbidden in ("proposal-apply", "record-review"):
        lines = [line for line in text.splitlines() if forbidden in line]
        assert len(lines) <= 1
        assert lines and "never" in lines[0].lower()
