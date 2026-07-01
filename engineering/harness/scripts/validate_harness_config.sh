#!/bin/bash
set -uo pipefail

# ============================================================================
# validate_harness_config.sh — harness 配置层合法性校验
# 职责边界：
#   - 确认 scope-mapping.yaml / doc-sync-mapping.yaml 存在且能被 python3 解析
#   - 校验 rules[]/routes[] 的 priority 为整数、match 非空、scope 命名规范
#   - 校验 routes[].mode 值域、docs 数组项前缀、version 合法性
# 不负责：
#   - README 解释层正确性
#   - bash 是否消费配置（本阶段不强制）
#   - workflow 文档引用完整性
#
# 约束规则来源：原有 JSON Schema 定义已内联为 Python 校验逻辑。
# 详见: docs/plans/2026-06-20-harness-engineering-midterm-governance-and-script-reliability-plan.md Task 8
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_harness_config"

HARNESS_DIR="$(harness_path HARNESS_DIR)"
CONFIG_DIR="$HARNESS_DIR/config"

WARN_COUNT=0
SCAN_COUNT=0

# --- 辅助：记录一条告警 ------------------------------------------------------
report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

# 检测 python3 是否可用
if ! command -v python3 >/dev/null 2>&1; then
    log_error "未找到 python3，无法进行 YAML 解析校验"
    harness_exit 3
fi

# ============================================================================
# Step 1: YAML 映射文件存在性 + 解析 + 结构化校验
# ============================================================================
step_begin "YAML 映射文件校验"

YAML_TARGETS=(
    "scope-mapping.yaml"
    "doc-sync-mapping.yaml"
    "baseline-status.yaml"
)

YAML_FOUND_COUNT=0
for yname in "${YAML_TARGETS[@]}"; do
    ypath="$CONFIG_DIR/$yname"
    if [ ! -f "$ypath" ]; then
        report_warn "$CONFIG_DIR/$yname" "必需的 YAML 映射文件缺失"
        continue
    fi
    YAML_FOUND_COUNT=$((YAML_FOUND_COUNT + 1))
    log_info "校验 YAML: $yname"

    # 尝试解析
    parse_err=$(python3 -c "
import sys, yaml
try:
    with open('$ypath', 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    if data is None:
        print('EMPTY')
    else:
        print('OK')
except Exception as e:
    print('ERR:'+str(e))
" 2>&1) || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
    case "$parse_err" in
        OK)
            log_info "  YAML 解析通过"
            SCAN_COUNT=$((SCAN_COUNT + 1))
            ;;
        EMPTY)
            log_warn "  YAML 解析为空: $yname"
            SCAN_COUNT=$((SCAN_COUNT + 1))
            ;;
        ERR:*)
            report_warn "$ypath" "YAML 解析失败: ${parse_err#ERR:}"
            ;;
    esac

    # 结构化字段校验：顶层 version + rules[]/routes[] 各字段
    field_err=$(python3 -c "
import sys, yaml, re
errs = []
with open('$ypath', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f) or {}

# --- 顶层 version ---
if isinstance(data, dict) and 'version' in data:
    v = data['version']
    if isinstance(v, bool) or not isinstance(v, int):
        errs.append('version 非整数: %r' % v)
    elif v < 1:
        errs.append('version 应 >= 1，当前: %d' % v)

# --- 提取规则数组 ---
items = []
if isinstance(data, list):
    items = data
elif isinstance(data, dict):
    for key in ('rules', 'routes', 'items', 'mappings'):
        v = data.get(key)
        if isinstance(v, list):
            items = v
            break
if not items:
    errs.append('未找到 rules/routes/items/mappings 非空数组')

for i, it in enumerate(items):
    if not isinstance(it, dict):
        errs.append('item[%d] 非对象类型: %r' % (i, it))
        continue

    # priority: 整数（bool 排他）
    if 'priority' in it:
        p = it['priority']
        if isinstance(p, bool) or not isinstance(p, int):
            errs.append('item[%d] priority 非整数: %r' % (i, p))

    # match: 非空字符串
    if 'match' in it:
        m = it['match']
        if m is None or (isinstance(m, str) and m.strip() == ''):
            errs.append('item[%d] match 为空' % i)

    # scope: 仅 scope-mapping.yaml 有，命名规范
    if 'scope' in it:
        s = it['scope']
        if not isinstance(s, str) or not re.match(r'^[a-z][a-z0-9-]*$', s):
            errs.append('item[%d] scope 格式非法（需小写字母/数字/连字符，字母开头）: %r' % (i, s))

    # mode: 仅 doc-sync-mapping.yaml 有，值域约束
    if 'mode' in it:
        mo = it['mode']
        if mo not in ('fixed', 'ai-diff', 'ai-pending'):
            errs.append('item[%d] mode 非法（需 fixed/ai-diff/ai-pending）: %r' % (i, mo))

    # docs: 仅 doc-sync-mapping.yaml 有，以 docs/ 开头
    if 'docs' in it:
        docs = it['docs']
        if isinstance(docs, list):
            for d in docs:
                if not (isinstance(d, str) and d.startswith('docs/')):
                    errs.append('item[%d] docs 项不以 docs/ 开头: %r' % (i, d))

for e in errs:
    print(e)
" 2>&1) || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
    if [ -n "$field_err" ]; then
        while IFS= read -r l; do
            [ -z "$l" ] && continue
            report_warn "$ypath" "$l"
        done <<< "$field_err"
    fi
    # 对 baseline-status.yaml 做额外字段校验
    if [ "$yname" = "baseline-status.yaml" ]; then
        bs_err=$(python3 -c "
import sys, yaml
with open('$ypath') as f:
    data = yaml.safe_load(f)
errs = []
baselines = data.get('baselines', []) if isinstance(data, dict) else []
VALID_STATUSES = {'archive', 'candidate', 'promoted'}
for i, bl in enumerate(baselines):
    s = bl.get('status', '')
    if s not in VALID_STATUSES:
        errs.append(f'baselines[{i}] status 非法: {s}')
    if s == 'promoted' and not bl.get('approved_by'):
        errs.append(f'baselines[{i}] promoted 缺少 approved_by')
for e in errs:
    print(e)
" 2>&1) || true
        if [ -n "$bs_err" ]; then
            while IFS= read -r l; do
                [ -z "$l" ] && continue
                report_warn "$ypath" "$l"
            done <<< "$bs_err"
        fi
    fi
done

log_info "YAML 映射文件检查完成：发现 $YAML_FOUND_COUNT 个，校验 $SCAN_COUNT 个"
step_end 0

# ============================================================================
# Step 2: 汇总与退出
# ============================================================================
if [ "$YAML_FOUND_COUNT" -eq 0 ]; then
    report_warn "$CONFIG_DIR" "未发现任何 YAML 配置文件，config 机器层缺失"
fi

if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_harness_config 结果" "scanned=$SCAN_COUNT" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_harness_config 结果" "scanned=$SCAN_COUNT" "warns=0" "verdict=PASS"
harness_exit 0
