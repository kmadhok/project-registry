#!/usr/bin/env bash
set -uo pipefail

faults_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
FAULT_TMP=${FAULT_TMP:-${TMPDIR:-/tmp}/project-registry-faults}
export FAULT_TMP
mkdir -p "$FAULT_TMP"

printf '%-4s | %-6s\n' 'F#' 'RESULT'
printf '%-4s-+-%-6s\n' '----' '------'
failures=0
for script in "$faults_dir"/f[0-9][0-9]-*.sh; do
  id=$(basename "$script" | sed -E 's/^f([0-9][0-9]).*/F\1/; s/F0/F/')
  if grep -q '^FAULT_STUB=1$' "$script"; then
    printf '%-4s | %-6s\n' "$id" 'SKIP'
    continue
  fi
  log="$FAULT_TMP/${id}.log"
  if bash "$script" >"$log" 2>&1; then
    printf '%-4s | %-6s\n' "$id" 'PASS'
  else
    printf '%-4s | %-6s\n' "$id" 'FAIL'
    sed 's/^/       /' "$log"
    failures=$((failures + 1))
  fi
done

exit "$failures"
