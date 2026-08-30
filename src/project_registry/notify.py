"""Push a run summary to the owner's phone. ntfy only; stdlib only; never raises on delivery.

The message is built for a lock screen: one-line title that says whether the
owner is needed, a short body, emoji tags for the outcome, higher priority when
something needs a human, and up to three "view" buttons for the merged PRs.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .storage import Paths, load_registry, read_json

DEFAULT_SERVER = "https://ntfy.sh"
MAX_ACTIONS = 3  # ntfy allows at most three action buttons per message.

FAILURE_OUTCOMES = {
    "crashed", "aborted", "codex_unavailable", "contract_broken",
    "baseline_red", "registry_dirty", "preflight_failed", "clone_failed",
}
HUMAN_OUTCOMES = {"needs_intent", "blocked_by_policy", "paused"}


def notify_config(paths: Paths) -> dict[str, Any] | None:
    raw = read_json(paths.build_dir / "notify.json") or {}
    topic = os.environ.get("REGISTRY_NTFY_TOPIC") or raw.get("topic")
    if not topic:
        return None
    command_topic = (
        os.environ.get("REGISTRY_NTFY_COMMAND_TOPIC")
        or raw.get("command_topic")
        or None
    )
    return {
        "topic": topic,
        "server": raw.get("server") or DEFAULT_SERVER,
        "command_topic": command_topic,
    }


def _field(digest: str, name: str) -> str:
    match = re.search(rf"^- {name}: (.+)$", digest, re.MULTILINE)
    return match.group(1).strip() if match else "?"


def _section(digest: str, title: str) -> list[str]:
    match = re.search(
        rf"^## {re.escape(title)}\n\n(.*?)(?:\n## |\Z)", digest, re.DOTALL | re.MULTILINE
    )
    if not match:
        return []
    return [line[2:] for line in match.group(1).splitlines() if line.startswith("- ")]


def _summary(digest: str) -> str:
    match = re.search(r"^## Summary\n\n(.+?)(?:\n\n|\Z)", digest, re.DOTALL | re.MULTILINE)
    if not match:
        return ""
    text = " ".join(match.group(1).split())
    return text if len(text) <= 140 else text[:137].rstrip() + "..."


def _outcome_progress(digest: str) -> str | None:
    section = re.search(
        r"^## Outcome\s*\n(.*?)(?:\n## |\Z)",
        digest,
        re.DOTALL | re.MULTILINE,
    )
    if not section:
        return None
    first = next((line.strip() for line in section.group(1).splitlines() if line.strip()), "")
    match = re.match(r"(?:-\s*)?criteria:\s*(\d+)/(\d+) met\b", first)
    return f"{match.group(1)}/{match.group(2)}" if match else None


def _merged(digest: str) -> list[tuple[str, str]]:
    """Return (chunk id, PR url) pairs from the digest's merged section."""
    pairs = []
    for entry in _section(digest, "Merged chunks"):
        chunk, _, rest = entry.partition(": ")
        url = rest.split(" — ")[0].strip()
        pairs.append((chunk, url))
    return pairs


def _pr_number(url: str) -> str | None:
    match = re.search(r"/pull/(\d+)$", url)
    return match.group(1) if match else None


def _repo_url(url: str) -> str | None:
    match = re.match(r"(https://github\.com/[^/]+/[^/]+)/pull/\d+$", url)
    return match.group(1) if match else None


def _clean_label(text: str) -> str:
    return re.sub(r"[,;]", " ", text).strip()


def build_notification(digest: str, inbox: dict[str, Any]) -> dict[str, Any]:
    """Assemble title, body, and ntfy metadata from a digest and the owner inbox."""
    project = _field(digest, "Project")
    outcome = _field(digest, "Outcome")
    merged = _merged(digest)
    skips = _section(digest, "Rejections and skips")
    denials = _section(digest, "Guard denials")
    items = list(inbox.get("items") or [])
    failed = outcome in FAILURE_OUTCOMES

    if failed:
        headline = f"{outcome} — needs you"
    elif items:
        headline = f"merged {len(merged)} — needs you ({len(items)})"
    elif outcome in HUMAN_OUTCOMES:
        headline = f"merged {len(merged)} — stopped: {outcome}"
    else:
        headline = f"merged {len(merged)} — nothing needed"
    title = f"{project}: {headline}"
    progress = _outcome_progress(digest)
    if progress:
        title += f" · criteria {progress}"

    body: list[str] = []
    stats = f"Merged {len(merged)} · rejected/skipped {len(skips)} · guard denials {len(denials)}"
    body.append(stats)
    for chunk, url in merged[:MAX_ACTIONS]:
        number = _pr_number(url)
        body.append(f"  chunk {chunk} → PR #{number}" if number else f"  chunk {chunk} → {url}")
    if len(merged) > MAX_ACTIONS:
        body.append(f"  +{len(merged) - MAX_ACTIONS} more")
    summary = _summary(digest)
    if outcome not in {"completed", "budget_exhausted"} and summary:
        body.append(f"Stopped: {outcome} — {summary}")
    if items:
        body.append("")
        body.append(f"NEEDS YOU ({len(items)})")
        for index, item in enumerate(items[:3], start=1):
            body.append(f"{index}. {item['kind']} · {item['project_id'] or '-'}")
            body.append(f"   {item['action']}")
        if len(items) > 3:
            body.append(f"   +{len(items) - 3} more — run: registry owner-inbox")
    else:
        body.append("Nothing needs you.")

    if failed:
        priority, tag = 5, "rotating_light"
    elif outcome in HUMAN_OUTCOMES or items:
        priority, tag = 4, "raised_hand"
    elif outcome == "shadow_completed":
        priority, tag = 3, "eyes"
    else:
        priority, tag = 3, "white_check_mark"
    tags = [tag] + (["inbox_tray"] if items else [])

    actions = [
        f"view, PR #{_pr_number(url)}, {url}"
        for _chunk, url in merged[:MAX_ACTIONS] if _pr_number(url)
    ]
    click = merged[0][1] if merged else (_repo_url(merged[0][1]) if merged else None)

    return {
        "title": _clean_label(title),
        "body": "\n".join(body),
        "priority": priority,
        "tags": tags,
        "click": click,
        "actions": actions,
    }


def build_inbox_notification(inbox: dict[str, Any]) -> dict[str, Any]:
    """Assemble a lock-screen notification from the owner inbox alone."""
    count = int(inbox.get("count") or 0)
    items = list(inbox.get("items") or [])
    title = f"Needs you ({count})" if count > 0 else "Nothing needed"
    body: list[str] = []
    for item in items[:8]:
        body.append(
            f"[{item['kind']}] {item.get('project_id') or '-'}: {item['summary']}"
        )
        body.append(f"    -> {item['action']}")
    if count > 8:
        body.append(f"+{count - 8} more — registry owner-inbox")
    if not body:
        body.append("Nothing needs you.")
    return {
        "title": _clean_label(title),
        "body": "\n".join(body),
        "priority": 4 if count > 0 else 3,
        "tags": ["warning"] if count > 0 else ["white_check_mark"],
        "click": None,
        "actions": [],
    }


def render_notification(digest: str, inbox: dict[str, Any]) -> str:
    """Plain-text form (title + body) for terminals and dry runs."""
    notification = build_notification(digest, inbox)
    return notification["title"] + "\n" + notification["body"]


def _action_payload(spec: str) -> dict[str, str] | None:
    """Turn 'view, label, url' into ntfy's JSON action object."""
    parts = [part.strip() for part in spec.split(",", 2)]
    if len(parts) != 3 or parts[0] != "view":
        return None
    return {"action": "view", "label": parts[1], "url": parts[2]}


def ntfy_payload(topic: str, notification: dict[str, Any]) -> dict[str, Any]:
    """The JSON publish body. Used instead of headers so UTF-8 titles survive
    (HTTP headers are latin-1; an em dash in a Title header raises)."""
    payload: dict[str, Any] = {
        "topic": topic,
        "title": notification["title"],
        "message": notification["body"],
    }
    if notification.get("priority"):
        payload["priority"] = int(notification["priority"])
    if notification.get("tags"):
        payload["tags"] = list(notification["tags"])
    if notification.get("click"):
        payload["click"] = notification["click"]
    actions = [
        action for action in map(_action_payload, notification.get("actions") or [])
        if action is not None
    ][:MAX_ACTIONS]
    if actions:
        payload["actions"] = actions
    return payload


def _post_json(server: str, payload: dict) -> dict:
    """POST a JSON payload to an ntfy server. Returns a result dict; never raises."""
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            server.rstrip("/"),
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return {"sent": True, "status": response.status}
    except (urllib.error.URLError, OSError, ValueError, UnicodeError) as error:
        return {"sent": False, "error": str(error)}


def send_ntfy(topic: str, notification: dict[str, Any] | str, *, server: str) -> dict[str, Any]:
    """POST one notification to an ntfy topic as JSON. Returns a result dict; never raises."""
    if isinstance(notification, str):
        notification = {"title": notification.splitlines()[0], "body": notification}
    return _post_json(server, ntfy_payload(topic, notification))


def request_run(
    paths: Paths,
    project_id: str,
    *,
    reason: str = "",
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Request that the build host start a run for a registered project."""
    if project_id not in load_registry(paths):
        raise KeyError(project_id)
    requested_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(
        dt.timezone.utc
    ).isoformat()
    command = {
        "action": "run",
        "project": project_id,
        "requested_at": requested_at,
        "reason": reason,
    }
    message = json.dumps(command)
    config = notify_config(paths)
    if config is None or not config.get("command_topic"):
        return {
            "project_id": project_id,
            "configured": False,
            "sent": False,
            "message": message,
        }
    payload = {
        "topic": config["command_topic"],
        "title": f"run {project_id}",
        "message": message,
        "tags": ["arrow_forward"],
    }
    result = _post_json(config["server"], payload)
    return {
        "project_id": project_id,
        "configured": True,
        "message": message,
        **result,
    }
