"""Validation for a target repository's ``.project-meta.yaml`` contract."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from .model import RegistryError
from .validation import ERROR, SUGGESTION


SCHEMA_VERSION = 1
REQUIRED_FIELDS = ("schema", "registry_id", "runtime", "test")

_REGISTRY_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_RUNTIME_KINDS = frozenset({"python", "node", "go", "rust", "other"})
_PACKAGE_MANAGERS = frozenset(
    {"pip", "uv", "poetry", "npm", "pnpm", "yarn", "cargo", "go", "other"}
)
_COMMAND_FIELDS = ("setup", "test", "lint", "typecheck", "verify", "generate")
_GLOB_FIELDS = ("generated_files", "personal_data", "forbidden_paths")
_LIST_FIELDS = (*_COMMAND_FIELDS, "secrets_required", "services", *_GLOB_FIELDS)
_V1_FIELDS = frozenset(
    {
        "schema",
        "registry_id",
        "runtime",
        "package_manager",
        *_LIST_FIELDS,
        "max_test_minutes",
        "network",
        "deploy",
    }
)
_LEGACY_FIELDS = ("interpreter", "validate")


class ContractError(RegistryError):
    """The contract text could not be parsed as a YAML mapping."""


class _ParsedContract(dict):
    """A dict that retains alias provenance without exposing marker keys."""

    def __init__(self, *args: Any, legacy_fields: tuple[str, ...] = (), **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.legacy_fields = legacy_fields


@dataclass(frozen=True)
class ContractFinding:
    rule_id: str
    severity: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
        }

    def render(self) -> str:
        return f"{self.severity.upper():10} {self.message} ({self.rule_id})"


@dataclass
class ContractReport:
    runnable: bool = True
    runnable_reason: str | None = None
    findings: list[ContractFinding] = field(default_factory=list)
    contract: dict[str, Any] = field(default_factory=dict)

    @property
    def errors(self) -> list[ContractFinding]:
        return [finding for finding in self.findings if finding.severity == ERROR]

    @property
    def suggestions(self) -> list[ContractFinding]:
        return [finding for finding in self.findings if finding.severity == SUGGESTION]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "runnable": self.runnable,
            "runnable_reason": self.runnable_reason,
            "findings": [finding.to_dict() for finding in self.findings],
            "contract": self.contract,
        }


def parse_contract(text: str) -> dict:
    """Parse YAML contract text and normalize supported legacy aliases."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ContractError(f"invalid .project-meta.yaml: {exc}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ContractError(".project-meta.yaml must contain a top-level mapping")
    return _normalize_aliases(loaded)


def validate_contract(
    raw: dict, *, expected_registry_id: str | None = None
) -> ContractReport:
    """Validate and normalize a schema-v1 target-repository contract."""
    if not isinstance(raw, dict):
        finding = ContractFinding(
            "contract_bad_type", ERROR, "contract root field must be a mapping"
        )
        return ContractReport(findings=[finding], contract=_defaults({}))

    normalized = _normalize_aliases(raw)
    legacy_fields = tuple(
        dict.fromkeys(
            (*getattr(raw, "legacy_fields", ()), *getattr(normalized, "legacy_fields", ()))
        )
    )
    contract = _defaults(dict(normalized))
    findings: list[ContractFinding] = []

    def add(rule_id: str, severity: str, message: str) -> None:
        findings.append(ContractFinding(rule_id, severity, message))

    if legacy_fields:
        names = ", ".join(f"`{name}`" for name in legacy_fields)
        add(
            "legacy_contract_fields",
            SUGGESTION,
            f"legacy field(s) {names} should use v1 fields `runtime` and `verify`",
        )

    for key in raw:
        if key not in _V1_FIELDS and key not in _LEGACY_FIELDS:
            add(
                "contract_unknown_field",
                ERROR,
                f"unknown top-level field `{key}` is not allowed",
            )

    schema = contract.get("schema")
    if type(schema) is not int or schema != SCHEMA_VERSION:
        add(
            "contract_schema_version",
            ERROR,
            f"field `schema` must be integer {SCHEMA_VERSION}",
        )

    registry_id = contract.get("registry_id")
    if registry_id is None or registry_id == "":
        add(
            "contract_registry_id_missing",
            ERROR,
            "field `registry_id` is required",
        )
    elif not isinstance(registry_id, str) or not _REGISTRY_ID.fullmatch(registry_id):
        add(
            "contract_bad_type",
            ERROR,
            "field `registry_id` must match ^[a-z0-9][a-z0-9-]*$",
        )
    elif expected_registry_id is not None and registry_id != expected_registry_id:
        add(
            "contract_registry_id_mismatch",
            ERROR,
            f"field `registry_id` is {registry_id!r}, expected {expected_registry_id!r}",
        )

    runtime = contract.get("runtime")
    if runtime is None:
        add("contract_runtime_missing", ERROR, "field `runtime` is required")
    elif not isinstance(runtime, dict):
        add("contract_bad_type", ERROR, "field `runtime` must be a mapping")
    else:
        kind = runtime.get("kind")
        version = runtime.get("version")
        if not isinstance(kind, str) or kind not in _RUNTIME_KINDS:
            add(
                "contract_bad_type",
                ERROR,
                "field `runtime.kind` must be python, node, go, rust, or other",
            )
        if not isinstance(version, str) or not version.strip():
            add(
                "contract_bad_type",
                ERROR,
                "field `runtime.version` must be a non-empty string",
            )

    package_manager = contract.get("package_manager")
    if package_manager is not None and (
        not isinstance(package_manager, str) or package_manager not in _PACKAGE_MANAGERS
    ):
        add(
            "contract_bad_type",
            ERROR,
            "field `package_manager` must be a supported package manager",
        )

    for field_name in _COMMAND_FIELDS:
        value = contract.get(field_name)
        if field_name == "test" and (value is None or value == []):
            add("contract_test_missing", ERROR, "field `test` requires at least one command")
            continue
        if value is not None:
            _validate_string_list(value, field_name, findings, non_empty_items=True)

    for field_name in _GLOB_FIELDS:
        value = contract.get(field_name)
        if value is not None:
            _validate_string_list(value, field_name, findings, non_empty_items=True)

    services = contract.get("services")
    if services is not None:
        _validate_string_list(services, "services", findings, non_empty_items=True)

    secrets = contract.get("secrets_required")
    if secrets is not None:
        if not isinstance(secrets, list):
            add("contract_bad_type", ERROR, "field `secrets_required` must be a list")
        else:
            for secret in secrets:
                if not isinstance(secret, str):
                    add(
                        "contract_bad_type",
                        ERROR,
                        "field `secrets_required` entries must be strings",
                    )
                elif not _SECRET_NAME.fullmatch(secret):
                    add(
                        "contract_secret_value",
                        ERROR,
                        f"field `secrets_required` entry {secret!r} must be a name only",
                    )

    max_minutes = contract.get("max_test_minutes")
    if type(max_minutes) is not int or max_minutes < 1:
        add(
            "contract_bad_type",
            ERROR,
            "field `max_test_minutes` must be an integer greater than or equal to 1",
        )

    network = contract.get("network")
    if not isinstance(network, dict) or type(network.get("allowed")) is not bool:
        add(
            "contract_bad_type",
            ERROR,
            "field `network.allowed` must be a boolean",
        )

    deploy = contract.get("deploy")
    if deploy is not None and deploy != "none":
        add(
            "contract_deploy_not_none",
            ERROR,
            "field `deploy` only accepts `none` in schema v1",
        )

    runnable = True
    runnable_reason = None
    if isinstance(services, list) and services:
        runnable = False
        runnable_reason = "services"
        add(
            "contract_services_not_runnable",
            SUGGESTION,
            "field `services` is non-empty, so the builder cannot run this contract",
        )

    return ContractReport(
        runnable=runnable,
        runnable_reason=runnable_reason,
        findings=findings,
        contract=contract,
    )


def contract_expectations(project: Any) -> dict[str, Any]:
    """Describe the contract a later builder task should expect for a project."""
    return {
        "path": ".project-meta.yaml",
        "schema": SCHEMA_VERSION,
        "required": True,
        "bootstrap_if_missing": True,
        "required_fields": list(REQUIRED_FIELDS),
    }


def _normalize_aliases(raw: dict[str, Any]) -> _ParsedContract:
    normalized = dict(raw)
    legacy_fields = tuple(field for field in _LEGACY_FIELDS if field in normalized)
    if legacy_fields and "schema" not in normalized:
        normalized["schema"] = SCHEMA_VERSION
    if "interpreter" in normalized and "runtime" not in normalized:
        normalized["runtime"] = {
            "kind": "python",
            "version": normalized["interpreter"],
        }
    if "validate" in normalized and "verify" not in normalized:
        normalized["verify"] = normalized["validate"]
    normalized.pop("interpreter", None)
    normalized.pop("validate", None)
    return _ParsedContract(normalized, legacy_fields=legacy_fields)


def _defaults(contract: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(contract)
    normalized.setdefault("max_test_minutes", 15)
    normalized.setdefault("network", {"allowed": False})
    if isinstance(normalized["network"], dict):
        normalized["network"] = {"allowed": False, **normalized["network"]}
    return normalized


def _validate_string_list(
    value: Any,
    field_name: str,
    findings: list[ContractFinding],
    *,
    non_empty_items: bool,
) -> None:
    if not isinstance(value, list):
        findings.append(
            ContractFinding(
                "contract_bad_type", ERROR, f"field `{field_name}` must be a list"
            )
        )
        return
    for item in value:
        if not isinstance(item, str) or (non_empty_items and not item.strip()):
            findings.append(
                ContractFinding(
                    "contract_bad_type",
                    ERROR,
                    f"field `{field_name}` entries must be non-empty strings",
                )
            )
