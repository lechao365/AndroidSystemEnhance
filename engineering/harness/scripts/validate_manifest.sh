#!/bin/bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_manifest"

HARNESS_DIR="$(harness_path HARNESS_DIR)"
MANIFEST="$HARNESS_DIR/rules/manifest.yaml"
WARN_COUNT=0

report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

step_begin "校验 manifest.yaml"

[ ! -f "$MANIFEST" ] && { report_warn "$MANIFEST" "文件不存在"; step_end 1; harness_exit 1; }

py_out=$(python3 -c "
import sys, yaml

with open('$MANIFEST') as f:
    data = yaml.safe_load(f)

errs = []
VALID_ACCESS = {'direct_edit', 'require_workflow', 'require_plan', 'require_confirmation', 'require_evidence'}
VALID_CATEGORIES = {'source', 'archive', 'revert', 'doc-sync', 'git', 'harness', 'test', 'docs'}

contexts = data.get('contexts', [])
if not contexts:
    errs.append('contexts 列表为空')

seen_ids = set()
for i, ctx in enumerate(contexts):
    cid = ctx.get('id', '')
    if not cid:
        errs.append(f'contexts[{i}]: 缺少 id')
        continue
    if cid in seen_ids:
        errs.append(f'contexts[{i}]: 重复 id: {cid}')
    seen_ids.add(cid)

    if not ctx.get('match'):
        errs.append(f'{cid}: 缺少 match')
    access = ctx.get('access', '')
    if access not in VALID_ACCESS:
        errs.append(f'{cid}: access 非法: {access}')
    cat = ctx.get('scope_category', '')
    if cat not in VALID_CATEGORIES:
        errs.append(f'{cid}: scope_category 非法: {cat}')

access_levels = data.get('access_levels', [])
if not access_levels:
    errs.append('access_levels 列表为空')

for e in errs:
    print(e)
" 2>&1) || true
if [ -n "$py_out" ]; then
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        report_warn "$MANIFEST" "$line"
    done <<< "$py_out"
fi

step_end 0

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_manifest 结果" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_manifest 结果" "warns=0" "verdict=PASS"
harness_exit 0