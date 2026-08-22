"""Core operating rules as executable checks.

Findings are split into two severities:

* ``error``      -- the registry is provably inconsistent with its own rules.
* ``suggestion`` -- worth a human look; never blocks and never auto-applies.

Nothing in this module mutates a project. It reports; the owner decides
(operating rules 6 and 8).
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator

from .model import (
    AutomationMode,
    Lifecycle,
    NORMALLY_ACTIVE,
    OpenDecisionStatus,
    Project,
    RelationKind,
    name_tokens,
)
from .storage import Registry

ERROR = "error"
SUGGESTION = "suggestion"


@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: str
    message: str
    project_id: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "project_id": self.project_id,
            "message": self.message,
            "hint": self.hint,
        }

    def render(self) -> str:
        where = f"[{self.project_id}] " if self.project_id else ""
        line = f"{self.severity.upper():10} {where}{self.message} ({self.rule_id})"
        if self.hint:
            line += f"\n{' ' * 11}hint: {self.hint}"
        return line


@dataclass
class ValidationConfig:
    #: Operating rule: keep `now` very small.
    max_now: int = 3
    #: Active projects reviewed less recently than this are flagged.
    review_warning_days: int = 45
    minimum_next_action_length: int = 8


#: Next actions that do not name an outcome or a decision.
VAGUE_NEXT_ACTIONS = frozenset(
    {
        "todo", "tbd", "continue", "keep going", "work on it", "finish it",
        "more work", "next steps", "improve", "fix", "cleanup", "clean up",
        "continue work", "keep working", "misc",
    }
)

#: Patterns that must never appear in curated text (safety boundary: the
#: registry never stores credential values).
CREDENTIAL_PATTERNS = (
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("inline_secret", re.compile(r"(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*\S{12,}")),
)


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == ERROR]

    @property
    def suggestions(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == SUGGESTION]

    @property
    def ok(self) -> bool:
        return not self.errors

    def for_project(self, project_id: str) -> list[Finding]:
        return [f for f in self.findings if f.project_id == project_id]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error_count": len(self.errors),
            "suggestion_count": len(self.suggestions),
            "findings": [f.to_dict() for f in self.findings],
        }


@dataclass
class RuleContext:
    registry: Registry
    today: dt.date
    config: ValidationConfig


Rule = Callable[[RuleContext], Iterable[Finding]]
RULES: list[Rule] = []


def rule(func: Rule) -> Rule:
    RULES.append(func)
    return func


# -- US-002: purpose ------------------------------------------------------


@rule
def rule_purpose_present(ctx: RuleContext) -> Iterator[Finding]:
    """Every registered project must have a purpose (operating rule 1).

    `needs_review` is the sanctioned placeholder for an imported repository
    whose purpose has not been written yet, so it downgrades to a suggestion.
    """
    for project in ctx.registry:
        if project.purpose:
            continue
        if project.needs_review:
            yield Finding(
                "purpose_pending", SUGGESTION,
                "marked needs_review and has no purpose yet",
                project.id,
                hint="Write a one-line purpose, then clear needs_review.",
            )
        else:
            yield Finding(
                "purpose_missing", ERROR,
                "has no purpose and is not marked needs_review",
                project.id,
                hint="Add `purpose:` or set `needs_review: true`.",
            )


# -- US-005: next actions -------------------------------------------------


@rule
def rule_active_needs_next_action(ctx: RuleContext) -> Iterator[Finding]:
    """Every active project must have exactly one next action (operating rule 2)."""
    for project in ctx.registry:
        if project.active and project.next_action is None:
            yield Finding(
                "next_action_missing", ERROR,
                "is active but has no next action",
                project.id,
                hint="Add `next_action.description`, or set `active: false`.",
            )


@rule
def rule_next_action_is_concrete(ctx: RuleContext) -> Iterator[Finding]:
    """A next action should name an outcome or a decision, not a mood."""
    for project in ctx.registry:
        action = project.next_action
        if action is None:
            continue
        text = action.description.strip().lower().rstrip(".")
        if text in VAGUE_NEXT_ACTIONS or len(text) < ctx.config.minimum_next_action_length:
            yield Finding(
                "next_action_vague", SUGGESTION,
                f"next action {action.description!r} does not name an outcome or decision",
                project.id,
                hint="Phrase it as the result you want, e.g. 'Decide whether to merge X'.",
            )


@rule
def rule_next_action_reviewed(ctx: RuleContext) -> Iterator[Finding]:
    """A next action records when it was last reviewed (US-005)."""
    for project in ctx.registry:
        if project.next_action and project.next_action.reviewed is None:
            yield Finding(
                "next_action_unreviewed", ERROR,
                "next action has no `reviewed` date",
                project.id,
                hint="Add `next_action.reviewed: YYYY-MM-DD`.",
            )
        elif project.next_action and project.next_action.reviewed > ctx.today:
            yield Finding(
                "next_action_future_review", ERROR,
                "next action `reviewed` date is in the future",
                project.id,
            )


# -- US-004: lifecycle and activity ---------------------------------------


@rule
def rule_now_requires_outcome(ctx: RuleContext) -> Iterator[Finding]:
    """A project cannot be `now` without naming what moves it out (rule 3)."""
    for project in ctx.registry:
        if project.lifecycle is Lifecycle.NOW and not project.desired_outcome:
            yield Finding(
                "now_without_outcome", ERROR,
                "is in `now` without a desired_outcome",
                project.id,
                hint="Name the outcome that would move this out of `now`.",
            )


@rule
def rule_now_capacity(ctx: RuleContext) -> Iterator[Finding]:
    """Keep the `now` list very small (US-004)."""
    in_now = [p for p in ctx.registry if p.lifecycle is Lifecycle.NOW]
    if len(in_now) > ctx.config.max_now:
        names = ", ".join(p.id for p in in_now)
        yield Finding(
            "now_overloaded", SUGGESTION,
            f"{len(in_now)} projects are in `now` (soft limit {ctx.config.max_now}): {names}",
            hint="Move some to `next`, or accept the overload deliberately.",
        )


@rule
def rule_lifecycle_activity_coherence(ctx: RuleContext) -> Iterator[Finding]:
    """Unusual lifecycle/active combinations are surfaced, never corrected.

    Lifecycle and activity are separate signals precisely so exceptions can be
    expressed, so every finding here is a suggestion.
    """
    for project in ctx.registry:
        if project.active and project.lifecycle in {Lifecycle.ARCHIVED, Lifecycle.SUPERSEDED}:
            yield Finding(
                "active_but_closed", SUGGESTION,
                f"is marked active while lifecycle is `{project.lifecycle.value}`",
                project.id,
            )
        elif not project.active and project.lifecycle in NORMALLY_ACTIVE:
            yield Finding(
                "inactive_but_committed", SUGGESTION,
                f"is not active while lifecycle is `{project.lifecycle.value}`",
                project.id,
            )


@rule
def rule_active_recently_reviewed(ctx: RuleContext) -> Iterator[Finding]:
    """Active projects should carry a recent review date (US-005, US-008)."""
    for project in ctx.registry:
        if not project.active:
            continue
        if project.last_reviewed is None:
            yield Finding(
                "active_never_reviewed", SUGGESTION,
                "is active but has never recorded a review",
                project.id,
                hint="Run `registry record-review <id>` after your next look.",
            )
            continue
        days = project.days_since_review(ctx.today) or 0
        if days > ctx.config.review_warning_days:
            yield Finding(
                "active_review_stale", SUGGESTION,
                f"is active but was last reviewed {days} days ago",
                project.id,
            )


# -- automation readiness -------------------------------------------------


@rule
def rule_brief_complete_for_build(ctx: RuleContext) -> Iterator[Finding]:
    """Build and shadow modes require complete, owner-curated intent."""
    automated_modes = {AutomationMode.BUILD, AutomationMode.SHADOW}
    for project in ctx.registry:
        if project.automation.mode not in automated_modes:
            continue
        gaps = project.brief_gaps()
        if gaps:
            yield Finding(
                "brief_incomplete_for_build",
                ERROR,
                f"automation mode `{project.automation.mode.value}` requires a complete "
                f"brief; missing: {', '.join(gaps)}",
                project.id,
                hint="Fill every listed brief gap, or turn automation mode off.",
            )


@rule
def rule_automation_has_focus_lifecycle(ctx: RuleContext) -> Iterator[Finding]:
    """Enabled automation should stay attached to a focus lifecycle."""
    enabled_modes = {
        AutomationMode.BUILD,
        AutomationMode.SHADOW,
        AutomationMode.SPEC_ONLY,
    }
    focus_lifecycles = {Lifecycle.NOW, Lifecycle.NEXT, Lifecycle.MAINTAINED}
    for project in ctx.registry:
        if (
            project.automation.mode in enabled_modes
            and project.lifecycle not in focus_lifecycles
        ):
            yield Finding(
                "automation_without_focus_lifecycle",
                SUGGESTION,
                f"automation mode `{project.automation.mode.value}` is enabled while "
                f"lifecycle is `{project.lifecycle.value}`",
                project.id,
                hint="Move the project to now, next, or maintained, or disable automation.",
            )


@rule
def rule_open_decisions_answered(ctx: RuleContext) -> Iterator[Finding]:
    """Open intent decisions remain visible when they are not already build blockers."""
    blocking_modes = {AutomationMode.BUILD, AutomationMode.SHADOW}
    for project in ctx.registry:
        if project.automation.mode in blocking_modes:
            continue
        questions = [
            decision.question
            for decision in project.brief.open_decisions
            if decision.status is OpenDecisionStatus.OPEN
        ]
        if questions:
            yield Finding(
                "open_decision_unanswered",
                SUGGESTION,
                f"has unanswered open decision(s): {', '.join(questions)}",
                project.id,
                hint="Answer the decision or mark it answered when resolved.",
            )


# -- US-007: relationships ------------------------------------------------


@rule
def rule_relationships_resolve(ctx: RuleContext) -> Iterator[Finding]:
    """Relationship targets must exist, and nothing relates to itself."""
    for project in ctx.registry:
        for relationship in project.relationships:
            if relationship.target == project.id:
                yield Finding(
                    "relationship_self_reference", ERROR,
                    f"declares a `{relationship.kind.value}` relationship to itself",
                    project.id,
                )
            elif relationship.target not in ctx.registry:
                yield Finding(
                    "relationship_broken", ERROR,
                    f"`{relationship.kind.value}` target {relationship.target!r} "
                    "is not a registered project",
                    project.id,
                    hint="Fix the id, or register the missing project.",
                )


@rule
def rule_superseded_names_successor(ctx: RuleContext) -> Iterator[Finding]:
    """A superseded project must name its successor (operating rule 5)."""
    for project in ctx.registry:
        if project.lifecycle is not Lifecycle.SUPERSEDED:
            continue
        if not project.relationships_of_kind(RelationKind.SUCCESSOR):
            yield Finding(
                "superseded_without_successor", ERROR,
                "is `superseded` but names no successor",
                project.id,
                hint="Add a relationship of kind `successor`.",
            )


@rule
def rule_overlap_check(ctx: RuleContext) -> Iterator[Finding]:
    """New projects should be checked against existing ones (operating rule 4).

    Two heuristics: an identical repository, or a shared normalized name token
    set. Either one is only reported when neither project already declares a
    `duplicate` or `related` edge to the other.
    """
    projects = list(ctx.registry)
    for i, left in enumerate(projects):
        for right in projects[i + 1:]:
            if _declares_overlap(left, right) or _declares_overlap(right, left):
                continue
            if left.repo and right.repo and left.repo.lower() == right.repo.lower():
                yield Finding(
                    "overlap_same_repo", SUGGESTION,
                    f"{left.id} and {right.id} both point at {left.repo}",
                    left.id,
                    hint="Mark one a `duplicate` of the other, or correct the repo.",
                )
                continue
            shared = name_tokens(left.name) & name_tokens(right.name)
            if shared and shared == name_tokens(left.name) == name_tokens(right.name):
                yield Finding(
                    "overlap_similar_name", SUGGESTION,
                    f"{left.id} and {right.id} have the same name tokens "
                    f"({', '.join(sorted(shared))})",
                    left.id,
                    hint="Relate or consolidate them if they are the same effort.",
                )


def _declares_overlap(source: Project, target: Project) -> bool:
    return any(
        r.target == target.id and r.kind in {RelationKind.DUPLICATE, RelationKind.RELATED}
        for r in source.relationships
    )


# -- US-009: public safety ------------------------------------------------


@rule
def rule_public_requires_public_safe(ctx: RuleContext) -> Iterator[Finding]:
    """A project opted into the portfolio needs a public-safe description."""
    for project in ctx.registry:
        if project.public and not project.descriptions.public_safe:
            yield Finding(
                "public_without_public_safe", ERROR,
                "is marked public but has no `descriptions.public_safe`",
                project.id,
                hint="Write a public-safe summary, or set `public: false`.",
            )


@rule
def rule_showcase_order_unique(ctx: RuleContext) -> Iterator[Finding]:
    seen: dict[int, str] = {}
    for project in ctx.registry:
        if project.showcase_order is None or not project.public:
            continue
        if project.showcase_order in seen:
            yield Finding(
                "showcase_order_collision", SUGGESTION,
                f"shares showcase_order {project.showcase_order} with "
                f"{seen[project.showcase_order]}",
                project.id,
            )
        else:
            seen[project.showcase_order] = project.id


@rule
def rule_no_credentials(ctx: RuleContext) -> Iterator[Finding]:
    """The registry never stores credential values (safety boundary)."""
    for project in ctx.registry:
        text = project.search_text()
        for label, pattern in CREDENTIAL_PATTERNS:
            if pattern.search(text):
                yield Finding(
                    "credential_material", ERROR,
                    f"contains text matching a {label} pattern",
                    project.id,
                    hint="Remove the value and rotate it. The registry stores no secrets.",
                )
                break


# -- entry point ----------------------------------------------------------

_SEVERITY_ORDER = {ERROR: 0, SUGGESTION: 1}


def validate(
    registry: Registry,
    today: dt.date | None = None,
    config: ValidationConfig | None = None,
) -> ValidationReport:
    ctx = RuleContext(
        registry=registry,
        today=today or dt.date.today(),
        config=config or ValidationConfig(),
    )
    findings: list[Finding] = []
    for rule_func in RULES:
        findings.extend(rule_func(ctx))
    findings.sort(
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.project_id or "", f.rule_id)
    )
    return ValidationReport(findings=findings)
