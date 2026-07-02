#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-detach.sh — LcHarness Repo 分离工具
#
# 职责:
#   将指定 repo 从 LcHarness 分离：移除 overlay 目录并从 registry 中
#   删除条目。操作前需要用户交互确认。
#
# 用法:
#   lc-detach.sh <repo-id>
#
# 退出码:
#   0  成功分离
#   1  参数错误 / id 不存在 / 分离失败
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-detach"

# ============================================================================
# 主逻辑
# ============================================================================

main() {
    if [ $# -lt 1 ]; then
        log_error "缺少 <repo-id> 参数"
        echo "用法: $(basename "$0") <repo-id>"
        harness_exit 1
    fi

    local repo_id="$1"

    # Step 1: 查询 registry
    step_begin "查询 registry 中 id=${repo_id} 的信息"

    local repo_info
    repo_info=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" get "$repo_id" 2>&1)
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        step_end 1
        log_error "not registered: ${repo_id}"
        harness_exit 1
    fi

    local repo_path overlay_root
    repo_path=$(echo "$repo_info" | grep -E '^path=' | sed 's/^path=//')
    overlay_root=$(echo "$repo_info" | grep -E '^overlay_root=' | sed 's/^overlay_root=//')

    step_end 0

    # Step 2: 用户确认
    echo ""
    echo "WARNING: Detaching ${repo_id} (${repo_path}) will remove overlay at ${overlay_root}."
    echo -n "Continue? [y/N] "
    read -r confirm

    if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
        log_info "cancelled"
        harness_exit 0
    fi

    # Step 3: 移除 overlay 目录
    step_begin "移除 overlay 目录: ${overlay_root}"

    if [ -d "$overlay_root" ]; then
        rm -rf "$overlay_root"
    else
        log_info "overlay 目录不存在，跳过删除"
    fi

    # 验证移除
    if [ -d "$overlay_root" ]; then
        log_error "removal incomplete: ${overlay_root} still exists"
        step_end 1
        harness_exit 1
    fi

    step_end 0

    # Step 4: 从 registry 中移除条目
    step_begin "从 registry 中移除条目"

    local rm_output
    rm_output=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" remove "$repo_id" 2>&1)
    local rm_rc=$?
    if [ "$rm_rc" -ne 0 ]; then
        log_error "从 registry 移除条目失败: ${rm_output}"
        step_end 1
        harness_exit 1
    fi

    step_end 0

    # Step 5: 输出结果
    echo ""
    log_info "==========================================="
    log_info "  Detach 完成"
    log_info "  ID:       ${repo_id}"
    log_info "  Path:     ${repo_path}"
    log_info "  Overlay:  ${overlay_root}"
    log_info "==========================================="
    echo "Detached and cleaned: ${repo_id}"

    harness_exit 0
}

main "$@"