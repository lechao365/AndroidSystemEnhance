#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-status.sh — LcHarness Repo 状态查询工具
#
# 职责:
#   查询一个或多个 repo 的当前状态。对每个 repo 调用 lc-validate.sh
#   获取状态，汇总展示。
#
# 用法:
#   lc-status.sh              列出所有注册 repo 的状态（表格格式）
#   lc-status.sh <repo-id>    显示单个 repo 的详细信息（verbose 格式）
#
# 退出码:
#   0  成功
#   1  参数错误 / id 不存在
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-status"

# ============================================================================
# 工具函数
# ============================================================================

# 对 repo-id 执行 validate，返回 STATUS 值（无 id 模式用）
# 用法: run_validate <id>
# 输出: STATUS 值（healthy / attached / detached / broken / stale / unknown）
run_validate() {
    local id="$1"
    local validate_output validate_rc

    validate_output=$(bash "$SCRIPT_DIR/lc-validate.sh" "$id" 2>/dev/null)
    validate_rc=$?

    if [ "$validate_rc" -ne 0 ]; then
        # 从输出中提取 STATUS
        local status
        status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
        if [ -n "$status" ]; then
            echo "$status"
        else
            echo "unknown"
        fi
        return 1
    fi

    echo "healthy"
    return 0
}

# 表格模式：列出所有 repo
cmd_list_all() {
    local list_output
    list_output=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" list 2>/dev/null)

    # 检查是否有汇总行（harness_exit 输出的汇总信息走 stderr，但保险起见过滤掉）
    # 注意: grep -E 不支持 \t，用 awk 或 grep -P 过滤 tab 分隔的行
    local repo_lines
    repo_lines=$(echo "$list_output" | grep -P '^[a-f0-9]{12}\t' 2>/dev/null || echo "$list_output" | awk -F'\t' 'NF==4 && $1 ~ /^[a-f0-9]{12}$/{print}')

    if [ -z "$repo_lines" ]; then
        # 空表
        printf "%-15s %-55s %-25s %s\n" "ID" "PATH" "PROFILE" "STATE"
        log_info "无已注册仓库"
        harness_exit 0
    fi

    # 输出表头
    printf "%-15s %-55s %-25s %s\n" "ID" "PATH" "PROFILE" "STATE"

    # 逐行处理（后面的 tab 分隔字段，每行 4 字段）
    while IFS=$'\t' read -r id path profile registry_state; do
        # 跳过空行或非 12 字符 hex 的行
        if [ -z "$id" ] || ! echo "$id" | grep -qE '^[a-f0-9]{12}$'; then
            continue
        fi

        # 运行 validate 获取真实状态
        local actual_state
        actual_state=$(run_validate "$id")

        printf "%-15s %-55s %-25s %s\n" "$id" "$path" "$profile" "$actual_state"
    done <<< "$repo_lines"

    harness_exit 0
}

# 详细模式：显示单个 repo 的详细信息
cmd_show_single() {
    local target_id="$1"

    # 获取 registry 信息
    local repo_info
    repo_info=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" get "$target_id" 2>&1)
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        log_error "未找到 id: ${target_id}"
        harness_exit 1
    fi

    # 解析字段
    local repo_path profile_name overlay_root state attached_at
    repo_path=$(echo "$repo_info" | grep -E '^path=' | sed 's/^path=//')
    profile_name=$(echo "$repo_info" | grep -E '^profile=' | sed 's/^profile=//')
    overlay_root=$(echo "$repo_info" | grep -E '^overlay_root=' | sed 's/^overlay_root=//')
    state=$(echo "$repo_info" | grep -E '^state=' | sed 's/^state=//')
    attached_at=$(echo "$repo_info" | grep -E '^attached_at=' | sed 's/^attached_at=//')

    # 执行 validate 获取状态
    local validate_output validate_rc actual_state actual_reason
    validate_output=$(bash "$SCRIPT_DIR/lc-validate.sh" "$target_id" 2>/dev/null)
    validate_rc=$?

    actual_state=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    actual_reason=$(echo "$validate_output" | grep -E '^REASON=' | sed 's/^REASON=//')

    if [ -z "$actual_state" ]; then
        actual_state="unknown"
        actual_reason="validate script failed"
    fi

    # 获取当前时间作为 last_check
    local last_check
    last_check=$(date '+%Y-%m-%dT%H:%M:%S%z')

    # 输出 verbose 格式
    echo "Repo ID:       ${target_id}"
    echo "Path:          ${repo_path}"
    echo "Profile:       ${profile_name}"
    echo "State:         ${actual_state}"
    echo "Attached:      ${attached_at}"
    echo "Last Check:    ${last_check}"
    echo "Overlay:       ${overlay_root}"

    if [ -n "$actual_reason" ]; then
        echo "Reason:        ${actual_reason}"
    fi

    harness_exit 0
}

# ============================================================================
# 主入口
# ============================================================================

main() {
    if [ $# -eq 0 ]; then
        # 无参数：列出所有
        cmd_list_all
    else
        # 有参数：显示单个详情
        cmd_show_single "$1"
    fi
}

main "$@"