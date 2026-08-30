"""Deterministic parsing and readiness checks for agent-owned ``docs/SPEC.md``."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .model import OpenDecisionStatus, Project
from .validation import ERROR, SUGGESTION


CLASS_VOCABULARY = frozenset(
    {
        "dependencies",
        "ci",
        "generated_data",
        "public_api",
        "migrations",
        "personal_data",
        "plan",
        "contract",
    }
)
ALWAYS_ALLOWED_CLASSES = frozenset({"plan", "contract"})

_SECTION = re.compile(
    r"^##\s+(Goal|Done\s+looks\s+like|Current\s+state|Remaining\s+work|Non-goals)"
    r"(?:\s+(.*?))?\s*$",
    re.I,
)
_CHECKBOX = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.*)$")
_CHECKBOX_LIKE = re.compile(r"^\s*-\s*\[")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_METADATA = re.compile(
    r"(?i)(?:^|\s)(Acceptance|Tests|Size|Classes|Verified-missing|Criteria)\s*:\s*"
)
_SIGNIFICANT_TOKEN = re.compile(r"[a-z0-9]+")
_INTENT_PHRASE = re.compile(
    r"\b(?:once\s+the\s+owner|owner\s+decides|tbd|to\s+be\s+decided)\b", re.I
)
_NEVER = re.compile(
    r"\bdeploy\b"
    r"|\bsend\s+(?:an?\s+)?(?:email|message)\b"
    r"|\b(?:pay|payment)\b"
    r"|\bdelete\s+(?:the\s+)?(?:repo(?:sitory)?|branch)\b"
    r"|\b(?:secret|credential|token)\s+value\b"
    r"|\bforce[- ]push\b"
    r"|\bmerge\s+(?:into|to)\s+(?:the\s+)?main\b",
    re.I,
)


@dataclass(frozen=True)
class SpecItem:
    index: int
    checked: bool
    title: str
    body: str
    acceptance: str | None = None
    tests: str | None = None
    size: str | None = None
    classes: list[str] = field(default_factory=list)
    verified_missing: str | None = None
    criteria: list[int] = field(default_factory=list)
    criteria_raw: str | None = None


@dataclass
class Spec:
    goal: str = ""
    done_looks_like: list[str] = field(default_factory=list)
    current_state: str = ""
    items: list[SpecItem] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SpecFinding:
    rule_id: str
    severity: str
    message: str
    index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
        }
        if self.index is not None:
            result["index"] = self.index
        return result


@dataclass(frozen=True)
class SpecItemReport:
    index: int
    title: str
    ready: bool
    problems: list[str]
    classes: list[str]
    size: str | None
    needs_intent: bool
    criteria: list[int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "title": self.title,
            "ready": self.ready,
            "problems": list(self.problems),
            "classes": list(self.classes),
            "size": self.size,
            "needs_intent": self.needs_intent,
            "criteria": list(self.criteria),
        }


@dataclass
class SpecReport:
    structure_ok: bool
    problems: list[str]
    items: list[SpecItemReport]
    findings: list[SpecFinding]
    next_ready_index: int | None
    unchecked_count: int
    checked_count: int

    @property
    def warnings(self) -> list[SpecFinding]:
        return [finding for finding in self.findings if finding.severity == SUGGESTION]

    def to_dict(self) -> dict[str, Any]:
        return {
            "structure_ok": self.structure_ok,
            "problems": list(self.problems),
            "items": [item.to_dict() for item in self.items],
            "findings": [finding.to_dict() for finding in self.findings],
            "warnings": [finding.to_dict() for finding in self.warnings],
            "next_ready_index": self.next_ready_index,
            "unchecked_count": self.unchecked_count,
            "checked_count": self.checked_count,
        }


def parse_spec(text: str) -> Spec:
    """Parse the supported SPEC headings and the Remaining work checklist."""
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        heading = _SECTION.match(line)
        if heading:
            current = _section_name(heading.group(1))
            sections.setdefault(current, [])
            if heading.group(2):
                sections[current].append(heading.group(2))
        elif current is not None:
            sections[current].append(line)

    problems: list[str] = []
    remaining = sections.get("remaining work")
    items: list[SpecItem] = []
    if remaining is None:
        problems.append("missing_remaining_work")
    else:
        chunks: list[tuple[bool, str, list[str]]] = []
        for line in remaining:
            checkbox = _CHECKBOX.match(line)
            if checkbox:
                chunks.append((checkbox.group(1).lower() == "x", checkbox.group(2), []))
            elif _CHECKBOX_LIKE.match(line):
                problems.append("malformed_checkbox")
            elif chunks:
                chunks[-1][2].append(line)
        for index, (checked, title, continuation) in enumerate(chunks, start=1):
            item_text = "\n".join([title, *continuation]).strip()
            metadata = _extract_metadata(item_text)
            classes_text = metadata.get("classes")
            classes = (
                []
                if not classes_text or classes_text.lower() == "none"
                else [
                    value.strip().lower()
                    for value in classes_text.split(",")
                    if value.strip()
                ]
            )
            criteria_raw = metadata.get("criteria") if "criteria" in metadata else None
            criteria = _parse_criteria(criteria_raw)
            items.append(
                SpecItem(
                    index=index,
                    checked=checked,
                    title=title.strip(),
                    body=item_text,
                    acceptance=metadata.get("acceptance"),
                    tests=metadata.get("tests"),
                    size=metadata.get("size"),
                    classes=classes,
                    verified_missing=metadata.get("verified-missing"),
                    criteria=criteria,
                    criteria_raw=criteria_raw,
                )
            )
        if not items:
            problems.append("no_items")

    return Spec(
        goal=_prose(sections.get("goal", [])),
        done_looks_like=_bullets(sections.get("done looks like", [])),
        current_state=_prose(sections.get("current state", [])),
        items=items,
        non_goals=_bullets(sections.get("non-goals", [])),
        problems=list(dict.fromkeys(problems)),
    )


def validate_spec(text: str, *, project: Project) -> SpecReport:
    """Validate unchecked work items against metadata, intent, and policy."""
    spec = parse_spec(text)
    reports: list[SpecItemReport] = []
    findings = [
        SpecFinding(rule_id, ERROR, f"SPEC structure failed rule {rule_id}")
        for rule_id in spec.problems
    ]
    allowed = {item.value for item in project.automation.allow}
    open_questions = [
        decision.question
        for decision in project.brief.open_decisions
        if decision.status is OpenDecisionStatus.OPEN
    ]

    covered_criteria: set[int] = set()
    has_criteria_lines = False
    criterion_count = len(project.brief.done_criteria)
    for item in spec.items:
        if item.criteria_raw is None:
            continue
        has_criteria_lines = True
        covered_criteria.update(item.criteria)
        bad_tokens = _bad_criteria_tokens(item.criteria_raw, criterion_count)
        if bad_tokens:
            findings.append(
                SpecFinding(
                    "unknown_criterion",
                    SUGGESTION,
                    f"item {item.index} has unknown criterion tokens: "
                    + ", ".join(bad_tokens),
                    item.index,
                )
            )

    if project.brief.done_criteria and has_criteria_lines:
        uncovered = [
            (index, criterion)
            for index, criterion in enumerate(project.brief.done_criteria, start=1)
            if index not in covered_criteria
        ]
        if uncovered:
            details = "; ".join(
                f"{index}: {criterion[:80]}" for index, criterion in uncovered
            )
            findings.append(
                SpecFinding(
                    "uncovered_criteria",
                    SUGGESTION,
                    f"done criteria not covered by any SPEC item: {details}",
                )
            )

    for item in spec.items:
        if item.checked:
            continue
        item_problems: list[str] = []

        def problem(rule_id: str) -> None:
            if rule_id not in item_problems:
                item_problems.append(rule_id)
                findings.append(
                    SpecFinding(
                        rule_id,
                        ERROR,
                        f"item {item.index} failed readiness rule {rule_id}",
                        item.index,
                    )
                )

        if not item.acceptance:
            problem("missing_acceptance")
        if not item.tests:
            problem("missing_tests")
        if not item.size:
            problem("missing_size")
        elif item.size.upper() not in {"S", "M"}:
            problem("size_too_large")
        if not item.verified_missing:
            problem("missing_verified_missing")
        if any(change_class not in CLASS_VOCABULARY for change_class in item.classes):
            problem("unknown_class")
        if any(
            change_class in CLASS_VOCABULARY
            and change_class not in ALWAYS_ALLOWED_CLASSES
            and change_class not in allowed
            for change_class in item.classes
        ):
            problem("blocked_by_policy")
        if _is_never_class(item.body):
            problem("never_class")

        item_tokens = _tokens(item.body)
        needs_intent = bool(_INTENT_PHRASE.search(item.body)) or any(
            len(item_tokens & _tokens(question)) >= 2 for question in open_questions
        )
        if needs_intent:
            problem("needs_intent")

        for non_goal in project.brief.non_goals:
            if len(item_tokens & _tokens(non_goal)) >= 3:
                findings.append(
                    SpecFinding(
                        "contradicts_non_goal",
                        SUGGESTION,
                        f"item {item.index} overlaps a project non-goal",
                        item.index,
                    )
                )
                break

        reports.append(
            SpecItemReport(
                index=item.index,
                title=item.title,
                ready=not item_problems,
                problems=item_problems,
                classes=list(item.classes),
                size=item.size,
                needs_intent=needs_intent,
                criteria=list(item.criteria),
            )
        )

    next_ready = next((item.index for item in reports if item.ready), None)
    return SpecReport(
        structure_ok=not spec.problems,
        problems=list(spec.problems),
        items=reports,
        findings=findings,
        next_ready_index=next_ready,
        unchecked_count=len(reports),
        checked_count=sum(item.checked for item in spec.items),
    )


def outcome_status(project: Project, spec_text: str) -> dict[str, Any]:
    """Summarize how the SPEC checklist covers the owner's done criteria."""
    spec = parse_spec(spec_text)
    report = validate_spec(spec_text, project=project)
    reports = {item.index: item for item in report.items}
    allowed = {item.value for item in project.automation.allow}
    criteria: list[dict[str, Any]] = []

    for index, text in enumerate(project.brief.done_criteria, start=1):
        items = [item for item in spec.items if index in item.criteria]
        unchecked = [item for item in items if not item.checked]
        blocked_classes: list[str] = []
        if not items:
            status = "unplanned"
        elif not unchecked:
            status = "met"
        elif any("needs_intent" in reports[item.index].problems for item in unchecked):
            status = "needs_intent"
        elif all("blocked_by_policy" in reports[item.index].problems for item in unchecked):
            status = "blocked"
            blocked_classes = sorted({
                change_class
                for item in unchecked
                for change_class in item.classes
                if change_class not in allowed
            })
        else:
            status = "in_progress"
        criteria.append({
            "index": index,
            "text": text,
            "status": status,
            "items": [item.index for item in items],
            "classes": blocked_classes,
        })

    summary = {name: 0 for name in (
        "total", "met", "in_progress", "blocked", "needs_intent", "unplanned"
    )}
    summary["total"] = len(criteria)
    for criterion in criteria:
        summary[criterion["status"]] += 1
    return {
        "project_id": project.id,
        "criteria": criteria,
        "summary": summary,
        "blocked_classes": sorted({
            change_class
            for criterion in criteria
            if criterion["status"] == "blocked"
            for change_class in criterion["classes"]
        }),
    }


def outcome_line(status: dict[str, Any]) -> str:
    """Render a compact, human-readable outcome progress line."""
    summary = status["summary"]
    segments = [f"{summary['met']}/{summary['total']} met"]
    for key, label in (
        ("in_progress", "in progress"),
        ("blocked", "blocked"),
        ("needs_intent", "need intent"),
        ("unplanned", "unplanned"),
    ):
        count = summary[key]
        if not count:
            continue
        segment = f"{count} {label}"
        if key == "blocked" and status.get("blocked_classes"):
            segment += f" ({', '.join(status['blocked_classes'])})"
        segments.append(segment)
    return "criteria: " + " · ".join(segments)


def _section_name(heading: str) -> str:
    return re.sub(r"\s+", " ", heading.strip().lower())


def _prose(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def _bullets(lines: list[str]) -> list[str]:
    result: list[str] = []
    for line in lines:
        bullet = _BULLET.match(line)
        if bullet:
            result.append(bullet.group(1).strip())
        elif result and line.strip():
            result[-1] += " " + line.strip()
    return result


def _extract_metadata(body: str) -> dict[str, str]:
    matches = list(_METADATA.finditer(body))
    result: dict[str, str] = {}
    for position, match in enumerate(matches):
        end = matches[position + 1].start() if position + 1 < len(matches) else len(body)
        value = body[match.end():end].strip()
        result[match.group(1).lower()] = " ".join(value.split())
    return result


def _criteria_tokens(raw: str) -> list[str]:
    return [token for token in re.split(r"[\s,]+", raw.strip()) if token]


def _parse_criteria(raw: str | None) -> list[int]:
    if raw is None or raw.lower() == "none":
        return []
    return [int(token) for token in _criteria_tokens(raw) if token.isdigit() and int(token) > 0]


def _bad_criteria_tokens(raw: str, criterion_count: int) -> list[str]:
    if raw.lower() == "none":
        return []
    bad: list[str] = []
    for token in _criteria_tokens(raw):
        if not token.isdigit() or int(token) <= 0 or int(token) > criterion_count:
            if token not in bad:
                bad.append(token)
    return bad


def _tokens(text: str) -> set[str]:
    return {token for token in _SIGNIFICANT_TOKEN.findall(text.lower()) if len(token) > 3}


def _is_never_class(body: str) -> bool:
    if _NEVER.search(body):
        return True
    # Closing issues or pull requests remains forbidden even when "close" and
    # the object are separated by prose ("issues ... close what is shipped").
    return bool(
        re.search(r"\bclose\b", body, re.I)
        and re.search(r"\b(?:issues?|pull requests?|prs?)\b", body, re.I)
    )
