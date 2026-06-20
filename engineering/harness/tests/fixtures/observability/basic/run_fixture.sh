#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../lib/harness_bootstrap.sh"

SCRIPT_NAME="${1:-test-harness-observability-basic}"

harness_init "$SCRIPT_NAME"

step_begin "emit observability records"

tmp_file="$(harness_tmp_file "basic.txt")"
printf 'fixture-basic\n' > "$tmp_file"

tmp_dir="$(harness_tmp_dir "basic-dir")"
printf 'nested-artifact\n' > "$tmp_dir/nested.txt"

artifact_register "$tmp_file" "basic-file"
artifact_register "$tmp_dir" "basic-dir"

log_result "APPLY 结果" "applied=5" "plan=$tmp_file"
harness_status_emit "OK" "kernel/new/foo.c"
harness_status_emit "MISS" "kernel/missing/bar.c" "expected missing"

step_end 0
harness_exit 0
