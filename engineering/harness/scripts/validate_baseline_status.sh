#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_baseline_status"

REPO_ROOT="$(harness_repo_root)"
HARNESS_DIR="$(harness_path HARNESS_DIR)"
BASELINE="${BASELINE:-$REPO_ROOT/engineering/harness/config/baseline-status.yaml}"

TMPDIR="${TMPDIR:-/tmp/opencode}"
mkdir -p "$TMPDIR"
WARN_COUNT=0
SCAN_COUNT=0

report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

step_begin "校验 baseline-status.yaml"

[ ! -f "$BASELINE" ] && { report_warn "$BASELINE" "文件不存在"; step_end 1; harness_exit 1; }

python3 -c "
import sys, yaml, re

with open('$BASELINE') as f:
    data = yaml.safe_load(f)

baselines = data.get('baselines', []) if isinstance(data, dict) else []
if not baselines:
    print('WARN: baselines 列表为空')
    sys.exit(0)

errs = []
seen_ids = set()
VALID_STATUSES = {'archive', 'candidate', 'promoted'}

for i, bl in enumerate(baselines):
    bid = bl.get('baseline_id', '')
    if not bid:
        errs.append(f'baselines[{i}]: 缺少 baseline_id')
        continue
    if not re.match(r'^BL-\d{8}-\d{2}$', bid):
        errs.append(f'baselines[{i}]: baseline_id 格式非法: {bid}')
    if bid in seen_ids:
        errs.append(f'baselines[{i}]: 重复 baseline_id: {bid}')
    seen_ids.add(bid)

    status = bl.get('status', '')
    if status not in VALID_STATUSES:
        errs.append(f'{bid}: status 非法: {status}（需 archive/candidate/promoted）')

    if status == 'promoted':
        if not bl.get('approved_by'):
            errs.append(f'{bid}: promoted 缺少 approved_by')
        if not bl.get('approved_at'):
            errs.append(f'{bid}: promoted 缺少 approved_at')
        if not bl.get('build_result'):
            errs.append(f'{bid}: promoted 缺少 build_result')
        if not bl.get('package_result'):
            errs.append(f'{bid}: promoted 缺少 package_result')
        if not bl.get('board_verify'):
            errs.append(f'{bid}: promoted 缺少 board_verify')
    elif status == 'archive':
        if not bl.get('source_branch'):
            errs.append(f'{bid}: archive 缺少 source_branch')
        if not bl.get('source_commit'):
            errs.append(f'{bid}: archive 缺少 source_commit')

for e in errs:
    print(e)
" 2>&1 > "$TMPDIR/validate_baseline_output.txt"

while IFS= read -r line; do
    [ -z "$line" ] && continue
    case "$line" in
        WARN:*) log_warn "${line#WARN: }" ;;
        *) report_warn "$BASELINE" "$line" ;;
    esac
done < "$TMPDIR/validate_baseline_output.txt"
rm -f "$TMPDIR/validate_baseline_output.txt"

step_end 0

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_baseline_status 结果" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_baseline_status 结果" "warns=0" "verdict=PASS"
harness_exit 0
