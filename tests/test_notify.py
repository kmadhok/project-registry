from __future__ import annotations

import json

from project_registry.cli import main
from project_registry.notify import notify_config, render_notification, send_ntfy
from project_registry.storage import write_json

DIGEST = """# Build run r1

- Project: builder
- Outcome: completed
- Merges: 2

## Merged chunks

- 1: https://github.com/owner/builder/pull/1 — tag checkpoint/r1-1
- 2: https://github.com/owner/builder/pull/2 — tag checkpoint/r1-2

## Rejections and skips

- 3: review
"""

INBOX = {"count": 1, "items": [
    {"kind": "paused", "project_id": "other", "summary": "paused: revert", "action": "registry build resume other"},
]}


def test_render_is_short_and_leads_with_outcome():
    text = render_notification(DIGEST, INBOX)
    lines = text.splitlines()
    assert lines[0] == "builder · completed · merged 2"
    assert "https://github.com/owner/builder/pull/1" in text
    assert "rejected/skipped: 3 (review)" in text
    assert "Needs you (1): paused other — registry build resume other" in text
    assert len(lines) <= 12


def test_render_without_inbox_says_nothing_needed():
    text = render_notification(DIGEST, {"count": 0, "items": []})
    assert "Needs you: nothing" in text


def test_config_env_wins_over_file(paths, monkeypatch):
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.build_dir / "notify.json", {"topic": "file-topic", "server": "https://x"})
    assert notify_config(paths)["topic"] == "file-topic"
    monkeypatch.setenv("REGISTRY_NTFY_TOPIC", "env-topic")
    assert notify_config(paths) == {"topic": "env-topic", "server": "https://x"}


def test_config_absent_is_none(paths, monkeypatch):
    monkeypatch.delenv("REGISTRY_NTFY_TOPIC", raising=False)
    assert notify_config(paths) is None


def test_send_posts_to_topic(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, request.data, dict(request.header_items()), timeout))
        class R:
            status = 200
            def read(self): return b"{}"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()

    monkeypatch.setattr("project_registry.notify.urllib.request.urlopen", fake_urlopen)
    result = send_ntfy("t0pic", "hello", server="https://ntfy.sh", title="builder · completed")
    assert result == {"sent": True, "status": 200}
    url, data, headers, timeout = calls[0]
    assert url == "https://ntfy.sh/t0pic" and data == b"hello"
    assert headers.get("Title") == "builder · completed" and timeout == 10


def test_send_reports_failure_without_raising(monkeypatch):
    import urllib.error
    def boom(request, timeout):
        raise urllib.error.URLError("down")
    monkeypatch.setattr("project_registry.notify.urllib.request.urlopen", boom)
    assert send_ntfy("t", "m", server="https://ntfy.sh", title="x") == {"sent": False, "error": "<urlopen error down>"}


def test_notify_cli_dry_run_prints_message(paths, write_project, capsys, monkeypatch):
    monkeypatch.setenv("REGISTRY_NTFY_TOPIC", "t")
    write_project(id="builder", purpose="p", repo="o/b")
    paths.build_digests_dir.mkdir(parents=True, exist_ok=True)
    (paths.build_digests_dir / "r1.md").write_text(DIGEST, encoding="utf-8")
    code = main(["--root", str(paths.root), "notify", "--run", "r1", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["sent"] is False and payload["configured"] is True
    assert payload["message"].startswith("builder · completed · merged 2")
