#!/usr/bin/env bash
# PC Task Scheduler: wsl.exe -d Ubuntu -u learnmsds -- bash -lc '~/Github/project-registry/scripts/run-build.sh'
# Mac: scripts/run-build.sh [project-id] [--force-named]
set -u

case "$(uname -s)" in
  Darwin) HOST=mac ;;
  Linux)
    HOST="${BUILD_HOST_LABEL:-$(hostname -s | tr 'A-Z' 'a-z')}"
    export PATH="$HOME/bin:$HOME/.local/bin:$PATH"
    ;;
  *)
    echo "Unsupported host: $(uname -s)" >&2
    exit 2
    ;;
esac
export HOST
export REGISTRY_NTFY_TOPIC="${REGISTRY_NTFY_TOPIC:-}"
export BASH_MAX_TIMEOUT_MS=2400000 BASH_DEFAULT_TIMEOUT_MS=1200000

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$REGISTRY_ROOT/../build-work/logs"

if [[ "${1-}" == "--dry-run" ]]; then
  shift
  PROMPT="/push-project${*:+ $*}"
  printf 'cd %q && git pull --ff-only && claude -p %q --dangerously-skip-permissions --mcp-config .mcp.json\n' \
    "$REGISTRY_ROOT" "$PROMPT"
  exit 0
fi

PROMPT="/push-project${*:+ $*}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date -u +%Y%m%d-%H%M%S)-$HOST.log"

{
  cd "$REGISTRY_ROOT" || exit 1
  git pull --ff-only && \
    claude -p "$PROMPT" --dangerously-skip-permissions --mcp-config .mcp.json
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
