#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F16 expected: merge before reviewer approval is denied"
scratch=$(fault_scratch f16)
trap 'cleanup_scratch "$scratch"' EXIT
root="$scratch/registry"
mkdir -p "$root"
write_guard_lease "$root" false 0 pending passed
assert_guard_denied "$root" "$root" "gh pr merge 7 --squash --repo kmadhok/build-sandbox-fault" unverified_merge
echo "F16 assert: guard exit=2 and rule=unverified_merge"
