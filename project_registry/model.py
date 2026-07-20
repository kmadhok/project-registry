from __future__ import annotations

from typing import Any


SCHEMA_VERSION = 1
LIFECYCLES = {
    "now",
    "next",
    "incubating",
    "showcase",
    "maintained",
    "reference",
    "superseded",
    "archived",
}
REVIEW_STATUSES = {"needs_review", "reviewed"}
RELATIONSHIP_TYPES = {
    "component_of",
    "contains",
    "duplicate_of",
    "predecessor_of",
    "related_to",
    "successor_of",
}
MATURITY_LEVELS = {
    "empty",
    "concept",
    "scaffold",
    "prototype",
    "functional",
    "operational",
    "mature",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
CLAIM_TYPES = {"observed_fact", "supported_inference", "owner_context"}


def new_project(repository: dict[str, Any]) -> dict[str, Any]:
    full_name = repository["name_with_owner"]
    is_fork = bool(repository.get("is_fork"))
    return {
        "id": f"github:{full_name.lower()}",
        "name": repository["name"],
        "repository": full_name,
        "review_status": "needs_review",
        "purpose": None,
        "public_summary": None,
        "lifecycle": "reference" if is_fork else None,
        "active": False,
        "priority": None,
        "desired_outcome": None,
        "next_action": None,
        "last_reviewed_at": None,
        "accomplishments": [],
        "relationships": [],
        "tags": [],
        "understanding_path": None,
        "notes": None,
    }


def repository_slug(name_with_owner: str) -> str:
    return name_with_owner.replace("/", "__")
