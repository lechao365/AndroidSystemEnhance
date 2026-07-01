#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "run_all_validations"

VALIDATORS=(
    "validate_harness_scripts.sh"
    "validate_harness_config.sh"
    "validate_harness_docs.sh"
    "validate_baseline_status.sh"
    "validate_workflow_contracts.sh"
    "validate_manifest.sh"
)

FAIL_COUNT=0
for v in "${VALIDATORS[@]}"; do
    vpath="$SCRIPT_DIR/$v"
    if [ ! -f "$vpath" ]; then
        log_warn "校验器不存在，跳过: $v"
        continue
    fi
    step_begin "运行: $v"
    if bash "$vpath"; then
        step_end 0
    else
        step_end 1
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
done

if [ "$FAIL_COUNT" -gt 0 ]; then
    log_result "全量校验结果" "validators=${#VALIDATORS[@]}" "failed=$FAIL_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "全量校验结果" "validators=${#VALIDATORS[@]}" "failed=0" "verdict=PASS"
harness_exit 0