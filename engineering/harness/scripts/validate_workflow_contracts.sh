#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_workflow_contracts"

HARNESS_DIR="$(harness_path HARNESS_DIR)"
WARN_COUNT=0
SCAN_COUNT=0

report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

step_begin "校验 WORKFLOW.md 契约完整性"

WORKFLOW_FILES=()
while IFS= read -r f; do
    WORKFLOW_FILES+=("$f")
done < <(find "$HARNESS_DIR/workflows" -name 'WORKFLOW.md' -type f 2>/dev/null || true)

if [ "${#WORKFLOW_FILES[@]}" -eq 0 ]; then
    log_warn "未发现 WORKFLOW.md"
    step_end 0
    harness_exit 0
fi

for wf in "${WORKFLOW_FILES[@]}"; do
    wf_name="${wf#$HARNESS_DIR/}"
    log_info "校验: $wf_name"
    SCAN_COUNT=$((SCAN_COUNT + 1))

    first_line=$(head -n 1 "$wf")
    [ "$first_line" = "---" ] || { report_warn "$wf:1" "缺少 YAML front matter 起始 ---"; continue; }

    end_line=$(grep -nE '^---\s*$' "$wf" | sed -n '2p' | cut -d: -f1)
    [ -n "$end_line" ] || { report_warn "$wf:1" "YAML front matter 未闭合"; continue; }

    fm_content=$(head -n "$end_line" "$wf")
    echo "$fm_content" | grep -qE '^name:' || report_warn "$wf:1" "缺少 name"
    echo "$fm_content" | grep -qE '^description:' || report_warn "$wf:1" "缺少 description"
    echo "$fm_content" | grep -qE '^stages:' || report_warn "$wf:1" "缺少 stages 声明"

    grep -qE '## TODO 跟踪' "$wf" || report_warn "$wf" "缺少 ## TODO 跟踪 章节"
    grep -qE '## 退出码' "$wf" || report_warn "$wf" "缺少 ## 退出码 章节"
done

step_end 0

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_workflow_contracts 结果" "scanned=$SCAN_COUNT" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_workflow_contracts 结果" "scanned=$SCAN_COUNT" "warns=0" "verdict=PASS"
harness_exit 0