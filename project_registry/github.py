from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from project_registry.io import utc_now
from project_registry.model import SCHEMA_VERSION


class GitHubCommandError(RuntimeError):
    pass


def resolve_gh() -> str:
    configured = os.environ.get("GH_BIN")
    candidates = [
        configured,
        shutil.which("gh"),
        shutil.which("gh.exe"),
        "/mnt/c/Program Files/GitHub CLI/gh.exe",
        r"C:\Program Files\GitHub CLI\gh.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise GitHubCommandError(
        "GitHub CLI was not found. Install gh or set GH_BIN to its executable path."
    )


def run_gh(arguments: list[str], gh_bin: str | None = None) -> Any:
    executable = gh_bin or resolve_gh()
    result = subprocess.run(
        [executable, *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown gh error"
        raise GitHubCommandError(f"gh {' '.join(arguments[:3])}: {detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise GitHubCommandError("GitHub CLI returned invalid JSON") from error


def _repo_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("nameWithOwner") or value.get("name") or "")
    return str(value or "")


def _person_login(value: Any) -> str | None:
    if isinstance(value, dict):
        return value.get("login")
    return None


def _normalize_repository(repository: dict[str, Any]) -> dict[str, Any]:
    language = repository.get("primaryLanguage")
    if isinstance(language, dict):
        language = language.get("name")
    default_branch = repository.get("defaultBranchRef")
    if isinstance(default_branch, dict):
        default_branch = default_branch.get("name")
    topics = repository.get("repositoryTopics") or []
    normalized_topics: list[str] = []
    for topic in topics:
        if isinstance(topic, str):
            normalized_topics.append(topic)
        elif isinstance(topic, dict):
            normalized_topics.append(str(topic.get("name") or topic.get("topic", {}).get("name") or ""))
    return {
        "name": repository["name"],
        "name_with_owner": repository["nameWithOwner"],
        "url": repository["url"],
        "description": repository.get("description") or None,
        "visibility": "private" if repository.get("isPrivate") else "public",
        "is_private": bool(repository.get("isPrivate")),
        "is_archived": bool(repository.get("isArchived")),
        "is_fork": bool(repository.get("isFork")),
        "is_template": bool(repository.get("isTemplate")),
        "has_issues_enabled": bool(repository.get("hasIssuesEnabled")),
        "default_branch": default_branch,
        "primary_language": language,
        "topics": sorted(topic for topic in normalized_topics if topic),
        "homepage_url": repository.get("homepageUrl") or None,
        "disk_usage_kb": repository.get("diskUsage"),
        "pushed_at": repository.get("pushedAt"),
        "updated_at": repository.get("updatedAt"),
    }


def _check_summary(checks: Any) -> dict[str, Any]:
    if not isinstance(checks, list) or not checks:
        return {"state": "none", "total": 0, "failing": 0, "pending": 0}
    failing_values = {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "ERROR"}
    pending_values = {"PENDING", "QUEUED", "IN_PROGRESS", "REQUESTED", "WAITING"}
    failing = 0
    pending = 0
    for check in checks:
        if not isinstance(check, dict):
            continue
        value = str(check.get("conclusion") or check.get("status") or "").upper()
        if value in failing_values:
            failing += 1
        elif value in pending_values or not value:
            pending += 1
    state = "failing" if failing else "pending" if pending else "passing"
    return {"state": state, "total": len(checks), "failing": failing, "pending": pending}


def _normalize_pr(pull_request: dict[str, Any]) -> dict[str, Any]:
    return {
        "repository": _repo_name(pull_request.get("repository")),
        "number": pull_request["number"],
        "title": pull_request["title"],
        "url": pull_request["url"],
        "is_draft": bool(pull_request.get("isDraft")),
        "author": _person_login(pull_request.get("author")),
        "created_at": pull_request.get("createdAt"),
        "updated_at": pull_request.get("updatedAt"),
        "review_decision": None,
        "merge_state": None,
        "checks": {"state": "unknown", "total": 0, "failing": 0, "pending": 0},
    }


def _normalize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    labels = issue.get("labels") or []
    normalized_labels = [
        label.get("name") if isinstance(label, dict) else str(label) for label in labels
    ]
    return {
        "repository": _repo_name(issue.get("repository")),
        "number": issue["number"],
        "title": issue["title"],
        "url": issue["url"],
        "author": _person_login(issue.get("author")),
        "labels": sorted(label for label in normalized_labels if label),
        "created_at": issue.get("createdAt"),
        "updated_at": issue.get("updatedAt"),
    }


def collect_github_snapshot(
    owner: str,
    *,
    gh_bin: str | None = None,
    enrich_pull_requests: bool = True,
) -> dict[str, Any]:
    repository_fields = (
        "name,nameWithOwner,isPrivate,isArchived,isFork,isTemplate,hasIssuesEnabled,"
        "updatedAt,pushedAt,description,url,primaryLanguage,defaultBranchRef,diskUsage,"
        "homepageUrl,repositoryTopics"
    )
    raw_repositories = run_gh(
        ["repo", "list", owner, "--limit", "500", "--json", repository_fields], gh_bin
    )
    raw_pull_requests = run_gh(
        [
            "search", "prs", "--owner", owner, "--state", "open", "--limit", "1000",
            "--json", "repository,title,number,updatedAt,createdAt,url,isDraft,author",
        ],
        gh_bin,
    )
    raw_issues = run_gh(
        [
            "search", "issues", "--owner", owner, "--state", "open", "--limit", "1000",
            "--json", "repository,title,number,updatedAt,createdAt,url,labels,author",
        ],
        gh_bin,
    )

    repositories = sorted(
        (_normalize_repository(item) for item in raw_repositories),
        key=lambda item: item["name_with_owner"].lower(),
    )
    pull_requests = sorted(
        (_normalize_pr(item) for item in raw_pull_requests),
        key=lambda item: (item["repository"].lower(), item["number"]),
    )
    issues = sorted(
        (_normalize_issue(item) for item in raw_issues),
        key=lambda item: (item["repository"].lower(), item["number"]),
    )
    errors: list[str] = []

    if enrich_pull_requests:
        for pull_request in pull_requests:
            try:
                detail = run_gh(
                    [
                        "pr", "view", str(pull_request["number"]),
                        "--repo", pull_request["repository"],
                        "--json", "reviewDecision,statusCheckRollup,mergeStateStatus",
                    ],
                    gh_bin,
                )
                pull_request["review_decision"] = detail.get("reviewDecision") or None
                pull_request["merge_state"] = detail.get("mergeStateStatus") or None
                pull_request["checks"] = _check_summary(detail.get("statusCheckRollup"))
            except GitHubCommandError as error:
                errors.append(
                    f"Could not enrich {pull_request['repository']}#{pull_request['number']}: {error}"
                )

    return {
        "schema_version": SCHEMA_VERSION,
        "owner": owner,
        "generated_at": utc_now(),
        "repositories": repositories,
        "open_pull_requests": pull_requests,
        "open_issues": issues,
        "errors": errors,
    }
