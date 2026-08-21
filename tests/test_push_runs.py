"""Push-run journal reporting and cached PR-state resolution."""

from __future__ import annotations

import json

from project_registry import push_runs as push_runs_module
from project_registry.cli import main
from project_registry.github.client import GitHubError
from project_registry.mcp.server import handle_request
from project_registry.push_runs import build_push_report
from project_registry.storage import write_json

from .conftest import NOW


PR_OPEN = "https://github.com/owner/one/pull/1"
PR_MERGED = "https://github.com/owner/two/pull/2"
PR_CLOSED = "https://github.com/owner/three/pull/3"


def _write_journal(paths, *, malformed: bool = True) -> None:
    records = [
        {"ts": "2026-07-01T08:00:00Z", "host": "mac", "project": "one", "gear": 1,
         "outcome": "spec", "pr": PR_OPEN},
        {"ts": "2026-07-02T08:00:00Z", "host": "pc", "project": "two", "gear": 2,
         "outcome": "chunk", "pr": PR_MERGED},
        {"ts": "2026-07-03T08:00:00Z", "host": "cloud", "project": "three", "gear": 2,
         "outcome": "amend", "pr": PR_CLOSED},
        {"ts": "2026-07-04T08:00:00Z", "host": "mac", "project": "four", "gear": 2,
         "outcome": "aborted", "pr": None},
        {"ts": "2026-07-05T08:00:00Z", "host": "mac", "project": None, "gear": None,
         "outcome": "parked", "pr": None},
        {"ts": "2026-07-06T08:00:00Z", "host": "cloud", "project": None, "gear": None,
         "outcome": "empty_focus", "pr": None},
    ]
    lines = [json.dumps(record) for record in records]
    if malformed:
        lines.insert(2, "{not-json")
    paths.push_runs_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_cache(paths) -> None:
    write_json(paths.push_prs_file, {
        "fetched_at": "2026-07-20T12:00:00Z",
        "pull_requests": {
            PR_OPEN: {"state": "open", "fetched_at": "2026-07-20T12:00:00Z"},
            PR_MERGED: {"state": "merged", "fetched_at": "2026-07-20T12:00:00Z"},
            PR_CLOSED: {"state": "closed", "fetched_at": "2026-07-20T12:00:00Z"},
        },
    })


def test_report_covers_every_outcome_and_reports_malformed_lines(paths):
    _write_journal(paths)
    _write_cache(paths)

    report = build_push_report(paths=paths, now=NOW)

    assert report["journal"] == {
        "path": str(paths.push_runs_file),
        "total_lines": 7,
        "valid_runs": 6,
        "malformed_count": 1,
        "malformed": [{"line": 3, "error": "invalid JSON"}],
    }
    assert report["runs"]["total"] == 6
    assert report["runs"]["by_outcome"] == {
        "aborted": 1, "amend": 1, "chunk": 1,
        "empty_focus": 1, "parked": 1, "spec": 1,
    }
    assert report["runs"]["by_host"] == {"cloud": 2, "mac": 3, "pc": 1}
    assert report["runs"]["by_project"]["(none)"] == 2
    assert report["runs"]["by_gear"] == {"(none)": 2, "1": 1, "2": 3}
    assert report["pull_requests"]["by_state"] == {
        "open": 1, "merged": 1, "closed": 1, "unknown": 0,
    }
    assert report["pull_requests"]["merge_rate"] == {
        "merged": 1, "total": 3, "percent": 33.3,
    }


def test_cache_miss_is_unknown_and_never_merged(paths):
    _write_journal(paths, malformed=False)

    report = build_push_report(paths=paths, now=NOW)

    assert report["pull_requests"]["by_state"] == {
        "open": 0, "merged": 0, "closed": 0, "unknown": 3,
    }
    assert report["pull_requests"]["merge_rate"] == {
        "merged": 0, "total": 3, "percent": 0.0,
    }


def test_duplicate_pr_links_count_as_runs_but_one_pr(paths):
    _write_journal(paths, malformed=False)
    duplicate = {
        "ts": "2026-07-07T08:00:00Z", "host": "mac", "project": "one", "gear": 2,
        "outcome": "chunk", "pr": PR_OPEN,
    }
    with paths.push_runs_file.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(duplicate) + "\n")

    report = build_push_report(paths=paths, now=NOW)

    assert report["runs"]["total"] == 7
    assert report["pull_requests"]["total"] == 3


def test_schema_and_timestamp_errors_are_counted_without_losing_valid_runs(paths):
    valid = {
        "ts": "2026-07-01T08:00:00Z", "host": "mac", "project": None, "gear": None,
        "outcome": "parked", "pr": None,
    }
    paths.push_runs_file.write_text(
        "\n".join([
            json.dumps(valid),
            json.dumps({**valid, "ts": "yesterday"}),
            json.dumps({**valid, "outcome": "mystery"}),
            json.dumps(["not", "an", "object"]),
        ]) + "\n",
        encoding="utf-8",
    )

    report = build_push_report(paths=paths, now=NOW)

    assert report["runs"]["total"] == 1
    assert report["journal"]["malformed_count"] == 3
    assert [item["error"] for item in report["journal"]["malformed"]] == [
        "ts must be an ISO-8601 timestamp",
        "outcome is missing or unknown",
        "expected a JSON object",
    ]


def test_since_is_inclusive_and_filters_valid_runs(paths):
    _write_journal(paths)

    report = build_push_report(paths=paths, since="2026-07-03", now=NOW)

    assert report["since"] == "2026-07-03"
    assert report["runs"]["total"] == 4
    assert report["runs"]["by_outcome"] == {
        "aborted": 1, "amend": 1, "empty_focus": 1, "parked": 1,
    }
    assert report["journal"]["malformed_count"] == 1


def test_refresh_maps_states_and_preserves_cached_state_on_error(paths):
    _write_journal(paths, malformed=False)
    _write_cache(paths)

    class Client:
        def get_pull(self, repo, number):
            if number == 1:
                return {"state": "open", "merged_at": None}
            if number == 2:
                return {"state": "closed", "merged_at": "2026-07-10T12:00:00Z"}
            raise GitHubError("temporary failure")

    report = build_push_report(paths=paths, refresh=True, client=Client(), now=NOW)

    assert report["pull_requests"]["by_state"] == {
        "open": 1, "merged": 1, "closed": 1, "unknown": 0,
    }
    assert report["pull_requests"]["refresh_errors"] == [
        {"url": PR_CLOSED, "error": "temporary failure", "kept_cached": True}
    ]
    saved = json.loads(paths.push_prs_file.read_text(encoding="utf-8"))
    assert saved["pull_requests"][PR_CLOSED]["state"] == "closed"


def test_cli_default_is_network_free_and_matches_mcp_numbers(paths, capsys, monkeypatch):
    _write_journal(paths)
    _write_cache(paths)

    class NetworkMustNotStart:
        def __init__(self, *args, **kwargs):
            raise AssertionError("default push-report must not construct a GitHub client")

    monkeypatch.setattr(push_runs_module, "GitHubClient", NetworkMustNotStart)
    code = main(["--root", str(paths.root), "push-report", "--json"])
    cli_report = json.loads(capsys.readouterr().out)
    response = handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_push_report", "arguments": {}},
    }, paths)
    result = response["result"]
    mcp_payload = json.loads(result["content"][0]["text"])

    assert code == 0
    assert result["isError"] is False
    assert mcp_payload["report"] == cli_report
