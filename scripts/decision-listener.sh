#!/usr/bin/env bash
# Decision listener for the build host: subscribes to the private ntfy
# *command* topic and starts a named build for {"action":"run","project":"<id>"}.
# Published by `registry request-run <id>` (the Mac's /inbox after an
# unblocking decision). Runs under systemd (docs/RUNBOOK.md, "Owner loop").
#
# Safety: the topic name is the only credential, so every message is
# validated — the project id must match the registry's own list — and the
# only action ever taken is `scripts/run-build.sh <project>`, which goes
# through `registry build start` (lease, eligibility, guard). Overlapping
# requests serialize here and a held lease makes the second one a cheap
# `lease_held` exit.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REGISTRY_ROOT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$REGISTRY_ROOT/../build-work/logs"
REGISTRY_PY="$REGISTRY_ROOT/.venv/bin/python"
[[ -x "$REGISTRY_PY" ]] || REGISTRY_PY=python3
CONFIG="$REGISTRY_ROOT/data/build/notify.json"

read_config() {
  # Prints "<server> <command_topic>"; env overrides the file.
  "$REGISTRY_PY" - "$CONFIG" <<'PY'
import json, os, sys
raw = {}
try:
    raw = json.load(open(sys.argv[1], encoding="utf-8"))
except (OSError, ValueError):
    pass
server = (raw.get("server") or "https://ntfy.sh").rstrip("/")
topic = os.environ.get("REGISTRY_NTFY_COMMAND_TOPIC") or raw.get("command_topic") or ""
print(server, topic)
PY
}

read -r SERVER TOPIC < <(read_config)
if [[ -z "${TOPIC:-}" ]]; then
  echo "decision-listener: no command topic (data/build/notify.json command_topic or REGISTRY_NTFY_COMMAND_TOPIC)" >&2
  exit 2
fi

mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/listener.log"
log() { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG"; }

# Extract a validated project id from one ntfy JSON event line, or nothing.
project_from_event() {
  "$REGISTRY_PY" - "$REGISTRY_ROOT" <<'PY'
import json, re, subprocess, sys
root = sys.argv[1]
line = sys.stdin.readline()
try:
    event = json.loads(line)
except ValueError:
    sys.exit(0)
if event.get("event") != "message":
    sys.exit(0)
try:
    command = json.loads(event.get("message") or "")
except ValueError:
    sys.exit(0)
if not isinstance(command, dict) or command.get("action") != "run":
    sys.exit(0)
project = command.get("project")
if not isinstance(project, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", project):
    sys.exit(0)
listing = subprocess.run(
    [sys.executable, "-m", "project_registry.cli", "--root", root, "list", "--json"],
    capture_output=True, text=True, env={"PYTHONPATH": f"{root}/src", "PATH": "/usr/bin:/bin"},
)
if listing.returncode != 0:
    sys.exit(0)
data = json.loads(listing.stdout)
items = data.get("projects", data.get("items", data)) if isinstance(data, dict) else data
ids = {item.get("id") for item in items if isinstance(item, dict)}
if project in ids:
    print(project)
PY
}

log "listening on $SERVER/$TOPIC"
while true; do
  # -N disables buffering so each event line arrives as it is published.
  curl -sN --retry 0 "$SERVER/$TOPIC/json" 2>>"$LOG" | while IFS= read -r line; do
    project=$(printf '%s\n' "$line" | project_from_event)
    [[ -n "$project" ]] || continue
    log "run requested: $project"
    if bash "$REGISTRY_ROOT/scripts/run-build.sh" "$project" >>"$LOG" 2>&1; then
      log "run finished: $project"
    else
      log "run exited non-zero: $project (see $LOG)"
    fi
  done
  log "subscription ended; reconnecting in 15s"
  sleep 15
done
