#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F1 expected: build start exits immediately with outcome lease_held"
scratch=$(fault_scratch f01)
trap 'cleanup_scratch "$scratch"' EXIT
clone="$scratch/registry"
git clone -q "$FAULT_REPO_ROOT" "$clone"
git -C "$clone" switch -q main
mkdir -p "$clone/data/build"
started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat >"$clone/data/build/lease.json" <<EOF
{"run_id":"FAKE-UNEXPIRED","host":"fault","pid":1,"project_id":"sandbox","repo":"kmadhok/build-sandbox-fault","dry_run":false,"started_at":"$started_at","ttl_seconds":86400,"status":"active","prs_open":0,"merges":0,"chunks":{},"allow":[],"budget":{"chunks_per_run":1,"minutes_per_run":1},"contract_forbidden_paths":[],"reconcile_targets":[]}
EOF
set +e
output=$("$FAULT_REPO_ROOT/.venv/bin/registry" --root "$clone" build start --host mac --json 2>&1)
status=$?
set -e
[[ $status -eq 3 ]] || { echo "F1 assert: expected exit 3, got $status: $output" >&2; exit 1; }
outcome=$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("outcome"))')
[[ "$outcome" == lease_held ]] || { echo "F1 assert: expected lease_held, got $outcome" >&2; exit 1; }
echo "F1 assert: outcome=lease_held and existing lease remained authoritative"
