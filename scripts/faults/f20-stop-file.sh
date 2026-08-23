#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F20 expected: STOP blocks a new run; an in-flight chunk may still verify before orchestration skips merge"
scratch=$(fault_scratch f20)
trap 'cleanup_scratch "$scratch"' EXIT
clone="$scratch/registry"
git clone -q "$FAULT_REPO_ROOT" "$clone"
git -C "$clone" switch -q main
mkdir -p "$clone/data/build"
: >"$clone/data/build/STOP"
set +e
output=$("$FAULT_REPO_ROOT/.venv/bin/registry" --root "$clone" build start --host mac --json 2>&1)
status=$?
set -e
[[ $status -eq 3 ]] || { echo "F20 assert: expected exit 3, got $status: $output" >&2; exit 1; }
outcome=$(printf '%s' "$output" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("outcome"))')
[[ "$outcome" == stopped ]] || { echo "F20 assert: expected stopped, got $outcome" >&2; exit 1; }
rm "$clone/data/build/STOP"
write_guard_lease "$clone"
: >"$clone/data/build/STOP"
assert_guard_allowed "$clone" "$clone" "python3 -m pytest -q"
echo "F20 assert: startup outcome=stopped; guard allows the current chunk's verification command"
