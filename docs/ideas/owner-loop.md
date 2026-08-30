# The owner loop — decisions from a phone, everything else unattended

> **Status 2026-08-30.** Layers 1–2 shipped. The *command topic* half of
> layer 3 shipped in a narrower form: `registry request-run <id>` (called by
> `/inbox` for each project it released) publishes `{"action":"run",
> "project":"<id>"}` and `scripts/decision-listener.sh` on the build host
> starts the run — decision latency drops from "next cron" to a minute
> without any phone-side buttons. Approve/Reject buttons and a `decide`
> action remain the next step; the listener already has the shape to accept
> them. `blocked_by_policy` and `needs_intent` stops now park a project as
> `waiting_owner` (ADR-006 AD-13), so a missed notification costs nothing.

## Problem Statement

How might we let Kanu run three autonomous builders with the *only* human work being a handful of yes/no decisions a week — delivered to, and answerable from, his phone?

## Recommended Direction

Keep the builder exactly as it is and make the **decision surface** the product. Every run already ends in a digest and an owner inbox; the gap is delivery and response. Three layers, cheapest first:

1. **Notification that reads like a lock-screen card** (shipped 2026-08-27): title = `project: merged N — needs you (k)` / `nothing needed`; body = counts, chunk→PR lines, the stop reason, and a numbered NEEDS YOU list with the exact command per item; ntfy priority 4–5 and emoji tags when a human is needed; up to three "view PR" buttons. No web page, no dashboard.
2. **`/inbox` skill on the Mac** (shipped 2026-08-27): walks the inbox one item at a time — show the proposal diff, ask approve/reject/skip, run the registry command, commit, push. This is the desk version of the loop and the reference behaviour for the mobile one.
3. **Mobile decisions via ntfy action buttons** (next): each NEEDS YOU item gets `Approve` / `Reject` buttons whose ntfy `http` action POSTs `{"decision": "approve", "proposal": "<id>"}` to a private *command* topic. A listener on the VPS (`ntfy subscribe <command-topic>` → `registry proposal-apply|reject`) applies the decision, commits, pushes `main`, and posts the result back to the notification topic. No app, no server of ours, no inbound ports; the topic names are the only secret.

The reason this beats "build a mobile app" or "read GitHub on the phone": the registry already turns every situation into one command with one id. The phone only needs to send that id back. ntfy already does buttons, and the VPS already runs unattended.

## Key Assumptions to Validate

- [ ] ntfy `http` actions work from the iOS app for POST with a body — test with one throwaway topic and `curl`-equivalent listener before writing the listener.
- [ ] A decision can be applied on the VPS without a lease and without colliding with a running build — apply only when no `data/build/lease.json` exists; otherwise queue the decision to `data/build/decisions.jsonl` and let the next preflight apply it.
- [ ] Two topics are enough security: anyone with the command topic could approve proposals. Mitigate with a random 128-bit topic and, if needed, ntfy access tokens (`Authorization: Bearer`) — validate whether the free ntfy.sh tier supports tokens; if not, self-host ntfy on the VPS (single binary).
- [ ] The number of decisions stays small (≤5/week). If the inbox grows faster than that, the fix is in briefs and `automation.allow`, not in the UI.

## MVP Scope

- **In:** notification format (done); `/inbox` skill (done); `registry decide <proposal-id> approve|reject` (one command the listener calls, which also commits and pushes); `scripts/decision-listener.sh` running `ntfy subscribe` on the VPS under the same scheduler; Approve/Reject buttons on `proposal_pending` items only.
- **Out of MVP:** free-text answers to `needs_intent` from the phone (those need words — keep them on the Mac via `/inbox`), resume/pause buttons, any web UI.

## Not Doing (and Why)

- **A dashboard or web app** — the information fits in a notification; a page is a second place to look.
- **Slack/Notion/email delivery** — each adds an account and a format; ntfy is one HTTP POST and already on the phone.
- **Auto-approving proposals** — the whole design is that intent is human; the loop's value is that the human step is tiny, not absent.
- **Reading GitHub PRs on the phone** — PR buttons exist for the curious, but the digest + reviewer verdict is the review; if a merged chunk is wrong, `git revert <sha>` from the digest is the fix.

## Open Questions

- Should `blocked_by_policy` items get an "Allow class" button, or stay Mac-only because widening `automation.allow` deserves a look at the diff first? (Default: Mac-only.)
- Where does the listener log decisions so a lost notification is recoverable? (Default: `data/build/decisions.jsonl`, committed with the writeback.)
