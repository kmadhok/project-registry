"""SPEC parsing and deterministic readiness validation."""

from pathlib import Path

from project_registry.model import Automation, Brief, ChangeClass, OpenDecision, Project
from project_registry.specs import parse_spec, validate_spec


FIXTURES = Path(__file__).parent / "fixtures" / "specs"


def project(*, allow=(), non_goals=(), decisions=(), done_criteria=()):
    return Project(
        id="target",
        name="Target",
        brief=Brief(
            done_criteria=list(done_criteria),
            non_goals=list(non_goals),
            open_decisions=list(decisions),
        ),
        automation=Automation(allow=list(allow)),
    )


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parser_extracts_sections_wrapped_items_and_metadata():
    spec = parse_spec(fixture("annotated.md"))
    assert spec.goal == "Exercise deterministic validation."
    assert spec.done_looks_like == ["Every item has checkable metadata."]
    assert len(spec.items) == 5
    assert spec.items[0].acceptance == "the parser recognizes a wrapped checklist item"
    assert spec.items[1].classes == ["plan", "contract"]
    assert spec.items[-1].checked is True


def test_parser_extracts_criteria_metadata():
    text = """## Remaining work
- [ ] Comma separated
      Criteria: 1, 3
- [x] Explicitly none
      Criteria: none
- [ ] Absent
- [ ] Keeps invalid tokens in raw metadata
      Criteria: 2 x
"""
    items = parse_spec(text).items
    assert items[0].criteria == [1, 3]
    assert items[1].criteria == []
    assert items[2].criteria == []
    assert items[2].criteria_raw is None
    assert items[3].criteria == [2]
    assert "x" in items[3].criteria_raw


def test_legacy_items_are_unready_and_never_actions_are_detected():
    report = validate_spec(fixture("ai-news-aggregator.md"), project=project())
    assert report.next_ready_index is None
    assert {"missing_acceptance", "missing_tests"} <= set(report.items[0].problems)
    assert "never_class" in report.items[0].problems
    interview = validate_spec(fixture("interview-prep.md"), project=project())
    assert "never_class" in interview.items[0].problems


def test_policy_size_next_item_and_checked_item_handling():
    text = fixture("annotated.md")
    blocked = validate_spec(text, project=project(allow=(ChangeClass.DEPENDENCIES,)))
    assert blocked.next_ready_index == 1
    assert "blocked_by_policy" in blocked.items[2].problems
    assert "size_too_large" in blocked.items[3].problems
    assert blocked.checked_count == 1
    assert blocked.unchecked_count == 4
    allowed = validate_spec(
        text,
        project=project(allow=(ChangeClass.DEPENDENCIES, ChangeClass.PUBLIC_API)),
    )
    assert allowed.items[2].ready is True


def test_owner_phrase_and_open_decision_overlap_need_intent():
    decision = OpenDecision.parse(
        {"question": "Which underwriting domain (credit / insurance / mortgage)?"},
        "target",
    )
    report = validate_spec(
        fixture("underwriting.md"), project=project(decisions=(decision,))
    )
    assert report.items[0].needs_intent is True
    assert "needs_intent" in report.items[0].problems


def test_structure_errors_and_non_goal_warning():
    missing = validate_spec("# No roadmap\n## Goal\nSomething\n", project=project())
    assert missing.structure_ok is False
    assert "missing_remaining_work" in missing.problems
    assert missing.next_ready_index is None

    text = """# Example — spec
## Remaining work
- [ ] Replace the application framework adapter
      Acceptance: the application framework adapter is replaced
      Tests: tests/test_adapter.py
      Size: S
      Classes: none
      Verified-missing: no application framework adapter exists
"""
    report = validate_spec(
        text, project=project(non_goals=("Replace the application framework entirely",))
    )
    assert report.items[0].ready is True
    assert [warning.rule_id for warning in report.warnings] == ["contradicts_non_goal"]


def test_unknown_criterion_is_a_suggestion_and_does_not_affect_readiness():
    text = """## Remaining work
- [ ] Add a bounded test
      Acceptance: the test passes
      Tests: tests/test_one.py
      Size: S
      Classes: none
      Verified-missing: the test file is absent
      Criteria: 2 5
"""
    report = validate_spec(
        text, project=project(done_criteria=("one", "two", "three"))
    )
    finding = next(f for f in report.findings if f.rule_id == "unknown_criterion")
    assert finding.severity == "suggestion"
    assert "5" in finding.message
    assert report.items[0].ready is True
    assert report.items[0].criteria == [2, 5]


def test_unparsable_criterion_is_a_suggestion():
    text = """## Remaining work
- [ ] Legacy item
      Criteria: 2 x
"""
    report = validate_spec(text, project=project(done_criteria=("one", "two")))
    finding = next(f for f in report.findings if f.rule_id == "unknown_criterion")
    assert "x" in finding.message


def test_coverage_includes_checked_items_and_does_not_affect_ready_item():
    criteria = tuple(f"criterion {index}" for index in range(1, 6))
    text = """## Remaining work
- [x] Finished work
      Criteria: 1
- [ ] Ready work
      Acceptance: the outcome exists
      Tests: tests/test_one.py
      Size: S
      Classes: none
      Verified-missing: the outcome is absent
      Criteria: 3
"""
    report = validate_spec(text, project=project(done_criteria=criteria))
    finding = next(f for f in report.findings if f.rule_id == "uncovered_criteria")
    assert all(f"{index}: criterion {index}" in finding.message for index in (2, 4, 5))
    assert "1: criterion 1" not in finding.message
    assert "3: criterion 3" not in finding.message
    assert report.items[0].ready is True


def test_specs_without_criteria_lines_do_not_report_uncovered_criteria():
    report = validate_spec(
        fixture("annotated.md"), project=project(done_criteria=("one", "two"))
    )
    assert "uncovered_criteria" not in {finding.rule_id for finding in report.findings}
