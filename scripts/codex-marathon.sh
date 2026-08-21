#!/usr/bin/env bash
#
# codex-marathon.sh — run GPT-5.6 (Codex CLI) unattended over docs/codex-marathon/QUEUE.md.
#
# Each iteration is a fresh `codex exec` session fed PROMPT.md; state lives in
# QUEUE.md / PROGRESS.md / git, so sessions chain without shared context.
# Stops when: .marathon-stop exists, the queue has no open `- [ ]` items,
# MAX_SESSIONS is reached, or codex fails MAX_CONSEC_FAILS times in a row
# (auth gone, weekly usage exhausted, network down).
#
# Usage:
#   scripts/codex-marathon.sh                 # defaults below
#   MAX_SESSIONS=60 CODEX_EFFORT=high scripts/codex-marathon.sh
#   SANDBOXED=1 scripts/codex-marathon.sh     # workspace-write sandbox + network instead of bypass
#   touch .marathon-stop                      # graceful stop after the current session
#
# Run it detached so it survives the terminal:
#   nohup scripts/codex-marathon.sh > .codex-marathon/runner.out 2>&1 &
#   tail -f .codex-marathon/runner.out

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT="$ROOT/docs/codex-marathon/PROMPT.md"
QUEUE="$ROOT/docs/codex-marathon/QUEUE.md"
LOGDIR="$ROOT/.codex-marathon"
STOPFILE="$ROOT/.marathon-stop"

MAX_SESSIONS="${MAX_SESSIONS:-60}"
MAX_CONSEC_FAILS="${MAX_CONSEC_FAILS:-3}"
FAIL_SLEEP="${FAIL_SLEEP:-180}"
MODEL="${CODEX_MODEL:-gpt-5.6-sol}"
EFFORT="${CODEX_EFFORT:-high}"
SANDBOXED="${SANDBOXED:-0}"

mkdir -p "$LOGDIR"
log() { printf '[%s] [runner] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

command -v codex >/dev/null 2>&1 || { log "ERROR codex CLI not on PATH"; exit 4; }
codex login status >/dev/null 2>&1 || { log "ERROR codex not authenticated (codex login)"; exit 4; }
[ -f "$PROMPT" ] || { log "ERROR missing $PROMPT"; exit 2; }
[ -f "$QUEUE" ]  || { log "ERROR missing $QUEUE"; exit 2; }
[ -x "$ROOT/.venv/bin/python" ] || { log "ERROR .venv missing — tasks need .venv/bin/python"; exit 2; }

if [ "$SANDBOXED" = "1" ]; then
  MODE_FLAGS=(-s workspace-write -c 'sandbox_workspace_write.network_access=true')
else
  MODE_FLAGS=(--dangerously-bypass-approvals-and-sandbox)
fi

open_items() { grep -c '^- \[ \]' "$QUEUE" 2>/dev/null || echo 0; }

log "start model=$MODEL effort=$EFFORT max_sessions=$MAX_SESSIONS sandboxed=$SANDBOXED open_items=$(open_items)"
rm -f "$STOPFILE"

consec_fails=0
for ((i = 1; i <= MAX_SESSIONS; i++)); do
  if [ -f "$STOPFILE" ]; then log "stop file present — exiting after $((i-1)) sessions"; break; fi
  if [ "$(open_items)" -eq 0 ]; then log "queue has no open items — exiting"; break; fi

  LAST="$LOGDIR/session-$(printf '%03d' "$i").last.md"
  OUT="$LOGDIR/session-$(printf '%03d' "$i").log"
  log "session $i begin (open_items=$(open_items)) → $OUT"

  codex exec "${MODE_FLAGS[@]}" \
    -C "$ROOT" -m "$MODEL" -c "model_reasoning_effort=\"$EFFORT\"" \
    --output-last-message "$LAST" - < "$PROMPT" > "$OUT" 2>&1
  rc=$?

  if [ $rc -eq 0 ]; then
    consec_fails=0
    log "session $i ok: $(tail -n 1 "$LAST" 2>/dev/null | cut -c1-200)"
  else
    consec_fails=$((consec_fails + 1))
    log "session $i FAILED rc=$rc (consecutive=$consec_fails) — tail:"
    tail -n 15 "$OUT" | sed 's/^/    /'
    if [ "$consec_fails" -ge "$MAX_CONSEC_FAILS" ]; then
      log "giving up after $consec_fails consecutive failures (usage exhausted? auth? network?)"
      break
    fi
    log "sleeping ${FAIL_SLEEP}s before retry"
    sleep "$FAIL_SLEEP"
    continue
  fi
  sleep 5
done

log "end open_items=$(open_items) stopfile=$([ -f "$STOPFILE" ] && echo yes || echo no)"
log "branch: $(git -C "$ROOT" branch --show-current)  last commits:"
git -C "$ROOT" log --oneline -8 | sed 's/^/    /'
