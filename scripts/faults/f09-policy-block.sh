#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

echo "F9 expected: a run that stops blocked_by_policy must journal the blocked classes; the project then waits on the owner (waiting_owner + inbox item) and the inbox's allow command releases it"
scratch=$(fault_scratch f09)
trap 'cleanup_scratch "$scratch"' EXIT
root="$scratch/registry"
git clone -q "$FAULT_REPO_ROOT" "$root"
git -C "$root" switch -q main
REGISTRY="$FAULT_REPO_ROOT/.venv/bin/registry"
project=ai-news-aggregator

# Start from an empty build state so the real state.json cannot mask the fault.
mkdir -p "$root/data/build"
printf '{}' >"$root/data/build/state.json"
write_guard_lease "$root" false 0 approve passed 0 6
python3 - "$root/data/build/lease.json" "$project" <<'PY'
import json
import sys
from pathlib import Path

lease = json.loads(Path(sys.argv[1]).read_text())
lease["project_id"] = sys.argv[2]
lease["repo"] = "kmadhok/AI-News-Aggregator"
Path(sys.argv[1]).write_text(json.dumps(lease))
PY

queue_state() {
  "$REGISTRY" --root "$root" build-queue --json | python3 -c '
import json, sys
queue = json.load(sys.stdin)
print(next(c["state"] for c in queue["candidates"] if c["project_id"] == sys.argv[1]))
' "$1"
}

# 1. Finishing blocked_by_policy without journaling the blocked items is refused.
set +e
output=$("$REGISTRY" --root "$root" build finish FAULT-RUN --outcome blocked_by_policy --summary "fault" --json 2>&1)
status=$?
set -e
[[ $status -eq 2 ]] || { echo "F9 assert: expected exit 2 for an unexplained blocked_by_policy, got $status: $output" >&2; exit 1; }
[[ "$output" == *"chunk_skipped --chunk-id"* ]] || { echo "F9 assert: refusal did not name the missing event: $output" >&2; exit 1; }
[[ ! -s "$root/data/build/digests/FAULT-RUN.md" ]] || { echo "F9 assert: digest written despite the refusal" >&2; exit 1; }

# 2. Journal the blocked item, finish, release the lease: the fold parks the project.
"$REGISTRY" --root "$root" build event FAULT-RUN chunk_skipped --chunk-id 3 --reason blocked_by_policy --detail classes=generated_data >/dev/null
"$REGISTRY" --root "$root" build finish FAULT-RUN --outcome blocked_by_policy --summary "fault: remaining items need generated_data" --json >/dev/null
"$REGISTRY" --root "$root" build finish FAULT-RUN --confirm-writeback --json >/dev/null
state=$(queue_state "$project")
[[ "$state" == waiting_owner ]] || { echo "F9 assert: expected waiting_owner after the policy stop, got $state" >&2; exit 1; }
"$REGISTRY" --root "$root" build-readiness "$project" | grep -q 'waiting on .*generated_data' \
  || { echo "F9 assert: build-readiness does not show the waiting line" >&2; exit 1; }

# 3. The inbox carries the releasing command; running it verbatim releases the project.
action=$("$REGISTRY" --root "$root" owner-inbox --json | python3 -c '
import json, sys
inbox = json.load(sys.stdin)
print(next(i["action"] for i in inbox["items"] if i["kind"] == "blocked_by_policy" and i["project_id"] == sys.argv[1]))
' "$project")
[[ "$action" == "registry propose $project --set automation.allow="*"generated_data"*" --rationale ..." ]] \
  || { echo "F9 assert: unexpected inbox action: $action" >&2; exit 1; }
args=${action#registry }
args=${args% --rationale ...}
# shellcheck disable=SC2086
proposal_id=$("$REGISTRY" --root "$root" $args --rationale "F9: owner allows the blocked class" --json \
  | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])')
"$REGISTRY" --root "$root" proposal-apply "$proposal_id" --approve >/dev/null
state=$(queue_state "$project")
[[ "$state" == ready ]] || { echo "F9 assert: expected ready after allowing the class, got $state" >&2; exit 1; }
remaining=$("$REGISTRY" --root "$root" owner-inbox --json | python3 -c '
import json, sys
inbox = json.load(sys.stdin)
print(sum(1 for i in inbox["items"] if i["kind"] == "blocked_by_policy"))
')
[[ "$remaining" == 0 ]] || { echo "F9 assert: blocked_by_policy item still present after allow" >&2; exit 1; }
echo "F9 assert: unexplained blocked_by_policy refused; journaled stop parks the project waiting_owner; the inbox allow command releases it to ready"
