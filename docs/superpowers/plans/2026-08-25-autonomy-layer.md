# Autonomy Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After every build run the owner receives one short message saying what merged, what failed, and exactly what (if anything) needs him — without opening GitHub — and a weekly retro turns run evidence into proposals he approves with one command.

**Architecture:** Three thin layers on the existing registry. (1) `owner_inbox` is a pure function over registry + journal + proposals that lists every "needs a human" state with its one-line action; exposed as `registry owner-inbox` and appended to each digest. (2) `registry notify` renders the latest digest + inbox into ≤12 lines and POSTs it to an ntfy topic (stdlib `urllib`, no new dependency); the push-project skill calls it in Phase 5. (3) `build-retro` is a Mac-side skill that reads a window of runs and files brief/policy proposals. Budgets are raised by proposal.

**Tech Stack:** Python 3.11 stdlib only (`urllib.request`, `json`, `datetime`), argparse CLI in `src/project_registry/cli.py`, pytest with the existing `paths`/`write_project` fixtures in `tests/conftest.py`, ntfy.sh (HTTP POST), Claude Code skill markdown.

**Ground rules (from CLAUDE.md):** never write curated YAML except via proposals; runtime output only under `data/build/`; no GitHub mutations from registry code; no secrets in files — the ntfy topic name is read from the environment (`REGISTRY_NTFY_TOPIC`) or `data/build/notify.json` (gitignored), never from YAML.

---

## File structure

| File | Responsibility |
|---|---|
| `src/project_registry/inbox.py` (new) | `owner_inbox(paths, registry, states, proposals, events, today) -> dict`: derive human-needed items; pure, no I/O beyond what is passed in |
| `src/project_registry/notify.py` (new) | `render_notification(digest_text, inbox) -> str`, `send_ntfy(topic, message, *, server, title) -> dict`, `notify_config(paths) -> dict` |
| `src/project_registry/build_runs.py` | `_write_digest` gains an `## Needs the owner` section from `owner_inbox`; `finish_run` passes registry/proposals in |
| `src/project_registry/cli.py` | `owner-inbox` and `notify` commands |
| `.claude/skills/push-project/SKILL.md` | Phase 5 step 8 calls `registry notify --run "$RUN_ID"` |
| `scripts/run-build.sh` | passes `REGISTRY_NTFY_TOPIC` through |
| `.claude/skills/build-retro/SKILL.md` (new) | weekly retro: reads `build-report`, digests, `owner-inbox`; writes `docs/observations/<date>-retro.md`; files proposals only |
| `.gitignore` | `data/build/notify.json` |
| `CLAUDE.md`, `docs/RUNBOOK.md`, `README.md` | cheatsheet lines, notification setup, retro cadence |
| `tests/test_inbox.py`, `tests/test_notify.py` (new) | requirement-driven tests |

---

### Task 1: `owner_inbox` — derive every "needs a human" item

**Files:**
- Create: `src/project_registry/inbox.py`
- Test: `tests/test_inbox.py`

Inbox items are dicts `{"kind", "project_id", "summary", "action"}`. Kinds and their sources (all facts already in the registry):

| kind | source | action |
|---|---|---|
| `proposal_pending` | `list_proposals(paths, status="pending")` | `registry proposal-apply <id> --approve` (or `proposal-reject <id>`) |
| `needs_intent` | `build_queue(...)["candidates"]` entries with `state == "needs_intent"` | `registry propose <id> --set brief.<gap>=…` |
| `paused` | `ProjectBuildState.paused_reason` not None | `registry build resume <id>` after reading the digest |
| `blocked_by_policy` | latest run's `run_finished.detail.summary` contains `blocked_by_policy`, or a `chunk_skipped` with `reason == "blocked_by_policy"` | `registry propose <id> --set automation.allow=[…]` |
| `run_failed` | last `run_finished` for the project has outcome in `{"crashed","aborted","codex_unavailable","contract_broken","baseline_red","registry_dirty"}` | read `data/build/digests/<run>.md`; fix host or contract |
| `finalize_pending` | `data/build/lease.json` exists with `status == "finalize_pending"` | `registry build finish <run> --confirm-writeback` after pushing `main` |

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_inbox.py
from __future__ import annotations

import datetime as dt

from project_registry.build import ProjectBuildState, append_event, save_state
from project_registry.inbox import owner_inbox
from project_registry.proposals import propose_update
from project_registry.storage import load_registry, write_json

NOW = dt.datetime(2026, 8, 26, 12, 0, tzinfo=dt.timezone.utc)


def ready(write_project, pid="builder", **extra):
    write_project(
        id=pid, name=pid, purpose="Ship it", desired_outcome="It ships",
        repo=f"owner/{pid}", brief={"done_criteria": ["tests pass"]},
        automation={"mode": "build"}, **extra,
    )


def kinds(result):
    return sorted(item["kind"] for item in result["items"])


def test_empty_registry_has_empty_inbox(paths):
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    assert result["items"] == []
    assert result["count"] == 0


def test_pending_proposal_is_listed_with_apply_action(paths, write_project):
    ready(write_project)
    proposal = propose_update(
        "builder", {"brief.done_criteria": ["tests pass", "docs"]},
        rationale="x", paths=paths, now=NOW,
    )
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "proposal_pending")
    assert item["project_id"] == "builder"
    assert proposal.id in item["action"]
    assert "proposal-apply" in item["action"]


def test_incomplete_brief_is_needs_intent(paths, write_project):
    write_project(id="vague", name="vague", purpose="p", repo="o/vague",
                  automation={"mode": "build"})
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "needs_intent")
    assert item["project_id"] == "vague"
    assert "registry propose vague --set brief." in item["action"]


def test_paused_project_is_listed(paths, write_project):
    ready(write_project)
    save_state(paths, {"builder": ProjectBuildState(paused_reason="consecutive_failures")})
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "paused")
    assert "consecutive_failures" in item["summary"]
    assert item["action"] == "registry build resume builder"


def test_failed_last_run_is_listed_with_digest_path(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "crashed", "detail": {"summary": "quota"}}, now=NOW)
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "run_failed")
    assert "crashed" in item["summary"] and "quota" in item["summary"]
    assert item["action"].endswith("data/build/digests/r1.md")


def test_blocked_by_policy_skip_is_listed(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "chunk_skipped", "project_id": "builder",
                         "chunk_id": "4", "reason": "blocked_by_policy",
                         "detail": {"classes": ["personal_data"]}}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "completed"}, now=NOW)
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "blocked_by_policy")
    assert "personal_data" in item["summary"]
    assert "automation.allow" in item["action"]


def test_finalize_pending_lease_is_listed(paths, write_project):
    ready(write_project)
    paths.build_dir.mkdir(parents=True, exist_ok=True)
    write_json(paths.build_lease_file, {"run_id": "r9", "host": "x", "project_id": "builder",
                                        "status": "finalize_pending"})
    result = owner_inbox(paths, load_registry(paths), now=NOW)
    item = next(i for i in result["items"] if i["kind"] == "finalize_pending")
    assert item["action"] == "registry build finish r9 --confirm-writeback"


def test_successful_run_produces_no_items(paths, write_project):
    ready(write_project)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_started", "project_id": "builder"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "merged", "project_id": "builder",
                         "chunk_id": "1", "pr_url": "https://github.com/owner/builder/pull/1",
                         "tag": "checkpoint/r1-1"}, now=NOW)
    append_event(paths, {"run_id": "r1", "host": "x", "type": "run_finished", "project_id": "builder",
                         "outcome": "completed"}, now=NOW)
    assert owner_inbox(paths, load_registry(paths), now=NOW)["items"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_inbox.py -q`
Expected: `ModuleNotFoundError: No module named 'project_registry.inbox'`

- [ ] **Step 3: Implement `inbox.py`**

```python
# src/project_registry/inbox.py
"""Everything that needs the owner, in one list, with the command that clears it."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .automation import build_queue
from .build_runs import load_state, read_events, _read_lease
from .github.sync import load_snapshot
from .proposals import list_proposals
from .storage import Paths, Registry

FAILURE_OUTCOMES = {
    "crashed", "aborted", "codex_unavailable", "contract_broken",
    "baseline_red", "registry_dirty",
}


def _item(kind: str, project_id: str | None, summary: str, action: str) -> dict[str, Any]:
    return {"kind": kind, "project_id": project_id, "summary": summary, "action": action}


def owner_inbox(
    paths: Paths, registry: Registry, *, now: dt.datetime, snapshot: Any = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []

    for proposal in list_proposals(paths, status="pending"):
        items.append(_item(
            "proposal_pending", proposal.project_id,
            f"proposal {proposal.id}: {proposal.rationale[:80]}",
            f"registry proposal-apply {proposal.id} --approve  (or proposal-reject)",
        ))

    lease = _read_lease(paths)
    if lease is not None and lease.get("status") == "finalize_pending":
        items.append(_item(
            "finalize_pending", lease.get("project_id"),
            f"run {lease['run_id']} finished but its registry write-back was never confirmed",
            f"registry build finish {lease['run_id']} --confirm-writeback",
        ))

    states = load_state(paths)
    snapshot = snapshot or load_snapshot(paths)
    queue = build_queue(registry, snapshot, states, now.date())
    for candidate in queue["candidates"]:
        if candidate["state"] == "needs_intent":
            gaps = ", ".join(candidate["brief_gaps"]) or "brief"
            first = (candidate["brief_gaps"] or ["done_criteria"])[0]
            items.append(_item(
                "needs_intent", candidate["project_id"],
                f"brief incomplete: {gaps}",
                f"registry propose {candidate['project_id']} --set brief.{first}=... --rationale ...",
            ))

    for project_id, state in sorted(states.items()):
        if state.paused_reason is not None:
            items.append(_item(
                "paused", project_id,
                f"paused: {state.paused_reason} (since {state.paused_at})",
                f"registry build resume {project_id}",
            ))

    events, _ = read_events(paths)
    last_finished: dict[str, dict[str, Any]] = {}
    blocked: dict[str, set[str]] = {}
    for event in events:
        project_id = event.get("project_id")
        if event["type"] == "run_finished" and project_id:
            last_finished[project_id] = event
        if event["type"] == "chunk_skipped" and event.get("reason") == "blocked_by_policy" and project_id:
            classes = (event.get("detail") or {}).get("classes") or ["unknown"]
            blocked.setdefault(project_id, set()).update(classes)
    for project_id, event in sorted(last_finished.items()):
        if event.get("outcome") in FAILURE_OUTCOMES:
            summary = (event.get("detail") or {}).get("summary") or ""
            items.append(_item(
                "run_failed", project_id,
                f"last run {event['run_id']} {event['outcome']}: {summary[:120]}",
                f"read {paths.build_digests_dir / (event['run_id'] + '.md')}",
            ))
    for project_id, classes in sorted(blocked.items()):
        items.append(_item(
            "blocked_by_policy", project_id,
            f"SPEC work blocked on change classes: {', '.join(sorted(classes))}",
            f"registry propose {project_id} --set automation.allow='{sorted(classes)}' --rationale ...",
        ))

    return {"generated_at": now.isoformat(), "count": len(items), "items": items}
```

Note: `_read_lease` is private in `build_runs.py`; importing it is acceptable inside the package (same pattern as `cli.py` importing from `.build`). If `build_queue` requires a `Snapshot`, `load_snapshot(paths)` returns an empty one when no cache exists — check `src/project_registry/github/sync.py::load_snapshot` and pass `Snapshot()` explicitly if it raises on a missing file.

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_inbox.py -q`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add src/project_registry/inbox.py tests/test_inbox.py
git commit -m "inbox: derive every owner-needed item with its clearing command"
```

---

### Task 2: `registry owner-inbox` command and digest section

**Files:**
- Modify: `src/project_registry/cli.py` (add `cmd_owner_inbox` near `cmd_build_report` ~line 778; register in `build_parser()` next to `build-report`)
- Modify: `src/project_registry/build_runs.py:_write_digest` (add section) and `finish_run` (pass registry)
- Modify: `CLAUDE.md` cheatsheet, `README.md` command list
- Test: `tests/test_inbox.py` (CLI), `tests/test_build_lifecycle.py` (digest)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_inbox.py
import json
from project_registry.cli import main


def test_owner_inbox_cli_json_and_table(paths, write_project, capsys):
    ready(write_project)
    save_state(paths, {"builder": ProjectBuildState(paused_reason="revert")})
    code = main(["--root", str(paths.root), "owner-inbox", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["count"] == 1 and payload["items"][0]["kind"] == "paused"
    code = main(["--root", str(paths.root), "owner-inbox"])
    out = capsys.readouterr().out
    assert "paused" in out and "registry build resume builder" in out
```

```python
# append to tests/test_build_lifecycle.py
def test_digest_lists_owner_inbox(paths, write_project):
    ready_project(write_project)
    write_project(id="vague", name="Vague", purpose="p", repo="o/vague",
                  automation={"mode": "build"})
    start(paths, run_id="run")
    result = finish_run(paths, "run", outcome="completed", now=NOW,
                        registry=load_registry(paths), snapshot=Snapshot())
    digest = open(result["digest_path"], encoding="utf-8").read()
    assert "## Needs the owner" in digest
    assert "needs_intent" in digest and "vague" in digest
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_inbox.py tests/test_build_lifecycle.py -q -k "owner_inbox or digest_lists"`
Expected: FAIL (`invalid choice: 'owner-inbox'`; digest lacks section)

- [ ] **Step 3: Implement the CLI command**

```python
# src/project_registry/cli.py — near cmd_build_report
def cmd_owner_inbox(args, paths, now) -> int:
    from .inbox import owner_inbox
    registry = load_registry(paths)
    result = owner_inbox(paths, registry, now=now)
    if emit(result, args):
        return 0
    if not result["items"]:
        print("Nothing needs you.")
        return 0
    for item in result["items"]:
        print(f"[{item['kind']}] {item['project_id'] or '-'}: {item['summary']}")
        print(f"    -> {item['action']}")
    return 0
```

In `build_parser()`, next to the `build-report` registration:

```python
    sub = add("owner-inbox", cmd_owner_inbox, "List everything that needs the owner, with the command that clears it.")
```

(`add(...)` already wires `--json`; confirm by reading how `build-report` is registered around line 1150.)

- [ ] **Step 4: Add the digest section**

In `build_runs.py::_write_digest`, add a parameter `inbox: dict[str, Any] | None = None` and, before `target = ...`:

```python
    if inbox and inbox.get("items"):
        lines.extend(["## Needs the owner", ""])
        for item in inbox["items"]:
            lines.append(f"- [{item['kind']}] {item['project_id'] or '-'}: {item['summary']}")
            lines.append(f"  - `{item['action']}`")
        lines.append("")
```

In `finish_run`, after `needs_intent` is computed and before `_write_digest`:

```python
    inbox = None
    if registry is not None:
        from .inbox import owner_inbox
        inbox = owner_inbox(paths, registry, now=current, snapshot=snapshot)
    digest = _write_digest(paths, lease, run_events, outcome, summary, needs_intent, inbox=inbox)
```

Also add the two cheatsheet lines:

```
registry owner-inbox            # everything that needs the owner, with the clearing command; add --json
```

to `CLAUDE.md` (read-only queries block) and the README command list. `tests/test_docs.py` will fail if the name doesn't match the parser.

- [ ] **Step 5: Run the full suite**

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q`
Expected: all pass (previous count + 10)

- [ ] **Step 6: Commit**

```bash
git add src/project_registry/cli.py src/project_registry/build_runs.py tests/test_inbox.py tests/test_build_lifecycle.py CLAUDE.md README.md
git commit -m "owner-inbox: CLI command and digest section"
```

---

### Task 3: `registry notify` — render and push the run summary to ntfy

**Files:**
- Create: `src/project_registry/notify.py`
- Modify: `src/project_registry/cli.py`, `.gitignore` (`data/build/notify.json`), `docs/RUNBOOK.md` (setup)
- Test: `tests/test_notify.py`

Config precedence: `REGISTRY_NTFY_TOPIC` env → `data/build/notify.json` (`{"topic": "...", "server": "https://ntfy.sh"}`) → no config (the command prints the message and exits 0 with `"sent": false`). The topic is a secret-ish value; it never goes into YAML or docs.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_notify.py
from __future__ import annotations

import json

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_notify.py -q`
Expected: `ModuleNotFoundError: No module named 'project_registry.notify'`

- [ ] **Step 3: Implement `notify.py`**

```python
# src/project_registry/notify.py
"""Push a run summary to the owner. ntfy only; stdlib only; never raises on delivery."""

from __future__ import annotations

import os
import re
import urllib.error
import urllib.request
from typing import Any

from .storage import Paths, read_json

DEFAULT_SERVER = "https://ntfy.sh"


def notify_config(paths: Paths) -> dict[str, Any] | None:
    raw = read_json(paths.build_dir / "notify.json") or {}
    topic = os.environ.get("REGISTRY_NTFY_TOPIC") or raw.get("topic")
    if not topic:
        return None
    return {"topic": topic, "server": raw.get("server") or DEFAULT_SERVER}


def _field(digest: str, name: str) -> str:
    match = re.search(rf"^- {name}: (.+)$", digest, re.MULTILINE)
    return match.group(1).strip() if match else "?"


def _section(digest: str, title: str) -> list[str]:
    match = re.search(rf"^## {re.escape(title)}\n\n(.*?)(?:\n## |\Z)", digest, re.DOTALL | re.MULTILINE)
    if not match:
        return []
    return [line[2:] for line in match.group(1).splitlines() if line.startswith("- ")]


def render_notification(digest: str, inbox: dict[str, Any]) -> str:
    project, outcome, merges = _field(digest, "Project"), _field(digest, "Outcome"), _field(digest, "Merges")
    lines = [f"{project} · {outcome} · merged {merges}"]
    for entry in _section(digest, "Merged chunks")[:5]:
        chunk, _, rest = entry.partition(": ")
        url = rest.split(" — ")[0]
        lines.append(f"  #{chunk} {url}")
    skips = _section(digest, "Rejections and skips")
    if skips:
        lines.append("rejected/skipped: " + ", ".join(
            f"{s.split(': ')[0]} ({s.split(': ')[1]})" for s in skips[:4]))
    denials = _section(digest, "Guard denials")
    if denials:
        lines.append("guard denials: " + ", ".join(denials[:4]))
    items = inbox.get("items") or []
    if not items:
        lines.append("Needs you: nothing")
    else:
        lines.append(f"Needs you ({len(items)}):")
        for item in items[:4]:
            lines.append(f"  {item['kind']} {item['project_id'] or '-'} — {item['action']}")
    return "\n".join(lines[:12])


def send_ntfy(topic: str, message: str, *, server: str, title: str) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{server.rstrip('/')}/{topic}", data=message.encode("utf-8"), method="POST",
        headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return {"sent": True, "status": response.status}
    except (urllib.error.URLError, OSError) as error:
        return {"sent": False, "error": str(error)}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_notify.py -q`
Expected: `6 passed`

- [ ] **Step 5: Add the CLI command and gitignore entry**

```python
# src/project_registry/cli.py
def cmd_notify(args, paths, now) -> int:
    from .inbox import owner_inbox
    from .notify import notify_config, render_notification, send_ntfy
    digest_path = paths.build_digests_dir / f"{args.run}.md"
    if not digest_path.exists():
        print(f"no digest for run {args.run}", file=sys.stderr)
        return 1
    digest = digest_path.read_text(encoding="utf-8")
    inbox = owner_inbox(paths, load_registry(paths), now=now)
    message = render_notification(digest, inbox)
    config = notify_config(paths)
    result = {"run_id": args.run, "message": message, "sent": False, "configured": config is not None}
    if config is not None and not args.dry_run:
        result.update(send_ntfy(config["topic"], message, server=config["server"], title=message.splitlines()[0]))
    if not emit(result, args):
        print(message)
        print(f"[notify] configured={result['configured']} sent={result['sent']}")
    return 0
```

Register: `sub = add("notify", cmd_notify, "Push the run digest and owner inbox to the configured ntfy topic."); sub.add_argument("--run", required=True); sub.add_argument("--dry-run", action="store_true")`.

Append `data/build/notify.json` to `.gitignore` under the ephemeral-files comment. Add the cheatsheet line `registry notify --run <run> [--dry-run] # push digest + inbox to ntfy` to `CLAUDE.md` and README.

- [ ] **Step 6: CLI test**

```python
# append to tests/test_notify.py
import json
from project_registry.cli import main


def test_notify_cli_dry_run_prints_message(paths, write_project, capsys, monkeypatch):
    monkeypatch.setenv("REGISTRY_NTFY_TOPIC", "t")
    write_project(id="builder", purpose="p", repo="o/b")
    paths.build_digests_dir.mkdir(parents=True, exist_ok=True)
    (paths.build_digests_dir / "r1.md").write_text(DIGEST, encoding="utf-8")
    code = main(["--root", str(paths.root), "notify", "--run", "r1", "--dry-run", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["sent"] is False and payload["configured"] is True
    assert payload["message"].startswith("builder · completed · merged 2")
```

Run: `PYTHONPATH=src .venv/bin/python -m pytest -q` — Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/project_registry/notify.py src/project_registry/cli.py tests/test_notify.py .gitignore CLAUDE.md README.md
git commit -m "notify: push run digest and owner inbox to ntfy"
```

---

### Task 4: Wire the skill and launcher; document setup

**Files:**
- Modify: `.claude/skills/push-project/SKILL.md` Phase 5 step 8
- Modify: `scripts/run-build.sh`
- Modify: `docs/RUNBOOK.md` (new "Notifications" section), `CLAUDE.md` "Development" bullets

- [ ] **Step 1: Replace Phase 5 step 8 in SKILL.md**

Old:
```
8. Load PushNotification through ToolSearch when available and send `<project> · <outcome> · <merged n> · digest <path>`; otherwise print that line.
```
New:
```
8. Run `$REGISTRY_CLI notify --run "$RUN_ID" --json`. It renders the digest and owner inbox and pushes them to the configured ntfy topic; when nothing is configured it prints the message. Never retry or debug delivery inside a run.
```

- [ ] **Step 2: Pass the topic through the launcher**

In `scripts/run-build.sh`, after `export HOST`, add:
```bash
export REGISTRY_NTFY_TOPIC="${REGISTRY_NTFY_TOPIC:-}"
```
(The host's shell profile or Task Scheduler environment sets the real value; the launcher only forwards it.)

- [ ] **Step 3: RUNBOOK section**

```markdown
## Notifications

Every run ends with `registry notify --run <run-id>`: the first line is
`<project> · <outcome> · merged <n>`, then up to five merged PR links, any
rejections/denials, and the owner inbox (`registry owner-inbox`). Delivery is
an ntfy topic — install the ntfy app on the phone/Mac, subscribe to a private
topic name, and put it in the build host's environment as
`REGISTRY_NTFY_TOPIC` (or in `data/build/notify.json`, gitignored). Test with
`registry notify --run <any past run> --dry-run`. The topic name is a shared
secret: never commit it.
```

- [ ] **Step 4: Run `tests/test_skills.py` and `tests/test_docs.py`**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_skills.py tests/test_docs.py -q`
Expected: pass (the skill test parses `$REGISTRY_CLI notify` and requires `notify` to exist in the parser — it does after Task 3).

- [ ] **Step 5: Commit**

```bash
git add .claude/skills/push-project/SKILL.md scripts/run-build.sh docs/RUNBOOK.md CLAUDE.md
git commit -m "skill: notify at end of run; runbook: ntfy setup"
```

---

### Task 5: `build-retro` skill (Mac, weekly)

**Files:**
- Create: `.claude/skills/build-retro/SKILL.md`
- Modify: `docs/RUNBOOK.md` (cadence), `docs/observations/README.md` (retro file naming)
- Test: `tests/test_skills.py` already validates that every `registry …` reference in skills exists in the parser — extend its skill list if it enumerates files explicitly (read `tests/test_skills.py::_push_skill` and add a `_retro_skill` variant if needed).

- [ ] **Step 1: Write the skill**

```markdown
---
name: build-retro
description: Weekly retrospective of autonomous build runs — reads the journal, digests, and inbox, records what flowed and what stalled in docs/observations, and files brief/policy proposals for the owner; `/build-retro [--since YYYY-MM-DD]`.
---

# build-retro

Turn a window of build runs into two things: a factual observation file and proposals the owner can approve with one command. Never apply proposals, never edit registry YAML, never touch target repositories.

## Gather (read-only)

1. `registry build-report --since <date> --json` — runs by outcome/host/project, chunks merged/skipped/rejected, guard denials.
2. Every `data/build/digests/<run>.md` in the window.
3. `registry owner-inbox --json` and `registry build-queue --json`.
4. `registry sync` (needs `GITHUB_TOKEN`) then `registry prs --json`, to see which builder PRs merged, and `gh pr list --repo <repo> --state closed --search 'head:push/'` per active project for reverts or owner closes.

## Judge

For each project with at least one run in the window, answer in one line each:
- **Flow:** chunks merged per run; trend vs the previous window.
- **Stalls:** the outcome that ended each non-`completed` run, and whether it was infrastructure (Codex sandbox, quota, host), policy (`blocked_by_policy`, `needs_intent`), or quality (`reject`, revert, owner-closed PR).
- **Reviewer misses:** merged chunks later reverted, re-done, or contradicted by a following chunk.
- **Brief drift:** done criteria now met (the brief should retire them) or clearly unreachable under current `automation.allow`.

## Record

Write `docs/observations/<date>-retro.md` with a table per project (run id, outcome, merged, stalled-on, follow-up) and a "What changed because of this" list. Facts only in the tables; judgements in the closing section, each tied to a run id.

## Propose (never apply)

For every judgement that implies a curated change, file exactly one proposal:
- brief updates: `registry propose <id> --set brief.done_criteria='[...]' --rationale "retro <date>: ..."`
- policy: `registry propose <id> --set automation.allow='["personal_data"]' --rationale "..."` or `--set automation.budget.chunks_per_run=10`
- pausing a project that keeps failing on infrastructure: `--set automation.paused=true`

Finish by running `registry owner-inbox` and sending its output with `registry notify --run <latest run>` so the owner sees the retro's proposals in the same channel as run digests.
```

- [ ] **Step 2: Add naming + cadence to docs**

`docs/observations/README.md`: add `- Weekly build retros: \`<date>-retro.md\` (from the build-retro skill)`.
`docs/RUNBOOK.md`: under Observations add "Run `/build-retro` on the Mac every Monday after the first scheduled run of the week; approve or reject the proposals it files."

- [ ] **Step 3: Run skill/doc tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_skills.py tests/test_docs.py -q`
Expected: pass.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/build-retro/SKILL.md docs/observations/README.md docs/RUNBOOK.md
git commit -m "skill: build-retro weekly retrospective that files proposals"
```

---

### Task 6: Throughput proposals (owner decisions, applied through the workflow)

**Files:** `registry/projects/{interview-prep,ai-news-aggregator,ai-engineering-markets}.yaml` via CLI only; `DASHBOARD.md` regenerated.

- [ ] **Step 1: Budgets — apply now (owner agreed 2026-08-25)**

```bash
for p in interview-prep ai-news-aggregator ai-engineering-markets; do
  id=$(registry propose $p --set automation.budget.chunks_per_run=10 --set automation.budget.minutes_per_run=100 \
       --rationale "2026-08-25: d8fa did 6 chunks in 52 min; 100 min keeps a run under the session quota that crashed f90f" --json | jq -r .proposal_id)
  registry proposal-apply "$id" --approve
done
registry dashboard && registry validate
```

- [ ] **Step 2: `personal_data` for interview-prep — file only, owner decides**

```bash
registry propose interview-prep --set automation.allow='["personal_data"]' \
  --rationale "backlog-drain enrichment is blocked_by_policy; allowing personal_data lets the builder touch Roles/** and Pipeline.md under review"
```
Leave it pending; it will appear in `registry owner-inbox`.

- [ ] **Step 3: Commit**

```bash
git add registry/projects data/proposals data/audit_log.jsonl DASHBOARD.md
git commit -m "registry: raise build budgets to 10 chunks / 100 min; propose personal_data for interview-prep"
```

---

### Task 7: Deploy and verify end to end

- [ ] **Step 1:** Push the branch, open the PR against `main` (title "Autonomy layer: owner inbox, ntfy notify, build-retro, budgets"), merge it after the suite is green.
- [ ] **Step 2:** On the Linux build host (`/home/ubuntu/project-registry`): `git pull --ff-only`, `export BUILD_HOST_LABEL=<name> REGISTRY_NTFY_TOPIC=<topic>` in the scheduler's environment, `registry notify --run 20260825T120035Z-pc-d8fa --dry-run` (needs the digest recovered from that host first).
- [ ] **Step 3:** After the next scheduled run: confirm the phone notification arrived, `registry owner-inbox` matches the digest's section, and `registry build-report --since <yesterday>` shows the merges. Record in `docs/observations/<date>-first-build-run.md`.

---

## Self-review

- Spec coverage: inbox (Task 1–2), delivery (Task 3–4), retro (Task 5), throughput (Task 6), deploy (Task 7). Base-branch merging is intentionally out of scope (owner accepted merges to `main` with tags).
- Placeholders: none; every code step carries the code. `_read_lease` import and `load_snapshot` behaviour are the two places the implementer must confirm against the source before running Task 1.
- Type consistency: `owner_inbox(paths, registry, *, now, snapshot=None)` is used identically in Tasks 1, 2, 3; `render_notification(digest_text, inbox)` and `send_ntfy(topic, message, *, server, title)` match between Task 3's tests and implementation.
