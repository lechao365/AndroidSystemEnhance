#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-validate.sh — LcHarness Overlay 状态验证工具
#
# 职责:
#   验证指定 repo 的 overlay 状态是否健康。检测以下状态：
#     - detached:   repo 不在 registry 中
#     - attached:   overlay 目录缺失（已注册但未注入）
#     - broken:     标记文件损坏 / capabilities 目录缺失
#     - stale:      标记文件与 registry 信息不一致（profile/version 变更）
#     - healthy:    切正常
#
# 用法:
#   lc-validate.sh <repo-id>
#
# 输出（stdout，固定两行）:
#   STATUS=<state>
#   REASON=<reason>
#
# 退出码:
#   0  healthy
#   1  非 healthy（detached / attached / broken / stale）
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-validate"

# ============================================================================
# 工具函数
# ============================================================================

# 从 registry get 输出中解析指定 field 的值（key=value 格式）
parse_field() {
    local output="$1"
    local field="$2"
    echo "$output" | while IFS='=' read -r key value; do
        if [ "$key" = "$field" ]; then
            echo "$value"
            return 0
        fi
    done
    return 1
}

# 从 JSON 标记文件中提取字段值
json_get_field() {
    local marker_file="$1"
    local field="$2"
    python3 -c "import json,sys; d=json.load(open('${marker_file}')); print(d.get('${field}',''))" 2>/dev/null
}

# 输出 STATUS+REASON 两行并退出
emit_status() {
    local status="$1"
    local reason="$2"
    echo "STATUS=${status}"
    echo "REASON=${reason}"
}

# ============================================================================
# 核心状态判定
# ============================================================================

determine_state() {
    local repo_id="$1"

    # Step 1: 查询 registry
    local repo_info
    repo_info=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" get "$repo_id" 2>&1)
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        emit_status "detached" "repo id not found in registry"
        harness_exit 1
    fi

    # 解析字段
    local overlay_root profile_name state attached_at
    overlay_root=$(echo "$repo_info" | grep -E '^overlay_root=' | sed 's/^overlay_root=//')
    profile_name=$(echo "$repo_info" | grep -E '^profile=' | sed 's/^profile=//')
    state=$(echo "$repo_info" | grep -E '^state=' | sed 's/^state=//')
    attached_at=$(echo "$repo_info" | grep -E '^attached_at=' | sed 's/^attached_at=//')

    # Step 2: 检查 overlay 目录是否存在
    if [ ! -d "$overlay_root" ]; then
        emit_status "attached" "overlay directory missing: ${overlay_root}"
        harness_exit 1
    fi

    # Step 3: 检查标记文件
    local marker_file="${overlay_root}/.lcharness-overlay"
    if [ ! -f "$marker_file" ]; then
        emit_status "broken" "marker file missing: ${marker_file}"
        harness_exit 1
    fi

    # Step 4: 验证标记文件 JSON 可解析
    if ! python3 -c "import json; json.load(open('${marker_file}'))" 2>/dev/null; then
        emit_status "broken" "marker file unreadable: ${marker_file}"
        harness_exit 1
    fi

    # Step 5: 验证标记文件字段一致性
    local marker_repo_id marker_profile marker_version
    marker_repo_id=$(json_get_field "$marker_file" "repo_id")
    marker_profile=$(json_get_field "$marker_file" "profile")
    marker_version=$(json_get_field "$marker_file" "version")

    if [ "$marker_repo_id" != "$repo_id" ]; then
        emit_status "broken" "marker repo_id mismatch: expected ${repo_id}, got ${marker_repo_id}"
        harness_exit 1
    fi

    if [ "$marker_profile" != "$profile_name" ]; then
        emit_status "stale" "profile changed: expected ${profile_name}, got ${marker_profile}"
        harness_exit 1
    fi

    if [ "$marker_version" != "1" ]; then
        emit_status "stale" "overlay version changed: expected 1, got ${marker_version}"
        harness_exit 1
    fi

    # Step 6: 检查 capabilities/ 目录
    if [ ! -d "${overlay_root}/capabilities" ]; then
        emit_status "broken" "capabilities dir missing: ${overlay_root}/capabilities"
        harness_exit 1
    fi

    # 全部通过
    emit_status "healthy" ""
    harness_exit 0
}

# ============================================================================
# 主入口
# ============================================================================

main() {
    if [ $# -lt 1 ]; then
        log_error "缺少 <repo-id> 参数"
        echo "用法: $(basename "$0") <repo-id>"
        harness_exit 1
    fi

    local repo_id="$1"
    determine_state "$repo_id"
}

main "$@"