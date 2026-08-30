from __future__ import annotations

import json
import urllib.error

from project_registry.cli import main
from project_registry.notify import (
    build_inbox_notification, build_notification, notify_config,
    render_notification, send_ntfy,
)
from project_registry.storage import write_json

DIGEST = """# Build run r1

- Project: builder
- Outcome: completed
- Merges: 2

## Merged chunks

- 1: https://github.com/owner/builder/pull/1 — tag checkpoint/r1-1
  - Revert: `git revert abc`
- 2: https://github.com/owner/builder/pull/2 — tag checkpoint/r1-2

## Rejections and skips

- 3: review
"""

NEEDS_INTENT_DIGEST = """# Build run r2

- Project: builder
- Outcome: needs_intent
- Merges: 3

## Summary

2 chunks merged, 1 plan chunk merged. Roadmap now exhausted: remaining items blocked_by_policy; filed proposal builder-1.

## Merged chunks

- 1: https://github.com/owner/builder/pull/6 — tag t
- plan2: https://github.com/owner/builder/pull/7 — tag t
- 5: https://github.com/owner/builder/pull/8 — tag t
- 6: https://github.com/owner/builder/pull/9 — tag t
"""

CRASHED_DIGEST = """# Build run r3

- Project: builder
- Outcome: crashed
- Merges: 0

## Summary

Claude session quota was exhausted.

## Guard denials

- guard_error
"""

EMPTY_INBOX = {"count": 0, "items": []}
INBOX = {"count": 2, "items": [
    {"kind": "proposal_pending", "project_id": "builder", "summary": "proposal builder-1: allow deps",
     "action": "registry proposal-apply builder-1 --approve  (or proposal-reject)"},
    {"kind": "paused", "project_id": "other", "summary": "paused: revert", "action": "registry build resume other"},
]}


def test_build_empty_inbox_notification():
    notification = build_inbox_notification(EMPTY_INBOX)
    assert notification == {
        "title": "Nothing needed",
        "body": "Nothing needs you.",
        "priority": 3,
        "tags": ["white_check_mark"],
        "click": None,
        "actions": [],
    }


def test_build_inbox_notification_lists_items():
    inbox = {"count": 3, "items": INBOX["items"] + [{
        "kind": "run_failed", "project_id": None,
        "summary": "run r3 crashed", "action": "registry build-report",
    }]}
    notification = build_inbox_notification(inbox)
    assert notification["title"] == "Needs you (3)"
    assert notification["priority"] == 4 and notification["tags"] == ["warning"]
    assert "[proposal_pending] builder: proposal builder-1: allow deps" in notification["body"]
    assert "    -> registry build resume other" in notification["body"]
    assert "[run_failed] -: run r3 crashed" in notification["body"]


def test_build_inbox_notification_truncates_after_eight_items():
    items = [{
        "kind": "paused", "project_id": f"p{index}",
        "summary": f"summary {index}", "action": f"action {index}",
    } for index in range(10)]
    notification = build_inbox_notification({"count": 10, "items": items})
    assert "[paused] p7: summary 7" in notification["body"]
    assert "[paused] p8: summary 8" not in notification["body"]
    assert notification["body"].splitlines()[-1] == "+2 more — registry owner-inbox"


def test_clean_run_title_says_nothing_needed_and_lists_prs():
    n = build_notification(DIGEST, EMPTY_INBOX)
    assert n["title"] == "builder: merged 2 — nothing needed"
    assert n["body"].splitlines()[0] == "Merged 2 · rejected/skipped 1 · guard denials 0"
    assert "  chunk 1 → PR #1" in n["body"] and "  chunk 2 → PR #2" in n["body"]
    assert n["body"].splitlines()[-1] == "Nothing needs you."
    assert n["priority"] == 3 and n["tags"] == ["white_check_mark"]
    assert n["click"] == "https://github.com/owner/builder/pull/1"
    assert n["actions"] == [
        "view, PR #1, https://github.com/owner/builder/pull/1",
        "view, PR #2, https://github.com/owner/builder/pull/2",
    ]


def test_outcome_progress_is_appended_to_title_only_when_present():
    with_outcome = DIGEST.replace(
        "## Merged chunks", "## Outcome\n\ncriteria: 2/5 met · 1 blocked\n\n## Merged chunks"
    )
    assert build_notification(with_outcome, EMPTY_INBOX)["title"].endswith(
        "· criteria 2/5"
    )
    assert build_notification(DIGEST, EMPTY_INBOX)["title"] == (
        "builder: merged 2 — nothing needed"
    )


def test_needs_you_run_is_high_priority_with_numbered_actions():
    n = build_notification(NEEDS_INTENT_DIGEST, INBOX)
    assert n["title"] == "builder: merged 4 — needs you (2)"
    assert n["priority"] == 4 and n["tags"] == ["raised_hand", "inbox_tray"]
    body = n["body"]
    assert "  +1 more" in body                      # four merged, three shown
    assert body.count("Stopped: needs_intent — ") == 1
    assert "NEEDS YOU (2)" in body
    assert "1. proposal_pending · builder" in body
    assert "   registry proposal-apply builder-1 --approve" in body
    assert "2. paused · other" in body
    assert len(n["actions"]) == 3                   # ntfy maximum


def test_human_stop_with_empty_inbox_names_the_stop_reason():
    n = build_notification(NEEDS_INTENT_DIGEST, EMPTY_INBOX)
    assert n["title"] == "builder: merged 4 — stopped: needs_intent"
    assert n["priority"] == 4 and n["tags"] == ["raised_hand"]
    assert n["body"].splitlines()[-1] == "Nothing needs you."


def test_failed_run_is_urgent():
    n = build_notification(CRASHED_DIGEST, EMPTY_INBOX)
    assert n["title"] == "builder: crashed — needs you"
    assert n["priority"] == 5 and n["tags"] == ["rotating_light"]
    assert "guard denials 1" in n["body"]
    assert "Stopped: crashed — Claude session quota was exhausted." in n["body"]
    assert n["click"] is None and n["actions"] == []


def test_summary_is_truncated_to_lock_screen_length():
    long = NEEDS_INTENT_DIGEST.replace("filed proposal builder-1.", "x" * 300)
    n = build_notification(long, EMPTY_INBOX)
    stopped = next(line for line in n["body"].splitlines() if line.startswith("Stopped:"))
    assert len(stopped) <= len("Stopped: needs_intent — ") + 140
    assert stopped.endswith("...")


def test_render_is_title_then_body():
    text = render_notification(DIGEST, EMPTY_INBOX)
    assert text.splitlines()[0] == "builder: merged 2 — nothing needed"
    assert len(text.splitlines()) <= 12


def test_config_env_wins_over_file(paths, monkeypatch):
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.build_dir / "notify.json", {"topic": "file-topic", "server": "https://x"})
    monkeypatch.delenv("REGISTRY_NTFY_TOPIC", raising=False)
    assert notify_config(paths)["topic"] == "file-topic"
    monkeypatch.setenv("REGISTRY_NTFY_TOPIC", "env-topic")
    assert notify_config(paths) == {"topic": "env-topic", "server": "https://x"}


def test_config_absent_is_none(paths, monkeypatch):
    monkeypatch.delenv("REGISTRY_NTFY_TOPIC", raising=False)
    assert notify_config(paths) is None


def test_send_publishes_utf8_json_body(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        # Mirror http.client: headers must be latin-1 encodable.
        for name, value in request.header_items():
            value.encode("latin-1")
        calls.append((request.full_url, request.data, dict(request.header_items()), timeout))

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr("project_registry.notify.urllib.request.urlopen", fake_urlopen)
    n = build_notification(NEEDS_INTENT_DIGEST, INBOX)
    assert "—" in n["title"]                          # non-latin-1, must not go in a header
    assert send_ntfy("t0pic", n, server="https://ntfy.sh") == {"sent": True, "status": 200}
    url, data, headers, timeout = calls[0]
    assert url == "https://ntfy.sh" and timeout == 10
    assert headers["Content-type"].startswith("application/json")
    payload = json.loads(data.decode("utf-8"))
    assert payload["topic"] == "t0pic"
    assert payload["title"] == n["title"] and payload["message"] == n["body"]
    assert payload["priority"] == 4
    assert payload["tags"] == ["raised_hand", "inbox_tray"]
    assert payload["click"] == "https://github.com/owner/builder/pull/6"
    assert payload["actions"][0] == {
        "action": "view", "label": "PR #6", "url": "https://github.com/owner/builder/pull/6",
    }
    assert len(payload["actions"]) == 3


def test_send_accepts_plain_text(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout):
        seen["payload"] = json.loads(request.data.decode("utf-8"))

        class R:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        return R()

    monkeypatch.setattr("project_registry.notify.urllib.request.urlopen", fake_urlopen)
    assert send_ntfy("t", "hello\nworld", server="https://ntfy.sh")["sent"] is True
    assert seen["payload"] == {"topic": "t", "title": "hello", "message": "hello\nworld"}


def test_send_reports_failure_without_raising(monkeypatch):
    def boom(request, timeout):
        raise urllib.error.URLError("down")
    monkeypatch.setattr("project_registry.notify.urllib.request.urlopen", boom)
    assert send_ntfy("t", "m", server="https://ntfy.sh") == {"sent": False, "error": "<urlopen error down>"}


def test_notify_cli_dry_run_prints_message(paths, write_project, capsys, monkeypatch):
    monkeypatch.setenv("REGISTRY_NTFY_TOPIC", "t")
    write_project(id="builder", purpose="p", repo="o/b")
    paths.build_digests_dir.mkdir(parents=True, exist_ok=True)
    (paths.build_digests_dir / "r1.md").write_text(DIGEST, encoding="utf-8")
    code = main(["--root", str(paths.root), "notify", "--run", "r1", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["sent"] is False and payload["configured"] is True
    assert payload["message"].startswith("builder: merged 2 — nothing needed")
    assert payload["notification"]["priority"] == 3
