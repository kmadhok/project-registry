#!/usr/bin/env bash
# Scheduled entry point for push-project on the PC (WSL Ubuntu).
# Registered in Windows Task Scheduler as "PushProject":
#   wsl.exe -d Ubuntu -u learnmsds -- bash -lc '~/Github/project-registry/scripts/run-push-project-wsl.sh'
# The scheduler log (one file per run under ~/Github/push-work/) is the only
# record of a run that crashes before the skill's Step 4 write-back.
set -u
export PATH="$HOME/bin:$HOME/.local/bin:$PATH"
export BASH_MAX_TIMEOUT_MS=2400000 BASH_DEFAULT_TIMEOUT_MS=1200000

REGISTRY_ROOT="$HOME/Github/project-registry"
LOG_DIR="$HOME/Github/push-work"
LOG="$LOG_DIR/$(date -u +%Y%m%d-%H%M%S)-scheduled.log"
mkdir -p "$LOG_DIR"

{
  echo "=== push-project scheduled run $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
  cd "$REGISTRY_ROOT" || exit 1
  git pull --ff-only
  claude -p "/push-project" --dangerously-skip-permissions --mcp-config .mcp.json
  echo "=== exit $? at $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} >> "$LOG" 2>&1
