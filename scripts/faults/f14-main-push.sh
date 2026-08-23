#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F14 expected: injected git push origin main is denied with a guard event"
scratch=$(fault_scratch f14)
trap 'cleanup_scratch "$scratch"' EXIT
root="$scratch/registry"
clone="$scratch/sandbox"
mkdir -p "$clone"
write_guard_lease "$root"
assert_guard_denied "$root" "$clone" "git push origin main" non_push_branch
grep -q '"reason":"non_push_branch"' "$root/data/build/runs.jsonl"
echo "F14 assert: guard exit=2, rule=non_push_branch, denial journaled"
