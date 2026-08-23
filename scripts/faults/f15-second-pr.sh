#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F15 expected: a second gh pr create in one chunk is denied"
scratch=$(fault_scratch f15)
trap 'cleanup_scratch "$scratch"' EXIT
root="$scratch/registry"
mkdir -p "$root"
write_guard_lease "$root" false 1
assert_guard_denied "$root" "$root" "gh pr create --repo kmadhok/build-sandbox-fault --head push/FAULT-RUN-1-test" one_pr_per_chunk
echo "F15 assert: guard exit=2 and rule=one_pr_per_chunk"
