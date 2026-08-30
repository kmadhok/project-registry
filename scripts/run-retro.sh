#!/usr/bin/env bash
# Weekly retrospective launcher: scripts/run-retro.sh [--since YYYY-MM-DD] [--dry-run]
# Schedule it after the week's first build (RUNBOOK: Mondays 13:00Z on the
# build host). It refuses to run while a build lease exists.
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
export BASH_MAX_TIMEOUT_MS=1200000 BASH_DEFAULT_TIMEOUT_MS=600000

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$REGISTRY_ROOT/../build-work/logs"
REGISTRY_PY="$REGISTRY_ROOT/.venv/bin/python"
[[ -x "$REGISTRY_PY" ]] || REGISTRY_PY=python3

refresh_evidence() {
  if [[ -z "${GITHUB_TOKEN:-}" ]]; then
    echo "registry sync skipped: GITHUB_TOKEN unset"
    return 0
  fi
  PYTHONPATH="$REGISTRY_ROOT/src" "$REGISTRY_PY" -m project_registry.cli sync \
    || echo "registry sync failed; continuing without fresh evidence"
  return 0
}

if [[ "${1-}" == "--dry-run" ]]; then
  shift
  PROMPT="/build-retro${*:+ $*}"
  printf 'cd %q && git pull --ff-only && claude -p %q --dangerously-skip-permissions --mcp-config .mcp.json\n' \
    "$REGISTRY_ROOT" "$PROMPT"
  exit 0
fi

if [[ -e "$REGISTRY_ROOT/data/build/lease.json" ]]; then
  echo "retro skipped: a build lease exists ($REGISTRY_ROOT/data/build/lease.json)"
  exit 3
fi

PROMPT="/build-retro${*:+ $*}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/$(date -u +%Y%m%d-%H%M%S)-retro-$HOST.log"

{
  cd "$REGISTRY_ROOT" || exit 1
  git pull --ff-only && \
    refresh_evidence && \
    claude -p "$PROMPT" --dangerously-skip-permissions --mcp-config .mcp.json
} 2>&1 | tee "$LOG"
exit "${PIPESTATUS[0]}"
