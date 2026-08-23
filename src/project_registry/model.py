"""Curated project model.

Everything in this module describes *human intent*. Observed GitHub state lives
in `github.snapshot` and is never merged into these objects, so operating rule 7
("generated data must never overwrite human-curated fields") holds structurally.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable


class RegistryError(ValueError):
    """Raised when curated data cannot be parsed into the model."""


class Lifecycle(str, Enum):
    NOW = "now"
    NEXT = "next"
    INCUBATING = "incubating"
    SHOWCASE = "showcase"
    MAINTAINED = "maintained"
    REFERENCE = "reference"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


#: Lifecycles that normally imply `active: true`. Deviations are surfaced as
#: suggestions, never errors -- PURPOSE.md keeps the two signals separate
#: precisely so exceptions can be expressed.
NORMALLY_ACTIVE = frozenset({Lifecycle.NOW, Lifecycle.NEXT, Lifecycle.MAINTAINED})


class Priority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Effort(str, Enum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


class Horizon(str, Enum):
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"
    YEAR = "year"
    SOMEDAY = "someday"


class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class AutomationMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    SPEC_ONLY = "spec_only"
    BUILD = "build"


class ChangeClass(str, Enum):
    DEPENDENCIES = "dependencies"
    CI = "ci"
    GENERATED_DATA = "generated_data"
    PUBLIC_API = "public_api"
    MIGRATIONS = "migrations"
    PERSONAL_DATA = "personal_data"


class OpenDecisionStatus(str, Enum):
    OPEN = "open"
    ANSWERED = "answered"


class AccomplishmentKind(str, Enum):
    MILESTONE = "milestone"
    ARTIFACT = "artifact"
    DEMO = "demo"
    LESSON = "lesson"
    RELEASE = "release"


class RelationKind(str, Enum):
    SUCCESSOR = "successor"
    PREDECESSOR = "predecessor"
    DUPLICATE = "duplicate"
    COMPONENT = "component"
    PART_OF = "part_of"
    RELATED = "related"

    def inverse(self) -> "RelationKind":
        """The edge implied on the target project.

        Symmetric kinds invert to themselves; directional kinds pair up.
        """
        return _INVERSE_RELATIONS[self]

    @property
    def directional(self) -> bool:
        return self.inverse() is not self


_INVERSE_RELATIONS = {
    RelationKind.SUCCESSOR: RelationKind.PREDECESSOR,
    RelationKind.PREDECESSOR: RelationKind.SUCCESSOR,
    RelationKind.COMPONENT: RelationKind.PART_OF,
    RelationKind.PART_OF: RelationKind.COMPONENT,
    RelationKind.DUPLICATE: RelationKind.DUPLICATE,
    RelationKind.RELATED: RelationKind.RELATED,
}

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
REPO_PATTERN = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")


def _enum(value: Any, enum_cls: type[Enum], field_name: str, project_id: str):
    if value is None:
        return None
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value))
    except ValueError:
        allowed = ", ".join(m.value for m in enum_cls)
        raise RegistryError(
            f"{project_id}: {field_name} must be one of [{allowed}], got {value!r}"
        ) from None


def _date(value: Any, field_name: str, project_id: str) -> dt.date | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError:
        raise RegistryError(
            f"{project_id}: {field_name} must be an ISO date (YYYY-MM-DD), got {value!r}"
        ) from None


def _str_list(value: Any, field_name: str, project_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raise RegistryError(f"{project_id}: {field_name} must be a list, got a string")
    if not isinstance(value, list):
        raise RegistryError(f"{project_id}: {field_name} must be a list")
    return [str(item) for item in value]


def _clean(value: Any) -> str | None:
    """Normalize an optional free-text field; blank strings become None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _unknown_keys(
    raw: dict[str, Any], allowed: set[str], field_name: str, project_id: str
) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise RegistryError(
            f"{project_id}: unknown {field_name} field(s): "
            f"{', '.join(sorted(unknown))}"
        )


def _positive_int(value: Any, field_name: str, project_id: str) -> int:
    if isinstance(value, bool):
        raise RegistryError(f"{project_id}: {field_name} must be an integer >= 1")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise RegistryError(
            f"{project_id}: {field_name} must be an integer >= 1, got {value!r}"
        ) from None
    if parsed < 1 or (isinstance(value, float) and not value.is_integer()):
        raise RegistryError(
            f"{project_id}: {field_name} must be an integer >= 1, got {value!r}"
        )
    return parsed


@dataclass
class OpenDecision:
    question: str
    status: OpenDecisionStatus = OpenDecisionStatus.OPEN
    answer: str | None = None
    source_fields: frozenset[str] = field(default_factory=frozenset, repr=False)

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "OpenDecision":
        if not isinstance(raw, dict):
            raise RegistryError(
                f"{project_id}: each brief.open_decisions item must be a mapping"
            )
        _unknown_keys(raw, {"question", "status", "answer"}, "open decision", project_id)
        question = _clean(raw.get("question"))
        if not question:
            raise RegistryError(
                f"{project_id}: brief.open_decisions.question is required"
            )
        status = _enum(
            raw.get("status", OpenDecisionStatus.OPEN),
            OpenDecisionStatus,
            "brief.open_decisions.status",
            project_id,
        )
        return cls(
            question=question,
            status=status or OpenDecisionStatus.OPEN,
            answer=_clean(raw.get("answer")),
            source_fields=frozenset(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"question": self.question}
        if self.status is not OpenDecisionStatus.OPEN or "status" in self.source_fields:
            out["status"] = self.status.value
        if self.answer is not None or "answer" in self.source_fields:
            out["answer"] = self.answer
        return out


@dataclass
class Brief:
    done_criteria: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    open_decisions: list[OpenDecision] = field(default_factory=list)
    reviewed: dt.date | None = None
    source_fields: frozenset[str] = field(default_factory=frozenset, repr=False)

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "Brief":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: brief must be a mapping")
        _unknown_keys(
            raw,
            {"done_criteria", "non_goals", "constraints", "open_decisions", "reviewed"},
            "brief",
            project_id,
        )
        decisions = raw.get("open_decisions")
        if decisions is None:
            decisions = []
        if not isinstance(decisions, list):
            raise RegistryError(f"{project_id}: brief.open_decisions must be a list")
        return cls(
            done_criteria=_str_list(
                raw.get("done_criteria"), "brief.done_criteria", project_id
            ),
            non_goals=_str_list(raw.get("non_goals"), "brief.non_goals", project_id),
            constraints=_str_list(
                raw.get("constraints"), "brief.constraints", project_id
            ),
            open_decisions=[OpenDecision.parse(item, project_id) for item in decisions],
            reviewed=_date(raw.get("reviewed"), "brief.reviewed", project_id),
            source_fields=frozenset(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for name in ("done_criteria", "non_goals", "constraints"):
            value = getattr(self, name)
            if value or name in self.source_fields:
                out[name] = list(value)
        if self.open_decisions or "open_decisions" in self.source_fields:
            out["open_decisions"] = [item.to_dict() for item in self.open_decisions]
        if self.reviewed is not None or "reviewed" in self.source_fields:
            out["reviewed"] = self.reviewed.isoformat() if self.reviewed else None
        return out


@dataclass
class AutomationBudget:
    chunks_per_run: int = 6
    minutes_per_run: int = 120
    source_fields: frozenset[str] = field(default_factory=frozenset, repr=False)

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "AutomationBudget":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: automation.budget must be a mapping")
        _unknown_keys(
            raw, {"chunks_per_run", "minutes_per_run"}, "automation.budget", project_id
        )
        return cls(
            chunks_per_run=_positive_int(
                raw.get("chunks_per_run", 6),
                "automation.budget.chunks_per_run",
                project_id,
            ),
            minutes_per_run=_positive_int(
                raw.get("minutes_per_run", 120),
                "automation.budget.minutes_per_run",
                project_id,
            ),
            source_fields=frozenset(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.chunks_per_run != 6 or "chunks_per_run" in self.source_fields:
            out["chunks_per_run"] = self.chunks_per_run
        if self.minutes_per_run != 120 or "minutes_per_run" in self.source_fields:
            out["minutes_per_run"] = self.minutes_per_run
        return out


@dataclass
class Automation:
    mode: AutomationMode = AutomationMode.OFF
    allow: list[ChangeClass] = field(default_factory=list)
    budget: AutomationBudget = field(default_factory=AutomationBudget)
    paused: bool = False
    source_fields: frozenset[str] = field(default_factory=frozenset, repr=False)

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "Automation":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: automation must be a mapping")
        _unknown_keys(raw, {"mode", "allow", "budget", "paused"}, "automation", project_id)
        allowed = raw.get("allow")
        if allowed is None:
            allowed = []
        if isinstance(allowed, str) or not isinstance(allowed, list):
            raise RegistryError(f"{project_id}: automation.allow must be a list")
        change_classes: list[ChangeClass] = []
        for item in allowed:
            change_class = _enum(item, ChangeClass, "automation.allow", project_id)
            if change_class is None:
                choices = ", ".join(member.value for member in ChangeClass)
                raise RegistryError(
                    f"{project_id}: automation.allow must contain only [{choices}]"
                )
            change_classes.append(change_class)
        return cls(
            mode=_enum(
                raw.get("mode", AutomationMode.OFF),
                AutomationMode,
                "automation.mode",
                project_id,
            ) or AutomationMode.OFF,
            allow=change_classes,
            budget=AutomationBudget.parse(raw.get("budget"), project_id),
            paused=bool(raw.get("paused", False)),
            source_fields=frozenset(raw),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.mode is not AutomationMode.OFF or "mode" in self.source_fields:
            out["mode"] = self.mode.value
        if self.allow or "allow" in self.source_fields:
            out["allow"] = [item.value for item in self.allow]
        budget = self.budget.to_dict()
        if budget or "budget" in self.source_fields:
            out["budget"] = budget
        if self.paused or "paused" in self.source_fields:
            out["paused"] = self.paused
        return out


@dataclass
class NextAction:
    """The single clearest next step for a project (US-005).

    Modelled as one optional object rather than a list so that "exactly one"
    is expressed in the type instead of being checked after the fact.
    """

    description: str
    reviewed: dt.date | None = None
    link: str | None = None

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "NextAction":
        if isinstance(raw, str):
            raise RegistryError(
                f"{project_id}: next_action must be a mapping with a 'description' "
                "field, not a bare string"
            )
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: next_action must be a mapping")
        description = _clean(raw.get("description"))
        if not description:
            raise RegistryError(f"{project_id}: next_action.description is required")
        return cls(
            description=description,
            reviewed=_date(raw.get("reviewed"), "next_action.reviewed", project_id),
            link=_clean(raw.get("link")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"description": self.description}
        if self.reviewed:
            out["reviewed"] = self.reviewed.isoformat()
        if self.link:
            out["link"] = self.link
        return out


@dataclass
class Accomplishment:
    """A dated thing the project produced (US-003)."""

    date: dt.date
    summary: str
    kind: AccomplishmentKind = AccomplishmentKind.MILESTONE
    links: list[str] = field(default_factory=list)
    #: Explicit opt-in for public output. Private by default: an accomplishment
    #: may reference private issues, clients, or unreleased work (US-009).
    public: bool = False

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "Accomplishment":
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: each accomplishment must be a mapping")
        date = _date(raw.get("date"), "accomplishment.date", project_id)
        if date is None:
            raise RegistryError(f"{project_id}: accomplishment.date is required")
        summary = _clean(raw.get("summary"))
        if not summary:
            raise RegistryError(f"{project_id}: accomplishment.summary is required")
        kind = _enum(
            raw.get("kind", AccomplishmentKind.MILESTONE),
            AccomplishmentKind,
            "accomplishment.kind",
            project_id,
        )
        return cls(
            date=date,
            summary=summary,
            kind=kind or AccomplishmentKind.MILESTONE,
            links=_str_list(raw.get("links"), "accomplishment.links", project_id),
            public=bool(raw.get("public", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "date": self.date.isoformat(),
            "kind": self.kind.value,
            "summary": self.summary,
        }
        if self.links:
            out["links"] = list(self.links)
        if self.public:
            out["public"] = True
        return out


@dataclass
class Relationship:
    """A directed edge to another project (US-007)."""

    kind: RelationKind
    target: str
    note: str | None = None

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "Relationship":
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: each relationship must be a mapping")
        kind = _enum(raw.get("kind"), RelationKind, "relationship.kind", project_id)
        if kind is None:
            raise RegistryError(f"{project_id}: relationship.kind is required")
        target = _clean(raw.get("target"))
        if not target:
            raise RegistryError(f"{project_id}: relationship.target is required")
        return cls(kind=kind, target=target, note=_clean(raw.get("note")))

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"kind": self.kind.value, "target": self.target}
        if self.note:
            out["note"] = self.note
        return out


@dataclass
class Descriptions:
    """Private and public-safe prose, deliberately kept apart (US-002, US-009).

    No code path copies `private` into `public_safe`. A public export reads
    `public_safe` only, so a private detail cannot leak by omission.
    """

    private: str | None = None
    public_safe: str | None = None

    @classmethod
    def parse(cls, raw: Any, project_id: str) -> "Descriptions":
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise RegistryError(f"{project_id}: descriptions must be a mapping")
        return cls(
            private=_clean(raw.get("private")),
            public_safe=_clean(raw.get("public_safe")),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.private:
            out["private"] = self.private
        if self.public_safe:
            out["public_safe"] = self.public_safe
        return out


@dataclass
class Project:
    """One registered project.

    A project need not have a repository: intentional non-repository work is a
    first-class entry (US-001).
    """

    id: str
    name: str
    purpose: str | None = None
    desired_outcome: str | None = None
    brief: Brief = field(default_factory=Brief)
    lifecycle: Lifecycle = Lifecycle.INCUBATING
    active: bool = False
    needs_review: bool = False
    category: str | None = None
    priority: Priority | None = None
    effort: Effort | None = None
    horizon: Horizon | None = None
    blocked_by: str | None = None
    automation: Automation = field(default_factory=Automation)
    repo: str | None = None
    is_fork: bool = False
    visibility: Visibility = Visibility.PRIVATE
    last_reviewed: dt.date | None = None
    next_action: NextAction | None = None
    accomplishments: list[Accomplishment] = field(default_factory=list)
    relationships: list[Relationship] = field(default_factory=list)
    descriptions: Descriptions = field(default_factory=Descriptions)
    public: bool = False
    showcase_order: int | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None
    #: Path the project was loaded from, set by storage. Not part of the schema.
    source_path: str | None = None
    #: Top-level YAML keys that were explicitly present. Not part of the schema.
    source_fields: frozenset[str] = field(default_factory=frozenset, repr=False)
    #: Original falsy values, retained so automated rewrites preserve intent.
    source_falsy_values: dict[str, Any] = field(
        default_factory=dict, repr=False, compare=False
    )

    KNOWN_FIELDS = frozenset(
        {
            "id", "name", "purpose", "desired_outcome", "brief", "lifecycle", "active",
            "needs_review", "category", "priority", "effort", "horizon",
            "blocked_by", "automation", "repo", "is_fork", "visibility", "last_reviewed",
            "next_action", "accomplishments", "relationships", "descriptions",
            "public", "showcase_order", "tags", "notes",
        }
    )

    # -- parsing ---------------------------------------------------------

    @classmethod
    def parse(cls, raw: Any, source_path: str | None = None) -> "Project":
        if not isinstance(raw, dict):
            raise RegistryError(f"{source_path or '<data>'}: project must be a mapping")

        project_id = _clean(raw.get("id"))
        if not project_id:
            raise RegistryError(f"{source_path or '<data>'}: id is required")
        if not ID_PATTERN.match(project_id):
            raise RegistryError(
                f"{project_id}: id must be a lowercase slug matching {ID_PATTERN.pattern}"
            )

        unknown = set(raw) - cls.KNOWN_FIELDS
        if unknown:
            raise RegistryError(
                f"{project_id}: unknown field(s): {', '.join(sorted(unknown))}"
            )

        name = _clean(raw.get("name")) or project_id

        repo = _clean(raw.get("repo"))
        if repo and not REPO_PATTERN.match(repo):
            raise RegistryError(
                f"{project_id}: repo must be in 'owner/name' form, got {repo!r}"
            )

        showcase_order = raw.get("showcase_order")
        if showcase_order is not None:
            try:
                showcase_order = int(showcase_order)
            except (TypeError, ValueError):
                raise RegistryError(
                    f"{project_id}: showcase_order must be an integer"
                ) from None

        accomplishments = [
            Accomplishment.parse(item, project_id)
            for item in (raw.get("accomplishments") or [])
        ]
        # Newest first, so "what did I do recently" reads off the top.
        accomplishments.sort(key=lambda a: a.date, reverse=True)

        next_action_raw = raw.get("next_action")

        return cls(
            id=project_id,
            name=name,
            purpose=_clean(raw.get("purpose")),
            desired_outcome=_clean(raw.get("desired_outcome")),
            brief=Brief.parse(raw.get("brief"), project_id),
            lifecycle=_enum(
                raw.get("lifecycle", Lifecycle.INCUBATING),
                Lifecycle, "lifecycle", project_id,
            ) or Lifecycle.INCUBATING,
            active=bool(raw.get("active", False)),
            needs_review=bool(raw.get("needs_review", False)),
            category=_clean(raw.get("category")),
            priority=_enum(raw.get("priority"), Priority, "priority", project_id),
            effort=_enum(raw.get("effort"), Effort, "effort", project_id),
            horizon=_enum(raw.get("horizon"), Horizon, "horizon", project_id),
            blocked_by=_clean(raw.get("blocked_by")),
            automation=Automation.parse(raw.get("automation"), project_id),
            repo=repo,
            is_fork=bool(raw.get("is_fork", False)),
            visibility=_enum(
                raw.get("visibility", Visibility.PRIVATE),
                Visibility, "visibility", project_id,
            ) or Visibility.PRIVATE,
            last_reviewed=_date(raw.get("last_reviewed"), "last_reviewed", project_id),
            next_action=(
                NextAction.parse(next_action_raw, project_id)
                if next_action_raw not in (None, "")
                else None
            ),
            accomplishments=accomplishments,
            relationships=[
                Relationship.parse(item, project_id)
                for item in (raw.get("relationships") or [])
            ],
            descriptions=Descriptions.parse(raw.get("descriptions"), project_id),
            public=bool(raw.get("public", False)),
            showcase_order=showcase_order,
            tags=_str_list(raw.get("tags"), "tags", project_id),
            notes=_clean(raw.get("notes")),
            source_path=source_path,
            source_fields=frozenset(raw),
            source_falsy_values={key: value for key, value in raw.items() if not value},
        )

    # -- serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to plain data, omitting empty optional fields.

        Field order matches the documented schema so rewritten files stay
        readable and diffs stay small.
        """
        out: dict[str, Any] = {"id": self.id, "name": self.name}
        if self._include("purpose", self.purpose):
            out["purpose"] = self.purpose or self._source_falsy("purpose", "")
        if self._include("desired_outcome", self.desired_outcome):
            out["desired_outcome"] = self.desired_outcome or self._source_falsy(
                "desired_outcome", ""
            )
        brief = self.brief.to_dict()
        if self._include("brief", brief):
            out["brief"] = brief
        out["lifecycle"] = self.lifecycle.value
        out["active"] = self.active
        if self._include("needs_review", self.needs_review):
            out["needs_review"] = self.needs_review
        if self._include("category", self.category):
            out["category"] = self.category or self._source_falsy("category", "")
        if self._include("priority", self.priority):
            out["priority"] = (
                self.priority.value
                if self.priority
                else self._source_falsy("priority", None)
            )
        if self._include("effort", self.effort):
            out["effort"] = (
                self.effort.value
                if self.effort
                else self._source_falsy("effort", None)
            )
        if self._include("horizon", self.horizon):
            out["horizon"] = (
                self.horizon.value
                if self.horizon
                else self._source_falsy("horizon", None)
            )
        if self._include("blocked_by", self.blocked_by):
            out["blocked_by"] = self.blocked_by or self._source_falsy("blocked_by", "")
        automation = self.automation.to_dict()
        if self._include("automation", automation):
            out["automation"] = automation
        if self._include("repo", self.repo):
            out["repo"] = self.repo or self._source_falsy("repo", "")
        if self._include("is_fork", self.is_fork):
            out["is_fork"] = self.is_fork
        out["visibility"] = self.visibility.value
        if self._include("last_reviewed", self.last_reviewed):
            out["last_reviewed"] = (
                self.last_reviewed.isoformat()
                if self.last_reviewed
                else self._source_falsy("last_reviewed", None)
            )
        if self._include("next_action", self.next_action):
            out["next_action"] = (
                self.next_action.to_dict()
                if self.next_action
                else self._source_falsy("next_action", "")
            )
        if self._include("accomplishments", self.accomplishments):
            out["accomplishments"] = (
                [a.to_dict() for a in self.accomplishments]
                if self.accomplishments
                else self._source_falsy("accomplishments", [])
            )
        if self._include("relationships", self.relationships):
            out["relationships"] = (
                [r.to_dict() for r in self.relationships]
                if self.relationships
                else self._source_falsy("relationships", [])
            )
        descriptions = self.descriptions.to_dict()
        if self._include("descriptions", descriptions):
            out["descriptions"] = descriptions or self._source_falsy(
                "descriptions", {}
            )
        if self._include("public", self.public):
            out["public"] = self.public
        if self.showcase_order is not None or "showcase_order" in self.source_fields:
            out["showcase_order"] = (
                self.showcase_order
                if self.showcase_order is not None
                else self._source_falsy("showcase_order", None)
            )
        if self._include("tags", self.tags):
            out["tags"] = (
                list(self.tags) if self.tags else self._source_falsy("tags", [])
            )
        if self._include("notes", self.notes):
            out["notes"] = self.notes or self._source_falsy("notes", "")
        return out

    def _include(self, name: str, value: Any) -> bool:
        return bool(value) or name in self.source_fields

    def _source_falsy(self, name: str, default: Any) -> Any:
        if name in self.source_falsy_values:
            return self.source_falsy_values[name]
        return default

    # -- derived views ---------------------------------------------------

    @property
    def source(self) -> str:
        """`repo` for repository-backed projects, `non_repo` otherwise (US-001)."""
        return "repo" if self.repo else "non_repo"

    def summary(self) -> dict[str, Any]:
        """The six fields every portfolio listing must show (US-001)."""
        return {
            "id": self.id,
            "name": self.name,
            "purpose": self.purpose,
            "lifecycle": self.lifecycle.value,
            "active": self.active,
            "visibility": self.visibility.value,
            "last_reviewed": (
                self.last_reviewed.isoformat() if self.last_reviewed else None
            ),
        }

    def accomplishment_summary(self, limit: int = 3) -> list[str]:
        """Condensed accomplishments for dashboard and portfolio views (US-003)."""
        return [
            f"{a.date.isoformat()} — {a.summary}"
            for a in self.accomplishments[:limit]
        ]

    def brief_gaps(self) -> list[str]:
        """Missing owner intent that prevents build or shadow automation."""
        gaps: list[str] = []
        if not self.purpose:
            gaps.append("purpose")
        if not self.desired_outcome:
            gaps.append("desired_outcome")
        if not self.brief.done_criteria:
            gaps.append("brief.done_criteria")
        if not self.repo:
            gaps.append("repo")
        gaps.extend(
            f"open_decision: {decision.question}"
            for decision in self.brief.open_decisions
            if decision.status is OpenDecisionStatus.OPEN
        )
        return gaps

    def relationships_of_kind(self, kind: RelationKind) -> list[Relationship]:
        return [r for r in self.relationships if r.kind is kind]

    def days_since_review(self, today: dt.date) -> int | None:
        if self.last_reviewed is None:
            return None
        return (today - self.last_reviewed).days

    def search_text(self) -> str:
        parts: list[str] = [self.id, self.name]
        for value in (
            self.purpose, self.desired_outcome, self.category, self.notes,
            self.descriptions.private, self.descriptions.public_safe,
        ):
            if value:
                parts.append(value)
        parts.extend(self.brief.done_criteria)
        parts.extend(self.brief.non_goals)
        parts.extend(self.brief.constraints)
        for decision in self.brief.open_decisions:
            parts.append(decision.question)
            if decision.answer:
                parts.append(decision.answer)
        parts.extend(self.tags)
        if self.next_action:
            parts.append(self.next_action.description)
        parts.extend(a.summary for a in self.accomplishments)
        if self.repo:
            parts.append(self.repo)
        return "\n".join(parts).lower()


def name_tokens(text: str) -> frozenset[str]:
    """Normalized tokens used for the overlap heuristic (operating rule 4)."""
    words = re.split(r"[^a-z0-9]+", text.lower())
    return frozenset(w for w in words if len(w) > 2)


def sort_projects(projects: Iterable[Project]) -> list[Project]:
    """Stable ordering: lifecycle importance, then name."""
    order = {lc: i for i, lc in enumerate(Lifecycle)}
    return sorted(projects, key=lambda p: (order[p.lifecycle], p.name.lower()))
