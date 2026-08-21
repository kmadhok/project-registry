"""Guarded GraphQL transport tests for the read-only GitHub client."""

from __future__ import annotations

import inspect
import json

import pytest

from project_registry.github import client as client_module
from project_registry.github.client import GitHubClient, GitHubError, GraphQLError


class FakeResponse:
    def __init__(self, payload: dict):
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


def install_responses(monkeypatch, *payloads):
    calls = []
    remaining = list(payloads)

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse(remaining.pop(0))

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_post_is_allowlisted_only_inside_graphql():
    source = inspect.getsource(client_module)
    graphql_source = inspect.getsource(GitHubClient.graphql)

    assert client_module.ALLOWED_METHODS == frozenset({"GET", "POST"})
    assert source.count('method="POST"') == 1
    assert graphql_source.count('method="POST"') == 1


@pytest.mark.parametrize(
    "document",
    [
        "mutation { deleteRef(input: {}) { clientMutationId } }",
        "subscription { viewer { login } }",
        "# looks harmless\nmutation { deleteRef(input: {}) { clientMutationId } }",
        "fragment F on User { login }\nmutation { deleteRef(input: {}) { clientMutationId } }",
        "notAnOperation { viewer { login } }",
    ],
)
def test_graphql_refuses_non_read_operations_before_transport(monkeypatch, document):
    calls = install_responses(monkeypatch)

    with pytest.raises(GitHubError, match="only read queries"):
        GitHubClient().graphql(document)

    assert calls == []


@pytest.mark.parametrize(
    "document",
    [
        "{ viewer { login } }",
        "query Viewer { viewer { login } }",
        "\n query($login:String!) { user(login:$login) { id } }",
        "fragment UserFields on User { login }\nquery { viewer { ...UserFields } }",
    ],
)
def test_graphql_permits_unambiguous_read_documents(monkeypatch, document):
    calls = install_responses(monkeypatch, {"data": {"viewer": {"login": "owner"}}})

    data = GitHubClient(token="token").graphql(document, {"login": "owner"})

    assert data == {"viewer": {"login": "owner"}}
    request, timeout = calls[0]
    assert request.method == "POST"
    assert request.full_url == "https://api.github.com/graphql"
    assert json.loads(request.data) == {"query": document, "variables": {"login": "owner"}}
    assert request.get_header("Authorization") == "Bearer token"
    assert timeout == 30


def test_graphql_errors_are_not_returned_as_empty_success(monkeypatch):
    install_responses(monkeypatch, {"errors": [{"message": "field unavailable"}]})

    with pytest.raises(GraphQLError, match="field unavailable"):
        GitHubClient().graphql("{ viewer { login } }")


def test_list_branch_nodes_threads_cursor_and_reports_complete(monkeypatch):
    calls = install_responses(
        monkeypatch,
        {
            "data": {
                "repository": {
                    "refs": {
                        "totalCount": 2,
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                        "nodes": [{"name": "a"}],
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "refs": {
                        "totalCount": 2,
                        "pageInfo": {"hasNextPage": False, "endCursor": "cursor-2"},
                        "nodes": [{"name": "b"}],
                    }
                }
            }
        },
    )

    nodes, partial, total = GitHubClient(max_pages=2).list_branch_nodes("owner/repo")

    assert nodes == [{"name": "a"}, {"name": "b"}]
    assert partial is False
    assert total == 2
    variables = [json.loads(request.data)["variables"] for request, _ in calls]
    assert variables == [
        {"owner": "owner", "name": "repo", "cursor": None},
        {"owner": "owner", "name": "repo", "cursor": "cursor-1"},
    ]


def test_list_branch_nodes_marks_ceiling_as_partial(monkeypatch):
    install_responses(
        monkeypatch,
        {
            "data": {
                "repository": {
                    "refs": {
                        "totalCount": 200,
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                        "nodes": [{"name": "a"}],
                    }
                }
            }
        },
    )

    nodes, partial, total = GitHubClient(max_pages=1).list_branch_nodes("owner/repo")

    assert nodes == [{"name": "a"}]
    assert partial is True
    assert total == 200
