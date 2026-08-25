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
        first = items[0]
        lines.append(
            f"Needs you ({len(items)}): {first['kind']} "
            f"{first['project_id'] or '-'} — {first['action']}"
        )
        for item in items[1:4]:
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
