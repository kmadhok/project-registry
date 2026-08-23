"""Evidence-brief coverage and revision staleness visibility."""

from __future__ import annotations

import json

from project_registry.briefs import build_briefs_status
from project_registry.cli import main
from project_registry.github.snapshot import Branch
from project_registry.github.sync import save_snapshot
from project_registry.mcp.server import handle_request
from project_registry.storage import load_registry

from .conftest import NOW, make_repo_state, make_snapshot


def _write_brief(paths, project_id, *, revision="head-a", analyzed_at="2026-07-20T12:00:00Z"):
    paths.understanding_dir.mkdir(parents=True, exist_ok=True)
    path = paths.understanding_dir / f"{project_id}.json"
    path.write_text(json.dumps({
        "repository": f"owner/{project_id}",
        "analyzed_at": analyzed_at,
        "revision": revision,
    }), encoding="utf-8")
    return path


def _snapshot(*repo_heads):
    states = []
    for repo, head in repo_heads:
        states.append(make_repo_state(
            repo,
            branches=[Branch(name="main", head_sha=head, is_default=True)],
            branches_fetched=True,
        ))
    return make_snapshot(*states)


def _mcp_call(paths, name):
    response = handle_request({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": {}},
    }, paths)
    result = response["result"]
    return json.loads(result["content"][0]["text"]), result["isError"]


def test_missing_directory_reports_every_repo_project_missing(paths, write_project):
    write_project(id="alpha", purpose="p", repo="owner/alpha")
    write_project(id="beta", purpose="p", repo="owner/beta")
    write_project(id="local", purpose="p")

    report = build_briefs_status(load_registry(paths), make_snapshot(), paths=paths, now=NOW)

    assert report["summary"] == {
        "total": 2, "present": 0, "missing": 2, "current": 0,
        "stale": 0, "unknown": 0, "malformed": 0,
    }
    assert [item["project_id"] for item in report["projects"]] == ["alpha", "beta"]
    assert all(item["present"] is False for item in report["projects"])


def test_malformed_json_is_present_but_unusable_and_never_current(paths, write_project):
    write_project(id="alpha", purpose="p", repo="owner/alpha")
    paths.understanding_dir.mkdir(parents=True)
    (paths.understanding_dir / "alpha.json").write_text("{broken", encoding="utf-8")

    report = build_briefs_status(
        load_registry(paths), _snapshot(("owner/alpha", "head-a")), paths=paths, now=NOW
    )
    item = report["projects"][0]

    assert item["present"] is True
    assert item["usable"] is False
    assert item["revision_state"] == "unknown"
    assert item["error"] == "malformed JSON"
    assert report["summary"]["malformed"] == 1
    assert report["summary"]["unknown"] == 1


def test_revision_and_age_are_current_stale_or_unknown_from_known_evidence(
    paths, write_project
):
    write_project(id="current", purpose="p", repo="owner/current")
    write_project(id="stale", purpose="p", repo="owner/stale")
    write_project(id="unknown", purpose="p", repo="owner/unknown")
    _write_brief(paths, "current", revision="same", analyzed_at="2026-07-20T12:00:00Z")
    _write_brief(paths, "stale", revision="old", analyzed_at="2026-07-19T12:00:00Z")
    _write_brief(paths, "unknown", revision="brief-only", analyzed_at="2026-07-18T12:00:00Z")
    snapshot = _snapshot(("owner/current", "same"), ("owner/stale", "new"))

    report = build_briefs_status(load_registry(paths), snapshot, paths=paths, now=NOW)
    items = {item["project_id"]: item for item in report["projects"]}

    assert items["current"]["revision_state"] == "current"
    assert items["current"]["age_days"] == 5
    assert items["stale"]["revision_state"] == "stale"
    assert items["stale"]["current_revision"] == "new"
    assert items["unknown"]["revision_state"] == "unknown"
    assert items["unknown"]["current_revision"] is None
    assert report["summary"] == {
        "total": 3, "present": 3, "missing": 0, "current": 1,
        "stale": 1, "unknown": 1, "malformed": 0,
    }


def test_unfetched_branch_evidence_cannot_make_revision_current(paths, write_project):
    write_project(id="alpha", purpose="p", repo="owner/alpha")
    _write_brief(paths, "alpha", revision="same")
    state = make_repo_state(
        "owner/alpha",
        branches=[Branch(name="main", head_sha="same", is_default=True)],
        branches_fetched=False,
    )

    report = build_briefs_status(
        load_registry(paths), make_snapshot(state), paths=paths, now=NOW
    )

    assert report["projects"][0]["revision_state"] == "unknown"


def test_cli_and_mcp_share_brief_numbers_and_sync_status_summary(
    paths, write_project, capsys
):
    write_project(id="alpha", purpose="p", repo="owner/alpha")
    write_project(id="beta", purpose="p", repo="owner/beta")
    _write_brief(paths, "alpha", revision="old")
    save_snapshot(_snapshot(("owner/alpha", "new")), paths)

    code = main(["--root", str(paths.root), "briefs-status", "--json"])
    cli_report = json.loads(capsys.readouterr().out)
    mcp_report, is_error = _mcp_call(paths, "get_briefs_status")

    assert code == 0
    assert not is_error
    assert mcp_report["briefs"] == cli_report

    main(["--root", str(paths.root), "sync-status"])
    text = capsys.readouterr().out
    assert "briefs         1 present / 1 missing / 1 stale" in text
    status, is_error = _mcp_call(paths, "get_github_sync_status")
    assert not is_error
    assert status["status"]["briefs"] == cli_report["summary"]
