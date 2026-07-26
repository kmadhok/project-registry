"""Public-safe portfolio export (US-009).

The export is built by **allowlist**: it starts from an empty record and copies
in named fields, rather than starting from the project and stripping private
ones. A field added to the schema later is therefore private until someone
deliberately adds it here.

Three independent gates must all pass before anything about a project is
emitted:

1. ``public: true`` on the project;
2. a non-empty ``descriptions.public_safe``;
3. per-item opt-in for anything finer-grained (accomplishments), and a public
   repository on GitHub before the repository name is named at all.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .github.snapshot import Snapshot
from .model import Project
from .storage import Registry

#: The complete set of keys an exported record may contain. Anything not named
#: here never reaches a public output.
PUBLIC_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "summary",
    "lifecycle",
    "category",
    "tags",
    "showcase_order",
    "highlights",
    "repo",
    "url",
)


def is_publishable(project: Project) -> bool:
    return bool(project.public and project.descriptions.public_safe)


def export_portfolio(
    registry: Registry,
    snapshot: Snapshot | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Build the public-safe portfolio document.

    Ordering is human-curated: `showcase_order` first, then name. Activity,
    recency, and any other observed signal are deliberately not used.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    snapshot = snapshot or Snapshot()

    records = [
        _export_project(project, snapshot)
        for project in registry
        if is_publishable(project)
    ]
    records.sort(
        key=lambda r: (
            r.get("showcase_order") if r.get("showcase_order") is not None else 10_000,
            r["name"].lower(),
        )
    )

    return {
        "generated_at": now.isoformat(),
        "project_count": len(records),
        "projects": records,
    }


def _export_project(project: Project, snapshot: Snapshot) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": project.id,
        "name": project.name,
        # The public-safe description only. `purpose`, `notes`, `blocked_by`
        # and `descriptions.private` are never consulted here.
        "summary": project.descriptions.public_safe,
        "lifecycle": project.lifecycle.value,
    }
    if project.category:
        record["category"] = project.category
    if project.tags:
        record["tags"] = list(project.tags)
    if project.showcase_order is not None:
        record["showcase_order"] = project.showcase_order

    highlights = [
        {"date": a.date.isoformat(), "summary": a.summary, "kind": a.kind.value}
        for a in project.accomplishments
        if a.public
    ]
    if highlights:
        record["highlights"] = highlights

    # A repository name is itself private information. It is emitted only when
    # GitHub confirms the repository is public in the latest snapshot; with no
    # snapshot the answer is "no", not "probably fine".
    state = snapshot.get(project.repo)
    if project.repo and state is not None and not state.private:
        record["repo"] = project.repo
        if state.url:
            record["url"] = state.url

    unknown = set(record) - set(PUBLIC_FIELDS)
    if unknown:  # pragma: no cover - guards against a future field slipping in
        raise AssertionError(
            f"portfolio export produced non-allowlisted field(s): {sorted(unknown)}"
        )
    return record


def render_portfolio_markdown(document: dict[str, Any]) -> str:
    """Human-readable rendering of the same allowlisted data."""
    lines = ["# Portfolio", ""]
    if not document["projects"]:
        lines.append("_No projects are marked public with a public-safe summary._")
        return "\n".join(lines) + "\n"

    for record in document["projects"]:
        title = record["name"]
        if record.get("url"):
            title = f"[{title}]({record['url']})"
        lines.append(f"## {title}")
        lines.append("")
        lines.append(record["summary"])
        lines.append("")
        meta = [f"`{record['lifecycle']}`"]
        if record.get("category"):
            meta.append(record["category"])
        if record.get("tags"):
            meta.append(" ".join(f"`{tag}`" for tag in record["tags"]))
        lines.append(" · ".join(meta))
        lines.append("")
        for highlight in record.get("highlights", []):
            lines.append(f"- {highlight['date']} — {highlight['summary']}")
        if record.get("highlights"):
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"
