"""Propose-then-apply workflow for curated fields (MCP-005).

Proposing and applying are separate operations against separate files:

* ``propose_update`` writes ``data/proposals/<id>.json`` and touches nothing
  under ``registry/``;
* ``apply_proposal`` requires that proposal id **and** an explicit
  ``approved=True``, re-validates the whole registry, refuses if the change
  introduces a new error, then writes the curated file and appends to the
  audit log.

An agent can therefore prepare a change, but only an explicit approval can
land one (operating rule 8).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .model import Project, RegistryError
from .storage import (
    Paths,
    Registry,
    append_jsonl,
    read_json,
    save_project,
    write_json,
)
from .validation import ValidationConfig, ValidationReport, validate

PENDING = "pending"
APPLIED = "applied"
REJECTED = "rejected"

#: Curated paths a proposal may change. Everything else -- ids, accomplishments,
#: relationships -- is deliberately hand-edited, and observed GitHub fields are
#: not curated at all.
EDITABLE_PATHS: frozenset[str] = frozenset(
    {
        "name", "purpose", "desired_outcome", "lifecycle", "active", "needs_review",
        "category", "priority", "effort", "horizon", "blocked_by", "repo", "is_fork",
        "visibility", "last_reviewed", "public", "showcase_order", "notes", "tags",
        "next_action.description", "next_action.reviewed", "next_action.link",
        "descriptions.private", "descriptions.public_safe",
    }
)

_BOOL_PATHS = {"active", "needs_review", "public", "is_fork"}
_DATE_PATHS = {"last_reviewed", "next_action.reviewed"}
_INT_PATHS = {"showcase_order"}
_LIST_PATHS = {"tags"}


class ProposalError(RuntimeError):
    """A proposal could not be created or applied."""


@dataclass
class Proposal:
    id: str
    project_id: str
    created_at: str
    changes: dict[str, dict[str, Any]] = field(default_factory=dict)
    rationale: str | None = None
    status: str = PENDING
    applied_at: str | None = None
    source: str = "propose"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "created_at": self.created_at,
            "status": self.status,
            "rationale": self.rationale,
            "source": self.source,
            "applied_at": self.applied_at,
            "changes": self.changes,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Proposal":
        return cls(
            id=raw["id"],
            project_id=raw["project_id"],
            created_at=raw["created_at"],
            changes=raw.get("changes") or {},
            rationale=raw.get("rationale"),
            status=raw.get("status", PENDING),
            applied_at=raw.get("applied_at"),
            source=raw.get("source", "propose"),
        )

    def render_diff(self) -> str:
        """Exact before/after values, for review before approval."""
        lines = [f"Proposal {self.id} — project `{self.project_id}` ({self.status})"]
        if self.rationale:
            lines.append(f"Rationale: {self.rationale}")
        lines.append("")
        for path, change in self.changes.items():
            lines.append(f"  {path}")
            lines.append(f"    before: {_render_value(change.get('before'))}")
            lines.append(f"    after:  {_render_value(change.get('after'))}")
        return "\n".join(lines)


def _render_value(value: Any) -> str:
    if value is None:
        return "(unset)"
    return repr(value)


# -- value handling -------------------------------------------------------


def coerce_value(path: str, value: Any) -> Any:
    """Turn a CLI string or JSON value into the type the schema expects."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in {"null", "none", ""}:
        return None

    if path in _BOOL_PATHS:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"true", "yes", "1"}:
            return True
        if text in {"false", "no", "0"}:
            return False
        raise ProposalError(f"{path}: expected a boolean, got {value!r}")

    if path in _INT_PATHS:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ProposalError(f"{path}: expected an integer, got {value!r}") from None

    if path in _DATE_PATHS:
        if isinstance(value, dt.date):
            return value.isoformat()
        try:
            return dt.date.fromisoformat(str(value)).isoformat()
        except ValueError:
            raise ProposalError(f"{path}: expected YYYY-MM-DD, got {value!r}") from None

    if path in _LIST_PATHS:
        if isinstance(value, list):
            return [str(item) for item in value]
        return [part.strip() for part in str(value).split(",") if part.strip()]

    return str(value)


def _get_path(data: dict[str, Any], path: str) -> Any:
    node: Any = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _set_path(data: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = data
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    if value is None:
        node.pop(parts[-1], None)
    else:
        node[parts[-1]] = value


# -- propose --------------------------------------------------------------


def propose_update(
    registry: Registry,
    project_id: str,
    changes: dict[str, Any],
    rationale: str | None = None,
    paths: Paths | None = None,
    now: dt.datetime | None = None,
    source: str = "propose",
    write: bool = True,
) -> Proposal:
    """Record a proposed change. Does not modify the project."""
    paths = paths or Paths.resolve()
    now = now or dt.datetime.now(dt.timezone.utc)
    project = registry.get(project_id)
    if project is None:
        raise ProposalError(f"unknown project id: {project_id}")
    if not changes:
        raise ProposalError("a proposal must change at least one field")

    unknown = sorted(set(changes) - EDITABLE_PATHS)
    if unknown:
        raise ProposalError(
            f"not proposable: {', '.join(unknown)}. "
            f"Editable paths: {', '.join(sorted(EDITABLE_PATHS))}"
        )

    current = project.to_dict()
    diff: dict[str, dict[str, Any]] = {}
    for path, raw_value in changes.items():
        after = coerce_value(path, raw_value)
        before = _get_path(current, path)
        if before == after:
            continue
        diff[path] = {"before": before, "after": after}

    if not diff:
        raise ProposalError("proposal is a no-op: every value already matches")

    proposal = Proposal(
        id=_next_proposal_id(paths, project_id, now),
        project_id=project_id,
        created_at=now.isoformat(),
        changes=diff,
        rationale=rationale,
        source=source,
    )
    if write:
        write_json(paths.proposals_dir / f"{proposal.id}.json", proposal.to_dict())
    return proposal


def _next_proposal_id(paths: Paths, project_id: str, now: dt.datetime) -> str:
    base = f"{project_id}-{now.strftime('%Y%m%d%H%M%S')}"
    if not (paths.proposals_dir / f"{base}.json").exists():
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if not (paths.proposals_dir / f"{candidate}.json").exists():
            return candidate
    raise ProposalError("could not allocate a proposal id")  # pragma: no cover


def load_proposal(proposal_id: str, paths: Paths | None = None) -> Proposal:
    paths = paths or Paths.resolve()
    raw = read_json(paths.proposals_dir / f"{proposal_id}.json")
    if raw is None:
        raise ProposalError(f"unknown proposal: {proposal_id}")
    return Proposal.from_dict(raw)


def list_proposals(paths: Paths | None = None, status: str | None = None) -> list[Proposal]:
    paths = paths or Paths.resolve()
    if not paths.proposals_dir.is_dir():
        return []
    found = []
    for path in sorted(paths.proposals_dir.glob("*.json")):
        raw = read_json(path)
        if raw is None:
            continue
        proposal = Proposal.from_dict(raw)
        if status is None or proposal.status == status:
            found.append(proposal)
    return found


# -- apply ----------------------------------------------------------------


@dataclass
class ApplyResult:
    proposal: Proposal
    project: Project
    report: ValidationReport
    written_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.proposal.status == APPLIED,
            "proposal": self.proposal.to_dict(),
            "project_id": self.project.id,
            "written_path": self.written_path,
            "validation": self.report.to_dict(),
        }


def apply_proposal(
    registry: Registry,
    proposal_id: str,
    approved: bool = False,
    paths: Paths | None = None,
    now: dt.datetime | None = None,
    config: ValidationConfig | None = None,
    write: bool = True,
) -> ApplyResult:
    """Apply an approved proposal after re-validating the result.

    Refuses when: approval is absent, the proposal is not pending, the result
    does not parse, or the change would introduce a validation error that did
    not already exist.
    """
    paths = paths or Paths.resolve()
    now = now or dt.datetime.now(dt.timezone.utc)

    if not approved:
        raise ProposalError(
            "refusing to apply without explicit approval (pass approved=true)"
        )

    proposal = load_proposal(proposal_id, paths)
    if proposal.status != PENDING:
        raise ProposalError(
            f"proposal {proposal_id} is already {proposal.status}; it cannot be re-applied"
        )

    project = registry.get(proposal.project_id)
    if project is None:
        raise ProposalError(f"unknown project id: {proposal.project_id}")

    baseline = validate(registry, today=now.date(), config=config)
    baseline_keys = {(f.rule_id, f.project_id) for f in baseline.errors}

    raw = project.to_dict()
    for path, change in proposal.changes.items():
        _set_path(raw, path, change.get("after"))

    try:
        updated = Project.parse(raw, source_path=project.source_path)
    except RegistryError as exc:
        raise ProposalError(f"proposal would produce an invalid project: {exc}") from None

    # Validate against a registry that includes the change, so cross-project
    # rules (relationships, overlap, `now` capacity) are checked too.
    candidate = Registry(
        projects={**registry.projects, updated.id: updated},
        loaded_at=registry.loaded_at,
        paths=registry.paths,
    )
    report = validate(candidate, today=now.date(), config=config)
    new_errors = [f for f in report.errors if (f.rule_id, f.project_id) not in baseline_keys]
    if new_errors:
        detail = "; ".join(f"{f.project_id or '-'}: {f.message}" for f in new_errors)
        raise ProposalError(f"refusing to apply: change introduces new errors — {detail}")

    written_path = None
    if write:
        written_path = str(save_project(updated, paths))
        proposal.status = APPLIED
        proposal.applied_at = now.isoformat()
        write_json(paths.proposals_dir / f"{proposal.id}.json", proposal.to_dict())
        append_jsonl(
            paths.audit_log,
            {
                "ts": now.isoformat(),
                "action": "apply_proposal",
                "proposal_id": proposal.id,
                "project_id": proposal.project_id,
                "source": proposal.source,
                "rationale": proposal.rationale,
                "changes": proposal.changes,
            },
        )
        registry.projects[updated.id] = updated

    return ApplyResult(
        proposal=proposal, project=updated, report=report, written_path=written_path
    )


def reject_proposal(
    proposal_id: str, paths: Paths | None = None, now: dt.datetime | None = None
) -> Proposal:
    paths = paths or Paths.resolve()
    now = now or dt.datetime.now(dt.timezone.utc)
    proposal = load_proposal(proposal_id, paths)
    if proposal.status != PENDING:
        raise ProposalError(f"proposal {proposal_id} is already {proposal.status}")
    proposal.status = REJECTED
    write_json(paths.proposals_dir / f"{proposal.id}.json", proposal.to_dict())
    append_jsonl(
        paths.audit_log,
        {
            "ts": now.isoformat(),
            "action": "reject_proposal",
            "proposal_id": proposal.id,
            "project_id": proposal.project_id,
        },
    )
    return proposal


# -- reviews --------------------------------------------------------------

#: Fields a review may update, per US-008.
REVIEW_FIELDS = ("purpose", "lifecycle", "notes", "next_action.description", "active")


def record_review(
    registry: Registry,
    project_id: str,
    reviewed_on: dt.date | None = None,
    updates: dict[str, Any] | None = None,
    rationale: str | None = None,
    paths: Paths | None = None,
    now: dt.datetime | None = None,
    approved: bool = True,
) -> ApplyResult:
    """Stamp a review date and optionally update reviewed fields (US-008).

    This goes through the same propose/apply machinery, so a review is
    validated and audited exactly like any other curated change.
    """
    paths = paths or Paths.resolve()
    now = now or dt.datetime.now(dt.timezone.utc)
    reviewed_on = reviewed_on or now.date()

    changes: dict[str, Any] = {"last_reviewed": reviewed_on.isoformat()}
    for path, value in (updates or {}).items():
        if path not in EDITABLE_PATHS:
            raise ProposalError(f"not updatable during review: {path}")
        changes[path] = value

    project = registry.get(project_id)
    if project is not None and project.next_action is not None:
        # Reviewing a project also re-dates the next action it just confirmed.
        changes.setdefault("next_action.reviewed", reviewed_on.isoformat())

    proposal = propose_update(
        registry,
        project_id,
        changes,
        rationale=rationale or "recorded review",
        paths=paths,
        now=now,
        source="record_review",
    )
    if not approved:
        raise ProposalError(
            f"pending proposal {proposal.id} filed, NOTHING applied; "
            "call again with approved=true"
        )
    return apply_proposal(registry, proposal.id, approved=approved, paths=paths, now=now)
