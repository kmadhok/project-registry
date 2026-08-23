"""A deliberately read-only GitHub client.

REST requests are ``GET``. GitHub's GraphQL endpoint requires ``POST``, so the
one GraphQL transport fails closed unless its document is unambiguously a read
query. There is no code path that can issue a mutation, which is how MCP-006 is
enforced at the bottom of the stack rather than only at the tool layer.

Only the standard library is used, so the MCP server runs without an install
step.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

#: The only HTTP verbs this client is permitted to use. POST is confined to
#: ``graphql()``, which refuses anything except an unambiguous read document.
ALLOWED_METHODS = frozenset({"GET", "POST"})
ALLOWED_METHOD = "GET"

DEFAULT_BASE_URL = "https://api.github.com"
DEFAULT_BRANCH_MAX_PAGES = 50
GRAPHQL_PATH = "/graphql"
USER_AGENT = "project-registry/0.1 (read-only)"

BRANCHES_QUERY = """
query($owner:String!, $name:String!, $cursor:String) {
  repository(owner:$owner, name:$name) {
    refs(refPrefix:"refs/heads/", first:100, after:$cursor) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes {
        name
        branchProtectionRule { id }
        target {
          ... on Commit {
            oid
            committedDate
            associatedPullRequests(first:10) {
              nodes { number state headRepository { nameWithOwner } }
            }
          }
        }
      }
    }
  }
}
"""


class GitHubError(RuntimeError):
    """A GitHub request failed."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubNotFound(GitHubError):
    """The requested resource does not exist or is not visible to this token."""


class GraphQLError(GitHubError):
    """A GraphQL response carried an ``errors`` array."""


class GitHubClient:
    """Minimal read-only wrapper over the GitHub REST and GraphQL APIs."""

    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
        per_page: int = 100,
        max_pages: int = 10,
        branch_max_pages: int = DEFAULT_BRANCH_MAX_PAGES,
    ) -> None:
        self.token = token or os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.per_page = per_page
        self.max_pages = max_pages
        self.branch_max_pages = branch_max_pages

    # -- transport -------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(path, params)
        request = urllib.request.Request(url, method=ALLOWED_METHOD)
        request.add_header("Accept", "application/vnd.github+json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", USER_AGENT)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = _error_detail(exc)
            if exc.code == 404:
                raise GitHubNotFound(f"{path}: not found ({detail})", 404) from None
            raise GitHubError(f"{path}: HTTP {exc.code} ({detail})", exc.code) from None
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise GitHubError(f"{path}: {exc.reason}") from None

        return json.loads(payload) if payload.strip() else None

    def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict:
        """POST a read-only GraphQL query. Non-read operations fail closed."""
        if _is_mutation(query):
            raise GitHubError("graphql(): only read queries are permitted")

        url = self._url(GRAPHQL_PATH, None)
        body = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Accept", "application/json")
        request.add_header("Content-Type", "application/json")
        request.add_header("X-GitHub-Api-Version", "2022-11-28")
        request.add_header("User-Agent", USER_AGENT)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            detail = _error_detail(exc)
            if exc.code == 404:
                raise GitHubNotFound(f"graphql: not found ({detail})", 404) from None
            raise GitHubError(f"graphql: HTTP {exc.code} ({detail})", exc.code) from None
        except urllib.error.URLError as exc:  # pragma: no cover - network path
            raise GitHubError(f"graphql: {exc.reason}") from None

        try:
            payload = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            raise GraphQLError("graphql: invalid JSON response") from None
        if not isinstance(payload, dict):
            raise GraphQLError("graphql: expected a response object")
        if payload.get("errors"):
            first = payload["errors"][0]
            message = first.get("message") if isinstance(first, dict) else str(first)
            raise GraphQLError(f"graphql: {message or 'unknown error'}")
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _url(self, path: str, params: dict[str, Any] | None) -> str:
        if path.startswith("http"):
            base = path
        else:
            base = f"{self.base_url}/{path.lstrip('/')}"
        if params:
            query = urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
            base = f"{base}?{query}"
        return base

    def paginate(self, path: str, params: dict[str, Any] | None = None) -> Iterator[dict]:
        """Yield items across pages, bounded by ``max_pages``."""
        page = 1
        while page <= self.max_pages:
            batch = self.get(path, {**(params or {}), "per_page": self.per_page, "page": page})
            if not batch:
                return
            if not isinstance(batch, list):
                raise GitHubError(f"{path}: expected a list response")
            yield from batch
            if len(batch) < self.per_page:
                return
            page += 1

    # -- resources -------------------------------------------------------

    def list_owner_repos(self, owner: str, affiliation: str = "owner") -> list[dict]:
        """Repositories for a user or organization.

        Tries the authenticated endpoint first so private repositories are
        included, and falls back to the public listing when unauthenticated.
        """
        if self.token:
            try:
                repos = list(
                    self.paginate("/user/repos", {"affiliation": affiliation, "sort": "pushed"})
                )
                owned = [
                    r for r in repos
                    if (r.get("owner") or {}).get("login", "").lower() == owner.lower()
                ]
                if owned:
                    return owned
            except GitHubError:
                pass
        return list(self.paginate(f"/users/{owner}/repos", {"sort": "pushed"}))

    def get_repo(self, full_name: str) -> dict:
        return self.get(f"/repos/{full_name}")

    def list_open_pulls(self, full_name: str) -> list[dict]:
        return list(self.paginate(f"/repos/{full_name}/pulls", {"state": "open"}))

    def get_pull(self, full_name: str, number: int) -> dict:
        return self.get(f"/repos/{full_name}/pulls/{number}") or {}

    def list_open_issues(self, full_name: str) -> list[dict]:
        """Open issues, excluding pull requests (GitHub returns both here)."""
        items = self.paginate(f"/repos/{full_name}/issues", {"state": "open"})
        return [item for item in items if "pull_request" not in item]

    def list_reviews(self, full_name: str, number: int) -> list[dict]:
        return list(self.paginate(f"/repos/{full_name}/pulls/{number}/reviews"))

    def list_check_runs(self, full_name: str, ref: str) -> list[dict]:
        payload = self.get(f"/repos/{full_name}/commits/{ref}/check-runs")
        return list((payload or {}).get("check_runs") or [])

    def get_combined_status(self, full_name: str, ref: str) -> dict:
        return self.get(f"/repos/{full_name}/commits/{ref}/status") or {}

    def list_branch_nodes(self, full_name: str) -> tuple[list[dict], bool, int]:
        """Return GraphQL branch nodes, whether they are partial, and total count."""
        if "/" not in full_name or self.branch_max_pages < 1:
            raise GitHubError(f"{full_name}: invalid repository or pagination limit")
        owner, name = full_name.split("/", 1)
        if not owner or not name:
            raise GitHubError(f"{full_name}: invalid repository name")

        nodes: list[dict] = []
        cursor: str | None = None
        total_count = 0
        has_next_page = False
        for _ in range(self.branch_max_pages):
            data = self.graphql(
                BRANCHES_QUERY,
                {"owner": owner, "name": name, "cursor": cursor},
            )
            repository = data.get("repository")
            if not isinstance(repository, dict):
                raise GitHubNotFound(f"{full_name}: repository not found")
            refs = repository.get("refs")
            if not isinstance(refs, dict):
                raise GitHubError(f"{full_name}: graphql refs response missing")
            page_nodes = refs.get("nodes") or []
            if not isinstance(page_nodes, list):
                raise GitHubError(f"{full_name}: graphql refs nodes is not a list")
            nodes.extend(page_nodes)
            try:
                total_count = int(refs.get("totalCount"))
            except (TypeError, ValueError):
                raise GitHubError(f"{full_name}: graphql refs totalCount missing") from None
            page_info = refs.get("pageInfo") or {}
            has_next_page = bool(page_info.get("hasNextPage"))
            if not has_next_page:
                return nodes, False, total_count
            cursor = page_info.get("endCursor")
            if not cursor:
                raise GitHubError(f"{full_name}: graphql refs cursor missing")

        return nodes, has_next_page, total_count


def _is_mutation(query: str) -> bool:
    """Return true unless a GraphQL document opens as an unambiguous read."""
    text = query.lstrip()
    while text.startswith("#"):
        _, newline, text = text.partition("\n")
        if not newline:
            return True
        text = text.lstrip()
    opens_as_read = text.startswith("{") or re.match(r"(?:query|fragment)\b", text)
    has_non_read_operation = re.search(r"\b(?:mutation|subscription)\b", text)
    return not opens_as_read or bool(has_non_read_operation)


def _error_detail(exc: urllib.error.HTTPError) -> str:  # pragma: no cover - network path
    try:
        body = json.loads(exc.read().decode("utf-8"))
        return str(body.get("message") or exc.reason)
    except Exception:
        return str(exc.reason)


def derive_review_state(reviews: list[dict]) -> str:
    """Collapse a review list into one state.

    Only each reviewer's most recent decisive review counts, so an approval
    that was later followed by a change request does not mask it.
    """
    if not reviews:
        return "unreviewed"

    latest: dict[str, str] = {}
    for review in reviews:
        state = (review.get("state") or "").upper()
        if state not in {"APPROVED", "CHANGES_REQUESTED", "COMMENTED", "DISMISSED"}:
            continue
        user = ((review.get("user") or {}).get("login")) or "unknown"
        if state == "DISMISSED":
            latest.pop(user, None)
            continue
        if state == "COMMENTED" and latest.get(user) in {"APPROVED", "CHANGES_REQUESTED"}:
            continue
        latest[user] = state

    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "changes_requested"
    if "APPROVED" in states:
        return "approved"
    if "COMMENTED" in states:
        return "commented"
    return "unreviewed"


def derive_ci_state(check_runs: list[dict], combined_status: dict | None = None) -> str:
    """Collapse check runs (preferred) or a combined status into one state.

    Returns ``unknown`` when nothing could be read, never a guess.
    """
    if check_runs:
        conclusions = {(run.get("conclusion") or "").lower() for run in check_runs}
        statuses = {(run.get("status") or "").lower() for run in check_runs}
        if {"failure", "timed_out", "cancelled", "action_required"} & conclusions:
            return "failure"
        if statuses & {"queued", "in_progress", "waiting", "pending"}:
            return "pending"
        if "success" in conclusions:
            return "success"
        return "none"

    if combined_status:
        state = (combined_status.get("state") or "").lower()
        if state in {"success", "failure", "pending"}:
            return state
        if state == "error":
            return "failure"
        if not combined_status.get("statuses"):
            return "none"

    return "unknown"
