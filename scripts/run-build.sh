#!/usr/bin/env bash
# PC Task Scheduler: wsl.exe -d Ubuntu -u learnmsds -- bash -lc '~/Github/project-registry/scripts/run-build.sh'
# Mac: scripts/run-build.sh [project-id] [--force-named]
set -u

# This script `git pull`s its own checkout, so the file can be rewritten while
# it runs. Bash reads a script incrementally from its file offset; a rewrite
# mid-run would make it execute a fragment of the new file. Everything lives
# in main(), which bash parses completely before running, and the final line
# exits before bash reads anything further from the (possibly new) file.
main() {
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
  REGISTRY_PY="$REGISTRY_ROOT/.venv/bin/python"
  [[ -x "$REGISTRY_PY" ]] || REGISTRY_PY=python3

  # Refresh GitHub evidence before the run. Never blocks the build: a missing
  # token or a failed sync only means the dashboard and retro see stale data.
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
    # Pull, sync, and builder output also land in TAIL so the heartbeat can
    # quote its last lines; PIPESTATUS keeps each command's exit code, not tee's.
    TAIL="$LOG.tail"
    git pull --ff-only 2>&1 | tee "$TAIL"
    PULL_STATUS=${PIPESTATUS[0]}
    # data/build/active_host names the one scheduled host; any other non-Mac
    # host stands down silently (build start and notify --health enforce the same).
    ACTIVE_HOST="$(tr -d '[:space:]' < data/build/active_host 2>/dev/null)"
    if [[ -n "$ACTIVE_HOST" && "$ACTIVE_HOST" != "$HOST" && "$HOST" != mac ]]; then
      echo "run-build: $HOST is not the active build host ($ACTIVE_HOST); standing down"
      rm -f "$TAIL"
      exit 0
    fi
    if [[ $PULL_STATUS -eq 0 ]]; then
      refresh_evidence 2>&1 | tee -a "$TAIL"
      claude -p "$PROMPT" --dangerously-skip-permissions --mcp-config .mcp.json 2>&1 | tee -a "$TAIL"
      STATUS=${PIPESTATUS[0]}
    else
      echo "run-build: git pull failed (exit $PULL_STATUS); skipping the run" | tee -a "$TAIL"
      STATUS=1
    fi
    PYTHONPATH="$REGISTRY_ROOT/src" "$REGISTRY_PY" -m project_registry.cli notify --health \
      --exit-code "$STATUS" --log-tail "$TAIL" \
      || echo "run-build: heartbeat failed"
    rm -f "$TAIL"
    exit "$STATUS"
  } 2>&1 | tee "$LOG"
  exit "${PIPESTATUS[0]}"
}

main "$@"; exit
