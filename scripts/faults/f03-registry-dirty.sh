#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F3 expected: dirty registry refuses startup with outcome registry_dirty and creates no lease"
scratch=$(fault_scratch f03)
trap 'cleanup_scratch "$scratch"' EXIT
clone="$scratch/registry"
git clone -q "$FAULT_REPO_ROOT" "$clone"
git -C "$clone" switch -q main
printf 'fault injection\n' >"$clone/registry/projects/fault-untracked.tmp"
set +e
output=$("$FAULT_REPO_ROOT/.venv/bin/registry" --root "$clone" build start --host mac --json 2>&1)
status=$?
set -e
[[ $status -eq 3 ]] || { echo "F3 assert: expected exit 3, got $status: $output" >&2; exit 1; }
outcome=$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("outcome"))')
[[ "$outcome" == registry_dirty ]] || { echo "F3 assert: expected registry_dirty, got $outcome" >&2; exit 1; }
[[ ! -e "$clone/data/build/lease.json" ]] || { echo "F3 assert: lease was created" >&2; exit 1; }
echo "F3 assert: outcome=registry_dirty and no lease exists"
