#!/bin/bash
set -uo pipefail

# ============================================================================
# validate_harness_scripts.sh — harness bash 脚本静态合规校验
# 职责边界（5 项）：
#   - workflows/*/*.sh 与 scripts/*.sh 是否 source harness_bootstrap.sh
#   - 是否调用 harness_init
#   - 是否出现裸 exit（非 harness_exit）
#   - 是否出现裸 /tmp/（非 harness_tmp_*）
#   - 是否直接依赖 _H_* / _h_* 私有符号
# 不负责：
#   - README/YAML/运行时正确性、裸 echo 风格（由其他规则/校验覆盖）
# 扫描器对跨行单引号字符串做状态跟踪，避免把字符串数据内的 exit/tmp/符号误判为代码。
# 详见: docs/plans/2026-06-20-harness-engineering-midterm-governance-and-script-reliability-plan.md Task 8
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "validate_harness_scripts"

HARNESS_DIR="$(harness_path HARNESS_DIR)"

WARN_COUNT=0
SCAN_COUNT=0

# 公共库本身（实现 log/step 等，不参与业务层校验）
LIB_BASENAMES=(
    "harness_bootstrap.sh"
    "harness_observability.sh"
)

# --- 辅助：记录一条告警 ------------------------------------------------------
report_warn() {
    local where="$1" msg="$2"
    log_warn "$where | $msg"
    harness_status_emit "MISS" "$where" "$msg"
    WARN_COUNT=$((WARN_COUNT + 1))
}

is_in_list() {
    local target="$1"; shift
    local e
    for e in "$@"; do
        [ "$e" = "$target" ] && return 0
    done
    return 1
}

is_lib_self() {
    is_in_list "$1" "${LIB_BASENAMES[@]}"
}

# --- 代码净化：剔除注释、字符串字面量、heredoc、trap 子句 ---------------------
# 目的：避免把字符串内的 "exit" / "/tmp/" / "_h_xxx" 误判为代码层调用。
# 净化策略（按顺序）：
#   1. 剔除行尾注释（# 前有空格且不在引号内——简化：行首 # 算注释，行中 # 视上下文）
#   2. 把单引号字符串内容替换为空（'xxx' -> ''）
#   3. 把双引号字符串内容替换为空（"xxx" -> ""）
#   4. 把反引号命令替换内容替换为空（`xxx` -> ``）
#   5. 把 --exit-code 替换为占位符（exit-code 中的 exit 不算裸 exit）
# 净化后剩余即为"代码骨架"，供后续 grep 使用。
strip_literals() {
    local line="$1"
    # 1) 去除整行注释（行首 # 开头，允许前导空白）
    local trimmed
    trimmed=$(printf '%s' "$line" | sed -E 's/^[[:space:]]+//')
    case "$trimmed" in
        \#*) printf ''; return;;
    esac
    # 2) 单引号字符串 -> ''
    line=$(printf '%s' "$line" | sed -E "s/'[^']*'/''/g")
    # 3) 双引号字符串 -> ""（注意：双引号内可能含转义 \"，简化处理）
    line=$(printf '%s' "$line" | sed -E 's/"[^"]*"/""/g')
    # 4) 反引号 -> ``
    line=$(printf '%s' "$line" | sed -E 's/`[^`]*`/``/g')
    # 5) --exit-code 形式：替换为占位符避免被 \bexit\b 命中
    line=$(printf '%s' "$line" | sed -E 's/--exit-code/--XC/g')
    # 6) trap 'xxx' 形式：trap 中的 exit 是信号处理，合规
    #   已经在 2) 单引号净化中处理（'xxx' -> ''）
    printf '%s' "$line"
}

# ============================================================================
# Step 1: 收集待校验脚本
# ============================================================================
step_begin "收集待校验 bash 脚本"

TARGETS=()
while IFS= read -r f; do
    TARGETS+=("$f")
done < <(find "$HARNESS_DIR" -name '*.sh' -type f \
    -not -path '*/log/*' \
    -not -path '*/tests/*' \
    -not -path '*/lib/*' \
    2>/dev/null | sort || true)

log_info "待校验脚本 ${#TARGETS[@]} 个"
for t in "${TARGETS[@]}"; do
    log_info "  - ${t#$HARNESS_DIR/}"
done
step_end 0

# ============================================================================
# Step 2: 逐脚本静态扫描
# ============================================================================
step_begin "逐脚本静态扫描"

for f in "${TARGETS[@]}"; do
    rel="${f#$HARNESS_DIR/}"
    bn=$(basename "$f")
    log_info "扫描: $rel"
    SCAN_COUNT=$((SCAN_COUNT + 1))

    # ---- 2.1 bootstrap source 校验（业务脚本必须 source harness_bootstrap.sh）---
    if ! is_lib_self "$bn"; then
        if ! grep -qE 'source.*harness_bootstrap\.sh' "$f"; then
            report_warn "$f:1" "未 source harness_bootstrap.sh（违反 RID-OBS-001 MUST1）"
        fi
    fi

    # ---- 2.2 harness_init 调用校验（公共库豁免）---
    if ! is_lib_self "$bn"; then
        if ! grep -qE 'harness_init\b' "$f"; then
            report_warn "$f:1" "未调用 harness_init（违反 RID-OBS-001 MUST2）"
        fi
    fi

    # ---- 2.3 ~ 2.5 逐行扫描（公共库本身豁免 tmp/exit/私有符号校验）---
    # 跨行单引号字符串跟踪：bash 单引号内为纯字面量，跨行字符串（如 bash -c '...'）内的
    # exit / /tmp/ / _h_* 属字符串数据，不应误判为代码层调用。
    # 算法：先抹掉双引号片段（双引号内的 ' 不计），按单引号奇偶切换 in_squote 状态；
    #       若某行"起始即处于单引号块内"，则该行整体为字符串数据，跳过代码层校验。
    lineno=0
    in_squote=0
    while IFS= read -r line; do
        lineno=$((lineno + 1))

        line_starts_in_squote=$in_squote
        # 抹掉双引号片段后统计单引号数量，奇数则翻转跨行状态
        qline=$(printf '%s' "$line" | sed -E 's/"[^"]*"//g')
        sq=$(printf '%s' "$qline" | tr -cd "'" | wc -c)
        if [ $((sq % 2)) -eq 1 ]; then
            in_squote=$((1 - in_squote))
        fi
        # 起始即在单引号块内 → 整行为字符串数据，跳过代码层校验
        if [ "$line_starts_in_squote" -eq 1 ]; then
            continue
        fi

        skeleton=$(strip_literals "$line")

        # ---- 2.3 裸 exit 校验（公共库豁免）---
        if ! is_lib_self "$bn"; then
            # skeleton 中去掉 harness_exit 后检查是否还有裸 exit
            sk_exit=$(printf '%s' "$skeleton" | sed -E 's/harness_exit//g; s/sys\.exit//g; s/--exit-code//g')
            if printf '%s' "$sk_exit" | LC_ALL=C grep -qE '\bexit\b'; then
                report_warn "$f:$lineno" "出现裸 exit（应使用 harness_exit，违反 RID-OBS-001 MUST8）"
            fi
        fi

        # ---- 2.4 裸 /tmp/ 校验（公共库豁免）---
        if ! is_lib_self "$bn"; then
            # skeleton 中已剔除字符串；检查代码层是否含 /tmp/
            # 允许 harness_tmp_file / harness_tmp_dir
            sk_tmp=$(printf '%s' "$skeleton" | sed -E 's/harness_tmp_file//g; s/harness_tmp_dir//g')
            if printf '%s' "$sk_tmp" | grep -qE '/tmp/'; then
                report_warn "$f:$lineno" "出现裸 /tmp/（应使用 harness_tmp_file/dir，违反 RID-OBS-001 MUST7）"
            fi
        fi

        # ---- 2.5 私有 API 依赖校验（禁止 _H_* / _h_*，公共库豁免）---
        if ! is_lib_self "$bn"; then
            if printf '%s' "$skeleton" | LC_ALL=C grep -qE '\b_h_[a-z]+' 2>/dev/null; then
                report_warn "$f:$lineno" "直接依赖私有符号 _h_*（违反 RID-OBS-001 MUST9）"
            fi
            if printf '%s' "$skeleton" | LC_ALL=C grep -qE '\b_H_[A-Z]+' 2>/dev/null; then
                report_warn "$f:$lineno" "直接依赖私有符号 _H_*（违反 RID-OBS-001 MUST9）"
            fi
        fi
    done < "$f"
done

step_end 0

# ============================================================================
# 汇总
# ============================================================================
if [ "$WARN_COUNT" -gt 0 ]; then
    log_result "validate_harness_scripts 结果" "scanned=$SCAN_COUNT" "warns=$WARN_COUNT" "verdict=FAIL"
    harness_exit 1
fi

log_result "validate_harness_scripts 结果" "scanned=$SCAN_COUNT" "warns=0" "verdict=PASS"
harness_exit 0
