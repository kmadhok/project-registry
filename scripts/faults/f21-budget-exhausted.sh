#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F21 expected: chunks_per_run=1 permits the first approved merge and then requires budget_exhausted"
scratch=$(fault_scratch f21)
trap 'cleanup_scratch "$scratch"' EXIT
root="$scratch/registry"
git clone -q "$FAULT_REPO_ROOT" "$root"
git -C "$root" switch -q main
write_guard_lease "$root" false 0 approve passed 0 1
assert_guard_allowed "$root" "$root" "gh pr merge 7 --squash --repo kmadhok/build-sandbox-fault"
python3 - "$root/data/build/lease.json" <<'PY'
import json
import sys
from pathlib import Path

lease = json.loads(Path(sys.argv[1]).read_text())
assert lease["budget"]["chunks_per_run"] == 1
assert lease["merges"] + 1 >= lease["budget"]["chunks_per_run"]
lease["project_id"] = "project-registry"
lease["repo"] = "kmadhok/project-registry"
lease["merges"] = 1
lease["chunks"]["c1"]["merged"] = True
Path(sys.argv[1]).write_text(json.dumps(lease))
PY
output=$("$FAULT_REPO_ROOT/.venv/bin/registry" --root "$root" build finish \
  FAULT-RUN --outcome budget_exhausted --json)
grep -q '"status": "finalize_pending"' "$root/data/build/lease.json" || python3 -c "import json;assert json.load(open('$root/data/build/lease.json'))['status']=='finalize_pending'" || { echo "F21 assert: lease not finalize_pending after finish" >&2; exit 1; }
"$FAULT_REPO_ROOT/.venv/bin/registry" --root "$root" build finish FAULT-RUN --confirm-writeback --json >/dev/null
[[ ! -e "$root/data/build/lease.json" ]] || { echo "F21 assert: lease was not released after confirm-writeback" >&2; exit 1; }
grep -Eq '"outcome"[[:space:]]*:[[:space:]]*"budget_exhausted"' "$root/data/build/runs.jsonl"
grep -q -- '- Outcome: budget_exhausted' "$root/data/build/digests/FAULT-RUN.md"
printf '%s' "$output" | python3 -c 'import json,sys; assert json.load(sys.stdin)["state_after"]["last_run_id"] == "FAULT-RUN"'
echo "F21 assert: first merge allowed; finish leaves finalize_pending; confirm-writeback releases; outcome and digest are budget_exhausted"
