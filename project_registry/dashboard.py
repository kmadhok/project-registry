from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any


def _escape(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _age_days(value: str | None, now: datetime) -> int | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0, (now - moment).days)


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(_escape(cell) for cell in row) + " |" for row in rows)
    return lines


def build_dashboard(registry: dict[str, Any], snapshot: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc)
    projects = registry["projects"]
    repositories = snapshot["repositories"]
    repository_by_name = {item["name_with_owner"].lower(): item for item in repositories}
    issues_by_repository = Counter(item["repository"].lower() for item in snapshot["open_issues"])
    prs_by_repository = Counter(item["repository"].lower() for item in snapshot["open_pull_requests"])
    lifecycle_counts = Counter(project.get("lifecycle") or "needs_review" for project in projects)

    lines = [
        "# Project Registry Dashboard",
        "",
        f"Generated from GitHub data refreshed at **{snapshot['generated_at']}**.",
        "",
        "> This is a private operational dashboard. GitHub activity is evidence, not priority; curated registry fields remain authoritative.",
        "",
        "## Portfolio summary",
        "",
        f"- **{len(repositories)}** GitHub repositories represented",
        f"- **{sum(1 for item in repositories if item['is_private'])}** private, **{sum(1 for item in repositories if not item['is_private'])}** public",
        f"- **{sum(1 for item in repositories if item['is_fork'])}** forks, **{sum(1 for item in repositories if item['is_archived'])}** archived on GitHub",
        f"- **{sum(1 for project in projects if project['active'])}** active "
        f"{'project' if sum(1 for project in projects if project['active']) == 1 else 'projects'}",
        f"- **{sum(1 for project in projects if project['review_status'] == 'needs_review')}** projects need owner review",
        f"- **{len(snapshot['open_pull_requests'])}** open PRs and **{len(snapshot['open_issues'])}** open issues",
        "",
        "### Lifecycle counts",
        "",
    ]
    lines.extend(_table(["Lifecycle", "Projects"], [[key, lifecycle_counts[key]] for key in sorted(lifecycle_counts)]))

    active = sorted(
        (project for project in projects if project["active"]),
        key=lambda item: (item.get("priority") or 9999, item["repository"].lower()),
    )
    lines.extend(["", "## Active projects", ""])
    if active:
        lines.extend(
            _table(
                ["Project", "Lifecycle", "Purpose", "Next action", "PRs", "Issues"],
                [
                    [
                        project["repository"], project["lifecycle"], project["purpose"],
                        project["next_action"], prs_by_repository[project["repository"].lower()],
                        issues_by_repository[project["repository"].lower()],
                    ]
                    for project in active
                ],
            )
        )
    else:
        lines.append("No projects have been intentionally marked active yet.")

    lines.extend(["", "## Open pull requests", ""])
    if snapshot["open_pull_requests"]:
        rows = []
        for pull_request in snapshot["open_pull_requests"]:
            age = _age_days(pull_request.get("created_at"), now)
            rows.append(
                [
                    pull_request["repository"],
                    f"[#{pull_request['number']}]({pull_request['url']}) {pull_request['title']}",
                    "draft" if pull_request["is_draft"] else "ready",
                    pull_request.get("review_decision"),
                    pull_request.get("checks", {}).get("state"),
                    f"{age}d" if age is not None else None,
                ]
            )
        lines.extend(_table(["Repository", "PR", "State", "Review", "Checks", "Age"], rows))
    else:
        lines.append("No open pull requests.")

    understood = [project for project in projects if project.get("understanding_path")]
    lines.extend(["", "## Repository understanding coverage", ""])
    lines.append(
        f"**{len(understood)} of {len(projects)}** projects have an evidence-backed understanding brief."
    )
    if understood:
        lines.extend(
            [""]
            + _table(
                ["Repository", "Lifecycle", "Purpose", "Brief"],
                [
                    [
                        project["repository"], project["lifecycle"], project["purpose"],
                        f"[{project['understanding_path']}]({project['understanding_path']})",
                    ]
                    for project in understood
                ],
            )
        )

    attention_rows: list[list[Any]] = []
    for project in active:
        if not project.get("next_action"):
            attention_rows.append([project["repository"], "missing-next-action", "Active project has no next action"])
    for pull_request in snapshot["open_pull_requests"]:
        age = _age_days(pull_request.get("updated_at"), now)
        checks = pull_request.get("checks", {}).get("state")
        if checks == "failing":
            attention_rows.append([pull_request["repository"], "failing-ci", f"PR #{pull_request['number']} has failing checks"])
        if pull_request.get("review_decision") == "CHANGES_REQUESTED":
            attention_rows.append([pull_request["repository"], "changes-requested", f"PR #{pull_request['number']} needs changes"])
        if age is not None and age >= 30:
            attention_rows.append([pull_request["repository"], "stale-pr", f"PR #{pull_request['number']} has not updated for {age} days"])
    for project in projects:
        if project["review_status"] == "needs_review":
            repo = repository_by_name.get(project["repository"].lower(), {})
            pushed_age = _age_days(repo.get("pushed_at"), now)
            if pushed_age is not None and pushed_age <= 90:
                attention_rows.append([project["repository"], "recent-needs-review", f"Recently pushed ({pushed_age}d) but purpose/lifecycle are not reviewed"])

    lines.extend(["", "## Attention queue", ""])
    if attention_rows:
        lines.extend(_table(["Repository", "Signal", "Reason"], attention_rows))
    else:
        lines.append("No attention signals were generated.")

    needs_review = [project for project in projects if project["review_status"] == "needs_review"]
    lines.extend(["", "## Projects awaiting owner review", ""])
    lines.extend(
        _table(
            ["Repository", "Visibility", "Fork", "Last push", "Open PRs", "Open issues"],
            [
                [
                    project["repository"],
                    repository_by_name.get(project["repository"].lower(), {}).get("visibility"),
                    repository_by_name.get(project["repository"].lower(), {}).get("is_fork"),
                    repository_by_name.get(project["repository"].lower(), {}).get("pushed_at"),
                    prs_by_repository[project["repository"].lower()],
                    issues_by_repository[project["repository"].lower()],
                ]
                for project in needs_review
            ],
        )
    )

    if snapshot["errors"]:
        lines.extend(["", "## Refresh warnings", ""])
        lines.extend(f"- {error}" for error in snapshot["errors"])

    lines.extend(["", "---", "", "Regenerate with `python -m project_registry dashboard`.", ""])
    return "\n".join(lines)
