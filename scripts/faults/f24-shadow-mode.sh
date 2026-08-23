#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F24 expected: shadow opens/reviews a PR but guard denies gh pr merge with shadow_mode"
scratch=$(fault_scratch f24)
trap 'cleanup_scratch "$scratch"' EXIT
root="$scratch/registry"
mkdir -p "$root"
write_guard_lease "$root" true 0 approve passed
assert_guard_denied "$root" "$root" "gh pr merge 7 --squash --repo kmadhok/build-sandbox-fault" shadow_mode
echo "F24 assert: guard exit=2 and rule=shadow_mode while dry_run=true"
