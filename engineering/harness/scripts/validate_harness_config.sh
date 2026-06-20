#!/bin/bash
set -uo pipefail

# ============================================================================
# validate_harness_config.sh — harness 配置层合法性校验
# 职责边界：
#   - 确认 scope-mapping.yaml / doc-sync-mapping.yaml 存在且能被 python3 解析
#   - 校验 schema/*.json 能被 python3 json.load() 加载
#   - 校验 rules[]/routes[] 的 priority 为整数、match 非空
#   - 校验 routes[].docs 数组项以 docs/ 开头
# 不负责：
#   - README 解释层正确性
#   - bash 是否消费配置（本阶段不强制）
#   - workflow 文档引用完整性
# 详见: docs/plans/2026-06-20-harness-engineering-midterm-governance-and-script-reliability-plan.md Task 8
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/harness_bootstrap.sh"

harness_init "validate_harness_config"

HARNESS_DIR="$REPO_ROOT/engineering/harness"
CONFIG_DIR="$HARNESS_DIR/config"

WARN_COUNT=0
SCAN_COUNT=0

# --- 辅助：记录一条告警 ------------------------------------------------------
_report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

# 检测 python3 是否可用
if ! command -v python3 >/dev/null 2>&1; then
    log_error "未找到 python3，无法进行 YAML/JSON 解析校验"
    harness_exit 3
fi

# ============================================================================
# Step 1: YAML 映射文件存在性 + 解析校验
# ============================================================================
step_begin "YAML 映射文件校验"

YAML_TARGETS=(
    "scope-mapping.yaml"
    "doc-sync-mapping.yaml"
)

YAML_FOUND_COUNT=0
for yname in "${YAML_TARGETS[@]}"; do
    ypath="$CONFIG_DIR/$yname"
    if [ ! -f "$ypath" ]; then
        _report_warn "$CONFIG_DIR/$yname" "必需的 YAML 映射文件缺失"
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
" 2>&1)
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
            _report_warn "$ypath" "YAML 解析失败: ${parse_err#ERR:}"
            ;;
    esac

    # 结构化字段校验：priority 整数 / match 非空 / docs[] 以 docs/ 开头
    field_err=$(python3 -c "
import sys, yaml
errs = []
with open('$ypath', 'r', encoding='utf-8') as f:
    data = yaml.safe_load(f) or {}
# 提取规则数组：兼容 rules / routes / items / mappings 或顶层 list
items = []
if isinstance(data, list):
    items = data
elif isinstance(data, dict):
    for key in ('rules', 'routes', 'items', 'mappings'):
        v = data.get(key)
        if isinstance(v, list):
            items = v
            break
for i, it in enumerate(items):
    if not isinstance(it, dict):
        continue
    if 'priority' in it:
        p = it['priority']
        # bool 是 int 子类，需单独排除
        if isinstance(p, bool) or not isinstance(p, int):
            errs.append('item[%d] priority 非整数: %r' % (i, p))
    if 'match' in it:
        m = it['match']
        if m is None or (isinstance(m, str) and m.strip() == ''):
            errs.append('item[%d] match 为空' % i)
    if 'docs' in it:
        docs = it['docs']
        if isinstance(docs, list):
            for d in docs:
                if not (isinstance(d, str) and d.startswith('docs/')):
                    errs.append('item[%d] docs 项不以 docs/ 开头: %r' % (i, d))
for e in errs:
    print(e)
" 2>&1)
    if [ -n "$field_err" ]; then
        while IFS= read -r l; do
            [ -z "$l" ] && continue
            _report_warn "$ypath" "$l"
        done <<< "$field_err"
    fi
done

log_info "YAML 映射文件检查完成：发现 $YAML_FOUND_COUNT 个，校验 $SCAN_COUNT 个"
step_end 0

# ============================================================================
# Step 2: JSON schema 文件校验
# ============================================================================
step_begin "JSON schema 文件校验"

SCHEMA_DIR="$CONFIG_DIR/schema"
SCHEMA_FOUND_COUNT=0
if [ -d "$SCHEMA_DIR" ]; then
    SCHEMA_FILES=()
    while IFS= read -r s; do
        SCHEMA_FILES+=("$s")
    done < <(find "$SCHEMA_DIR" -name '*.json' -type f 2>/dev/null)

    for sf in "${SCHEMA_FILES[@]}"; do
        rel="${sf#$HARNESS_DIR/}"
        SCHEMA_FOUND_COUNT=$((SCHEMA_FOUND_COUNT + 1))
        log_info "校验 schema: $rel"
        jerr=$(python3 -c "
import json
try:
    with open('$sf', 'r', encoding='utf-8') as f:
        json.load(f)
    print('OK')
except Exception as e:
    print('ERR:'+str(e))
" 2>&1)
        case "$jerr" in
            OK)
                log_info "  JSON 解析通过"
                SCAN_COUNT=$((SCAN_COUNT + 1))
                ;;
            ERR:*)
                _report_warn "$sf" "JSON 解析失败: ${jerr#ERR:}"
                ;;
        esac
    done
fi

log_info "JSON schema 检查完成：发现 $SCHEMA_FOUND_COUNT 个"
step_end 0

# ============================================================================
# Step 3: 渐进引入说明（无 YAML/JSON 时不视为失败）
# ============================================================================
step_begin "渐进引入状态汇总"

if [ "$YAML_FOUND_COUNT" -eq 0 ] && [ "$SCHEMA_FOUND_COUNT" -eq 0 ]; then
    _report_warn "$CONFIG_DIR" "未发现任何 YAML/JSON 配置文件，config 机器层缺失"
fi

step_end 0

# ============================================================================
# 汇总
# ============================================================================
if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_harness_config 结果" "scanned=$SCAN_COUNT" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_harness_config 结果" "scanned=$SCAN_COUNT" "warns=0" "verdict=PASS"
harness_exit 0
