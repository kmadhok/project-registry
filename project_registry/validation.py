from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from project_registry.io import load_json
from project_registry.model import (
    CLAIM_TYPES,
    CONFIDENCE_LEVELS,
    LIFECYCLES,
    MATURITY_LEVELS,
    RELATIONSHIP_TYPES,
    REVIEW_STATUSES,
    SCHEMA_VERSION,
)


def _is_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _required(mapping: dict[str, Any], keys: Iterable[str], prefix: str) -> list[str]:
    return [f"{prefix}: missing required field '{key}'" for key in keys if key not in mapping]


def _unexpected(mapping: dict[str, Any], keys: Iterable[str], prefix: str) -> list[str]:
    allowed = set(keys)
    return [f"{prefix}: unexpected field '{key}'" for key in mapping if key not in allowed]


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _optional_string(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _string_list(value: Any, prefix: str) -> list[str]:
    if not isinstance(value, list):
        return [f"{prefix}: must be an array"]
    return [
        f"{prefix}[{index}]: must be a string"
        for index, item in enumerate(value)
        if not isinstance(item, str)
    ]


def validate_snapshot(snapshot: dict[str, Any]) -> list[str]:
    keys = [
        "schema_version", "owner", "generated_at", "repositories",
        "open_pull_requests", "open_issues", "errors",
    ]
    errors = _required(snapshot, keys, "snapshot")
    if errors:
        return errors
    errors.extend(_unexpected(snapshot, keys, "snapshot"))
    if snapshot["schema_version"] != SCHEMA_VERSION:
        errors.append("snapshot: unsupported schema_version")
    if not _nonempty_string(snapshot["owner"]):
        errors.append("snapshot: owner must be a non-empty string")
    if not _is_datetime(snapshot["generated_at"]):
        errors.append("snapshot: generated_at must be an ISO-8601 timestamp")
    for field in ("repositories", "open_pull_requests", "open_issues", "errors"):
        if not isinstance(snapshot[field], list):
            errors.append(f"snapshot: {field} must be an array")
    if errors:
        return errors
    seen: set[str] = set()
    for index, repository in enumerate(snapshot["repositories"]):
        prefix = f"snapshot.repositories[{index}]"
        if not isinstance(repository, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        errors.extend(_required(repository, ["name", "name_with_owner", "url", "is_fork"], prefix))
        if not _nonempty_string(repository.get("name")):
            errors.append(f"{prefix}: name must be a non-empty string")
        full_name = repository.get("name_with_owner")
        if not _nonempty_string(full_name) or full_name.count("/") != 1:
            errors.append(f"{prefix}: name_with_owner must be owner/name")
        if not _nonempty_string(repository.get("url")):
            errors.append(f"{prefix}: url must be a non-empty string")
        if not isinstance(repository.get("is_fork"), bool):
            errors.append(f"{prefix}: is_fork must be a boolean")
        key = str(full_name or "").lower()
        if key in seen:
            errors.append(f"{prefix}: duplicate repository {key}")
        seen.add(key)
    return errors


def validate_understanding(brief: dict[str, Any], prefix: str = "understanding") -> list[str]:
    keys = [
        "schema_version", "repository", "analyzed_at", "revision", "analysis_scope",
        "stated_purpose", "observed_capabilities", "planned_work", "architecture",
        "maturity", "accomplishments", "gaps", "risks", "suggested_next_actions",
        "questions", "evidence", "confidence", "limitations",
    ]
    errors = _required(brief, keys, prefix)
    if errors:
        return errors
    errors.extend(_unexpected(brief, keys, prefix))
    if brief["schema_version"] != SCHEMA_VERSION:
        errors.append(f"{prefix}: unsupported schema_version")
    if not _nonempty_string(brief["repository"]) or brief["repository"].count("/") != 1:
        errors.append(f"{prefix}: repository must be owner/name")
    if not _is_datetime(brief["analyzed_at"]):
        errors.append(f"{prefix}: analyzed_at must be an ISO-8601 timestamp")
    if not isinstance(brief["revision"], str) or len(brief["revision"]) < 7:
        errors.append(f"{prefix}: revision must identify a commit")
    if brief["maturity"] not in MATURITY_LEVELS:
        errors.append(f"{prefix}: invalid maturity '{brief['maturity']}'")
    if not _optional_string(brief["stated_purpose"]):
        errors.append(f"{prefix}: stated_purpose must be a string or null")

    scope_keys = ["inspected", "missing", "inaccessible", "sampling_notes"]
    scope = brief.get("analysis_scope")
    if not isinstance(scope, dict):
        errors.append(f"{prefix}.analysis_scope: must be an object")
    else:
        errors.extend(_required(scope, scope_keys, f"{prefix}.analysis_scope"))
        errors.extend(_unexpected(scope, scope_keys, f"{prefix}.analysis_scope"))
        for field in ("inspected", "missing", "inaccessible"):
            if field in scope:
                errors.extend(_string_list(scope[field], f"{prefix}.analysis_scope.{field}"))
        if "sampling_notes" in scope and not _optional_string(scope["sampling_notes"]):
            errors.append(f"{prefix}.analysis_scope.sampling_notes: must be a string or null")

    confidence = brief.get("confidence")
    if not isinstance(confidence, dict):
        errors.append(f"{prefix}: invalid confidence")
    else:
        errors.extend(_required(confidence, ["level", "rationale"], f"{prefix}.confidence"))
        errors.extend(_unexpected(confidence, ["level", "rationale"], f"{prefix}.confidence"))
        if confidence.get("level") not in CONFIDENCE_LEVELS:
            errors.append(f"{prefix}: invalid confidence")
        if not _nonempty_string(confidence.get("rationale")):
            errors.append(f"{prefix}.confidence: rationale must be a non-empty string")

    errors.extend(_string_list(brief.get("questions"), f"{prefix}.questions"))
    errors.extend(_string_list(brief.get("limitations"), f"{prefix}.limitations"))

    evidence_ids: set[str] = set()
    evidence = brief.get("evidence")
    if not isinstance(evidence, list):
        errors.append(f"{prefix}: evidence must be an array")
        evidence = []
    for index, item in enumerate(evidence):
        item_prefix = f"{prefix}.evidence[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{item_prefix}: must be an object")
            continue
        item_keys = ["id", "kind", "location", "summary"]
        errors.extend(_required(item, item_keys, item_prefix))
        errors.extend(_unexpected(item, item_keys, item_prefix))
        for field in item_keys:
            if not _nonempty_string(item.get(field)):
                errors.append(f"{item_prefix}.{field}: must be a non-empty string")
        evidence_id = item.get("id")
        if evidence_id in evidence_ids:
            errors.append(f"{prefix}: duplicate evidence id '{evidence_id}'")
        if isinstance(evidence_id, str):
            evidence_ids.add(evidence_id)

    claim_fields = [
        "observed_capabilities", "planned_work", "architecture", "accomplishments",
        "gaps", "risks", "suggested_next_actions",
    ]
    for field in claim_fields:
        claims = brief.get(field)
        if not isinstance(claims, list):
            errors.append(f"{prefix}.{field}: must be an array")
            continue
        for index, claim in enumerate(claims):
            claim_prefix = f"{prefix}.{field}[{index}]"
            if not isinstance(claim, dict):
                errors.append(f"{claim_prefix}: must be an object")
                continue
            claim_keys = ["statement", "claim_type", "evidence_ids"]
            errors.extend(_required(claim, claim_keys, claim_prefix))
            errors.extend(_unexpected(claim, claim_keys, claim_prefix))
            if not _nonempty_string(claim.get("statement")):
                errors.append(f"{claim_prefix}: statement must be a non-empty string")
            if claim.get("claim_type") not in CLAIM_TYPES:
                errors.append(f"{claim_prefix}: invalid claim_type")
            refs = claim.get("evidence_ids")
            if not isinstance(refs, list):
                errors.append(f"{claim_prefix}: evidence_ids must be an array")
                continue
            if claim.get("claim_type") != "owner_context" and not refs:
                errors.append(f"{claim_prefix}: observed and inferred claims require evidence")
            for evidence_id in refs:
                if not isinstance(evidence_id, str):
                    errors.append(f"{claim_prefix}: evidence id must be a string")
                elif evidence_id not in evidence_ids:
                    errors.append(f"{claim_prefix}: unknown evidence id '{evidence_id}'")
    return errors


def validate_registry(registry: dict[str, Any], snapshot: dict[str, Any] | None, root: Path) -> list[str]:
    registry_keys = ["schema_version", "owner", "updated_at", "projects"]
    errors = _required(registry, registry_keys, "registry")
    if errors:
        return errors
    errors.extend(_unexpected(registry, registry_keys, "registry"))
    if registry["schema_version"] != SCHEMA_VERSION:
        errors.append("registry: unsupported schema_version")
    if not _nonempty_string(registry["owner"]):
        errors.append("registry: owner must be a non-empty string")
    if not _is_datetime(registry["updated_at"]):
        errors.append("registry: updated_at must be an ISO-8601 timestamp")
    projects = registry.get("projects")
    if not isinstance(projects, list):
        return [*errors, "registry: projects must be an array"]

    project_keys = [
        "id", "name", "repository", "review_status", "purpose", "public_summary",
        "lifecycle", "active", "priority", "desired_outcome", "next_action",
        "last_reviewed_at", "accomplishments", "relationships", "tags",
        "understanding_path", "notes",
    ]
    ids: set[str] = set()
    repositories: set[str] = set()
    understanding_paths: list[tuple[str, Path]] = []
    for index, project in enumerate(projects):
        prefix = f"projects[{index}]"
        if not isinstance(project, dict):
            errors.append(f"{prefix}: must be an object")
            continue
        errors.extend(_required(project, project_keys, prefix))
        errors.extend(_unexpected(project, project_keys, prefix))
        project_id = project.get("id")
        repository_value = project.get("repository")
        repository = str(repository_value or "").lower()
        if not _nonempty_string(project_id) or not project_id.startswith("github:"):
            errors.append(f"{prefix}: id must start with 'github:'")
        if not _nonempty_string(project.get("name")):
            errors.append(f"{prefix}: name must be a non-empty string")
        if not _nonempty_string(repository_value) or repository_value.count("/") != 1:
            errors.append(f"{prefix}: repository must be owner/name")
        if project_id in ids:
            errors.append(f"{prefix}: duplicate id '{project_id}'")
        if repository in repositories:
            errors.append(f"{prefix}: duplicate repository '{repository}'")
        if isinstance(project_id, str):
            ids.add(project_id)
        if repository:
            repositories.add(repository)

        if project.get("review_status") not in REVIEW_STATUSES:
            errors.append(f"{prefix}: invalid review_status")
        lifecycle = project.get("lifecycle")
        if lifecycle is not None and lifecycle not in LIFECYCLES:
            errors.append(f"{prefix}: invalid lifecycle '{lifecycle}'")
        for field in (
            "purpose", "public_summary", "desired_outcome", "next_action",
            "understanding_path", "notes",
        ):
            if not _optional_string(project.get(field)):
                errors.append(f"{prefix}: {field} must be a string or null")
        if not isinstance(project.get("active"), bool):
            errors.append(f"{prefix}: active must be a boolean")
        priority = project.get("priority")
        if priority is not None and (not isinstance(priority, int) or isinstance(priority, bool) or priority < 1):
            errors.append(f"{prefix}: priority must be a positive integer or null")
        reviewed_at = project.get("last_reviewed_at")
        if reviewed_at is not None and not _is_datetime(reviewed_at):
            errors.append(f"{prefix}: last_reviewed_at must be an ISO-8601 timestamp or null")
        errors.extend(_string_list(project.get("tags"), f"{prefix}.tags"))
        if isinstance(project.get("tags"), list) and len(project["tags"]) != len(set(project["tags"])):
            errors.append(f"{prefix}: tags must be unique")

        if project.get("review_status") == "reviewed":
            if not project.get("purpose"):
                errors.append(f"{prefix}: reviewed project requires purpose")
            if lifecycle is None:
                errors.append(f"{prefix}: reviewed project requires lifecycle")
        if project.get("active"):
            if not project.get("next_action"):
                errors.append(f"{prefix}: active project requires one next_action")
            if not _is_datetime(project.get("last_reviewed_at")):
                errors.append(f"{prefix}: active project requires last_reviewed_at")
        if lifecycle == "now" and not project.get("desired_outcome"):
            errors.append(f"{prefix}: lifecycle 'now' requires desired_outcome")

        relationships = project.get("relationships")
        if not isinstance(relationships, list):
            errors.append(f"{prefix}: relationships must be an array")
            relationships = []
        successor_found = False
        for relationship_index, relationship in enumerate(relationships):
            relationship_prefix = f"{prefix}.relationships[{relationship_index}]"
            if not isinstance(relationship, dict):
                errors.append(f"{relationship_prefix}: must be an object")
                continue
            errors.extend(_required(relationship, ["type", "project_id"], relationship_prefix))
            errors.extend(_unexpected(relationship, ["type", "project_id", "notes"], relationship_prefix))
            if relationship.get("type") not in RELATIONSHIP_TYPES:
                errors.append(f"{relationship_prefix}: invalid relationship type")
            if not _nonempty_string(relationship.get("project_id")):
                errors.append(f"{relationship_prefix}: project_id must be a non-empty string")
            if "notes" in relationship and not _optional_string(relationship["notes"]):
                errors.append(f"{relationship_prefix}: notes must be a string or null")
            if relationship.get("type") == "predecessor_of":
                successor_found = True
        if lifecycle == "superseded" and not successor_found:
            errors.append(f"{prefix}: superseded project must name its successor")

        accomplishments = project.get("accomplishments")
        if not isinstance(accomplishments, list):
            errors.append(f"{prefix}: accomplishments must be an array")
        else:
            for accomplishment_index, accomplishment in enumerate(accomplishments):
                accomplishment_prefix = f"{prefix}.accomplishments[{accomplishment_index}]"
                if not isinstance(accomplishment, dict):
                    errors.append(f"{accomplishment_prefix}: must be an object")
                    continue
                keys = ["date", "summary", "evidence"]
                errors.extend(_required(accomplishment, keys, accomplishment_prefix))
                errors.extend(_unexpected(accomplishment, keys, accomplishment_prefix))
                if "date" in accomplishment and not isinstance(accomplishment["date"], str):
                    errors.append(f"{accomplishment_prefix}.date: must be a string")
                if "summary" in accomplishment and not _nonempty_string(accomplishment["summary"]):
                    errors.append(f"{accomplishment_prefix}.summary: must be a non-empty string")
                if "evidence" in accomplishment:
                    errors.extend(_string_list(accomplishment["evidence"], f"{accomplishment_prefix}.evidence"))

        understanding_path = project.get("understanding_path")
        if understanding_path:
            understanding_paths.append((str(repository_value or ""), root / understanding_path))

    for index, project in enumerate(projects):
        if not isinstance(project, dict):
            continue
        for relationship in project.get("relationships") or []:
            if isinstance(relationship, dict) and relationship.get("project_id") not in ids:
                errors.append(
                    f"projects[{index}]: relationship target '{relationship.get('project_id')}' does not exist"
                )

    if snapshot:
        observed = {
            str(item.get("name_with_owner") or "").lower()
            for item in snapshot.get("repositories", [])
            if isinstance(item, dict)
        }
        for repository in sorted(observed - repositories):
            errors.append(f"registry: GitHub repository '{repository}' is not represented")

    for repository, path in understanding_paths:
        if not path.exists():
            errors.append(f"{repository}: understanding_path does not exist: {path}")
            continue
        brief = load_json(path)
        errors.extend(validate_understanding(brief, str(path.relative_to(root))))
        if brief.get("repository", "").lower() != repository.lower():
            errors.append(f"{repository}: understanding brief repository does not match")
    return errors


def validate_files(root: Path) -> list[str]:
    snapshot_path = root / "data" / "github_snapshot.json"
    registry_path = root / "data" / "projects.json"
    errors: list[str] = []
    snapshot = None
    if not snapshot_path.exists():
        errors.append(f"missing {snapshot_path.relative_to(root)}")
    else:
        snapshot = load_json(snapshot_path)
        errors.extend(validate_snapshot(snapshot))
    if not registry_path.exists():
        errors.append(f"missing {registry_path.relative_to(root)}")
    else:
        registry = load_json(registry_path)
        errors.extend(validate_registry(registry, snapshot, root))
    return errors
