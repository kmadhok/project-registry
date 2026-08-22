"""Target-repository contract parsing and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from project_registry.contracts import contract_expectations, parse_contract, validate_contract
from project_registry.validation import ERROR, SUGGESTION


FIXTURES = Path(__file__).parent / "fixtures" / "contracts"


def report_for(extra: str = ""):
    return validate_contract(parse_contract(
        """\
schema: 1
registry_id: example-project
runtime: {kind: python, version: "3.11"}
test: [python3 -m pytest -q]
""" + extra
    ))


def rule_ids(report, severity=None):
    return {
        finding.rule_id
        for finding in report.findings
        if severity is None or finding.severity == severity
    }


def test_minimal_v1_contract_is_valid_and_runnable():
    report = report_for()
    assert report.ok is True
    assert report.runnable is True
    assert report.runnable_reason is None
    assert report.contract["max_test_minutes"] == 15
    assert report.contract["network"] == {"allowed": False}


def test_contract_expectations_describe_the_required_v1_file():
    assert contract_expectations(object()) == {
        "path": ".project-meta.yaml",
        "schema": 1,
        "required": True,
        "bootstrap_if_missing": True,
        "required_fields": ["schema", "registry_id", "runtime", "test"],
    }


@pytest.mark.parametrize(
    ("text", "rule_id", "field"),
    [
        (
            'schema: 1\nregistry_id: p\nruntime: {kind: python, version: "3.11"}\n',
            "contract_test_missing",
            "test",
        ),
        ("deploy: staging\n", "contract_deploy_not_none", "deploy"),
        ('secrets_required: ["API_KEY=abc"]\n', "contract_secret_value", "secrets_required"),
        ('secrets_required: ["sk-live-xxxx"]\n', "contract_secret_value", "secrets_required"),
        ("surprise: true\n", "contract_unknown_field", "surprise"),
    ],
)
def test_invalid_contract_fields_are_errors_and_name_the_field(text, rule_id, field):
    report = (
        validate_contract(parse_contract(text))
        if rule_id == "contract_test_missing"
        else report_for(text)
    )
    matching = [finding for finding in report.findings if finding.rule_id == rule_id]
    assert matching
    assert all(finding.severity == ERROR for finding in matching)
    assert all(field in finding.message for finding in matching)
    assert report.ok is False


def test_services_make_an_otherwise_valid_contract_not_runnable():
    report = report_for("services: [postgres]\n")
    assert report.ok is True
    assert report.runnable is False
    assert report.runnable_reason == "services"
    finding = next(f for f in report.findings if f.rule_id == "contract_services_not_runnable")
    assert finding.severity == SUGGESTION
    assert "services" in finding.message


def test_personal_and_forbidden_globs_are_preserved():
    report = report_for(
        "personal_data: [Roles/**, Pipeline.md]\n"
        "forbidden_paths: [.env, secrets/**]\n"
    )
    assert report.ok
    assert report.contract["personal_data"] == ["Roles/**", "Pipeline.md"]
    assert report.contract["forbidden_paths"] == [".env", "secrets/**"]


def test_registry_id_must_match_expected_project():
    report = validate_contract(
        parse_contract(
            'schema: 1\nregistry_id: actual\nruntime: {kind: python, version: "3.11"}\n'
            'test: ["pytest"]\n'
        ),
        expected_registry_id="expected",
    )
    assert "contract_registry_id_mismatch" in rule_ids(report, ERROR)
    assert "registry_id" in next(
        finding.message
        for finding in report.findings
        if finding.rule_id == "contract_registry_id_mismatch"
    )


def test_legacy_fields_normalize_and_produce_a_suggestion():
    parsed = parse_contract(
        'registry_id: legacy\ninterpreter: "3.11"\ntest: [pytest]\n'
        'validate: ["registry validate"]\n'
    )
    assert parsed["schema"] == 1
    assert parsed["runtime"] == {"kind": "python", "version": "3.11"}
    assert parsed["verify"] == ["registry validate"]
    report = validate_contract(parsed)
    assert report.ok
    assert "legacy_contract_fields" in rule_ids(report, SUGGESTION)
    assert "interpreter" not in report.contract
    assert "validate" not in report.contract


@pytest.mark.parametrize("path", sorted(FIXTURES.glob("*.yaml")))
def test_focus_repository_golden_contracts_validate(path):
    report = validate_contract(parse_contract(path.read_text(encoding="utf-8")))
    assert report.ok, (path.name, report.to_dict())
