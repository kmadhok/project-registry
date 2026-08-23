#!/usr/bin/env bash

set -u

FAULTS_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
FAULT_REPO_ROOT=$(CDPATH= cd -- "$FAULTS_DIR/../.." && pwd)
FAULT_TMP=${FAULT_TMP:-${TMPDIR:-/tmp}/project-registry-faults}

mkdir -p "$FAULT_TMP"

fault_scratch() {
  mktemp -d "$FAULT_TMP/$1.XXXXXX"
}

cleanup_scratch() {
  local scratch=$1
  case "$scratch" in
    "$FAULT_TMP"/*) rm -rf -- "$scratch" ;;
    *) echo "refusing to clean non-FAULT_TMP path: $scratch" >&2; return 1 ;;
  esac
}

guard_payload() {
  local root=$1
  local cwd=$2
  local command=$3
  CLAUDE_PROJECT_DIR="$root" python3 "$FAULT_REPO_ROOT/scripts/build-guard.py" <<EOF
{"session_id":"fault-injection","cwd":"$cwd","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"$command"}}
EOF
}

write_guard_lease() {
  local root=$1
  local dry_run=${2:-false}
  local prs_open=${3:-0}
  local verdict=${4:-approve}
  local verify=${5:-passed}
  local merges=${6:-0}
  local chunks_per_run=${7:-6}
  mkdir -p "$root/data/build"
  cat >"$root/data/build/lease.json" <<EOF
{"run_id":"FAULT-RUN","host":"fault","project_id":"sandbox","repo":"kmadhok/build-sandbox-fault","status":"active","prs_open":$prs_open,"merges":$merges,"dry_run":$dry_run,"chunks":{"c1":{"pr_number":7,"branch":"push/FAULT-RUN-1-test","verify":"$verify","verdict":"$verdict","merged":false}},"budget":{"chunks_per_run":$chunks_per_run,"minutes_per_run":120},"reconcile_targets":[],"contract_forbidden_paths":[]}
EOF
}

assert_guard_denied() {
  local root=$1
  local cwd=$2
  local command=$3
  local rule=$4
  local output
  local status
  set +e
  output=$(guard_payload "$root" "$cwd" "$command" 2>&1)
  status=$?
  set -e
  [[ $status -eq 2 ]] || { echo "expected guard exit 2, got $status: $output" >&2; return 1; }
  [[ "$output" == *"build-guard: $rule"* ]] || { echo "missing rule $rule: $output" >&2; return 1; }
}

assert_guard_allowed() {
  local root=$1
  local cwd=$2
  local command=$3
  guard_payload "$root" "$cwd" "$command" >/dev/null
}
