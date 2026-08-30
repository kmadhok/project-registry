"""Everything that needs the owner, in one list, with the command that clears it."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .automation import build_queue
from .build_runs import load_state, read_events, _read_lease
from .github.sync import load_snapshot
from .proposals import last_owner_action, list_proposals
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
    queue = build_queue(
        registry, snapshot, states, now.date(),
        owner_actions=last_owner_action(paths),
    )
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
    resumed_after: set[str] = set()
    blocked: dict[str, set[str]] = {}
    for event in events:
        project_id = event.get("project_id")
        if event["type"] == "run_finished" and project_id:
            last_finished[project_id] = event
            resumed_after.discard(project_id)
        if event["type"] == "resumed" and project_id:
            # An owner resume after a failed run acknowledges it; stop nagging.
            resumed_after.add(project_id)
        if event["type"] == "chunk_skipped" and event.get("reason") == "blocked_by_policy" and project_id:
            classes = (event.get("detail") or {}).get("classes") or ["unknown"]
            blocked.setdefault(project_id, set()).update(classes)
    for project_id, event in sorted(last_finished.items()):
        if event.get("outcome") in FAILURE_OUTCOMES and project_id not in resumed_after:
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
            f"registry propose {project_id} --set automation.allow={','.join(sorted(classes))} --rationale ...",
        ))

    return {"generated_at": now.isoformat(), "count": len(items), "items": items}
