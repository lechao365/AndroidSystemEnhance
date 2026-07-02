#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-reconcile.sh — LcHarness Overlay 修复工具
#
# 职责:
#   尝试自动修复非健康状态的 repo overlay。根据 lc-validate.sh 判定的
#   具体故障原因，执行针对性修复（重建标记文件、目录、或完整 overlay）。
#
# 用法:
#   lc-reconcile.sh <repo-id>
#
# 退出码:
#   0  修复成功（最终 healthy）
#   1  修复失败（仍 broken）/ 参数错误 / id 不存在
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-reconcile"

# ============================================================================
# 工具函数
# ============================================================================

# 从 registry get 输出中解析指定 field 的值（key=value 格式）
# 用法: parse_field <output> <field_name>
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

# 采集 registry 中的真实值并重建标记文件
rebuild_marker() {
    local repo_id="$1"
    local repo_info="$2"
    local overlay_root="$3"

    local profile_name
    profile_name=$(echo "$repo_info" | grep -E '^profile=' | sed 's/^profile=//')

    local marker_file="${overlay_root}/.lcharness-overlay"
    local attached_at
    attached_at=$(date '+%Y-%m-%dT%H:%M:%S%z')

    cat > "$marker_file" <<EOF
{
  "version": "1",
  "repo_id": "${repo_id}",
  "profile": "${profile_name}",
  "attached_at": "${attached_at}",
  "lcharness_version": "1.0"
}
EOF

    if [ ! -f "$marker_file" ]; then
        log_error "重建标记文件失败: ${marker_file}"
        return 1
    fi
    log_info "标记文件已重建: ${marker_file}"
    return 0
}

# 重建 capabilities/ 目录
rebuild_capabilities() {
    local overlay_root="$1"
    mkdir -p "${overlay_root}/capabilities"
    touch "${overlay_root}/capabilities/.placeholder"
    if [ ! -f "${overlay_root}/capabilities/.placeholder" ]; then
        log_error "创建 capabilities/.placeholder 失败"
        return 1
    fi
    log_info "capabilities/ 目录已重建"
    return 0
}

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

    local overlay_root
    overlay_root=$(echo "$repo_info" | grep -E '^overlay_root=' | sed 's/^overlay_root=//')
    step_end 0

    # Step 2: 调用 lc-validate.sh 获取当前状态
    step_begin "获取当前状态 (id=${repo_id})"

    local validate_output
    validate_output=$(bash "$SCRIPT_DIR/lc-validate.sh" "$repo_id" 2>&1)
    local validate_rc=$?

    local status=""
    local reason=""
    status=$(echo "$validate_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    reason=$(echo "$validate_output" | grep -E '^REASON=' | sed 's/^REASON=//')

    step_end 0

    # Step 3: 根据状态执行修复
    if [ "$status" = "healthy" ]; then
        log_info "already healthy, nothing to reconcile"
        harness_exit 0
    fi

    if [ "$status" = "attached" ]; then
        log_info "检测到 attached 状态，重建 overlay (reason: ${reason})"
        step_begin "调用 lc-inject.sh 重建整个 overlay"
        local inject_output
        inject_output=$(bash "$SCRIPT_DIR/lc-inject.sh" "$repo_id" 2>&1)
        local inject_rc=$?
        if [ "$inject_rc" -ne 0 ]; then
            log_error "调用 lc-inject.sh 重建 overlay 失败"
            step_end 1
            harness_exit 1
        fi
        log_info "overlay 已通过 lc-inject.sh 重建"
        step_end 0

    elif [ "$status" = "stale" ]; then
        log_info "检测到 stale 状态，重建 overlay (reason: ${reason})"
        step_begin "重建 overlay"

        # 从 registry 读取真实值写入标记文件
        if ! rebuild_marker "$repo_id" "$repo_info" "$overlay_root"; then
            step_end 1
            harness_exit 1
        fi

        # 确保 capabilities/ 目录存在
        if [ ! -d "${overlay_root}/capabilities" ]; then
            if ! rebuild_capabilities "$overlay_root"; then
                step_end 1
                harness_exit 1
            fi
        fi

        step_end 0
    elif [ "$status" = "broken" ]; then
        log_info "检测到 broken 状态 (reason: ${reason})"

        case "$reason" in
            "marker file missing"*)
                step_begin "重建标记文件"
                if ! rebuild_marker "$repo_id" "$repo_info" "$overlay_root"; then
                    step_end 1
                    harness_exit 1
                fi
                step_end 0
                ;;
            "capabilities dir missing"*)
                step_begin "重建 capabilities 目录"
                if ! rebuild_capabilities "$overlay_root"; then
                    step_end 1
                    harness_exit 1
                fi
                step_end 0
                ;;
            "overlay directory missing"*)
                step_begin "调用 lc-inject.sh 重建整个 overlay"
                local inject_output
                inject_output=$(bash "$SCRIPT_DIR/lc-inject.sh" "$repo_id" 2>&1)
                local inject_rc=$?
                if [ "$inject_rc" -ne 0 ]; then
                    log_error "调用 lc-inject.sh 重建 overlay 失败"
                    step_end 1
                    harness_exit 1
                fi
                log_info "overlay 已通过 lc-inject.sh 重建"
                step_end 0
                ;;
            "marker repo_id mismatch"*)
                step_begin "重建标记文件（覆盖写入）"
                if ! rebuild_marker "$repo_id" "$repo_info" "$overlay_root"; then
                    step_end 1
                    harness_exit 1
                fi
                step_end 0
                ;;
            *)
                log_error "cannot auto-repair: ${reason}"
                harness_exit 1
                ;;
        esac
    else
        log_error "cannot auto-repair: unexpected status '${status}' (reason: ${reason})"
        harness_exit 1
    fi

    # Step 4: 更新 last_reconcile
    step_begin "更新 last_reconcile"
    local update_output
    update_output=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" update "$repo_id" last_reconcile "$(date -Iseconds)" 2>&1)
    local update_rc=$?
    if [ "$update_rc" -ne 0 ]; then
        log_warn "更新 last_reconcile 失败: ${update_output}"
        step_end 1
    else
        step_end 0
    fi

    # Step 5: 再次验证
    step_begin "验证修复结果"

    local final_output
    final_output=$(bash "$SCRIPT_DIR/lc-validate.sh" "$repo_id" 2>&1)
    local final_rc=$?
    local final_status
    final_status=$(echo "$final_output" | grep -E '^STATUS=' | sed 's/^STATUS=//')
    local final_reason
    final_reason=$(echo "$final_output" | grep -E '^REASON=' | sed 's/^REASON=//')

    if [ "$final_rc" -eq 0 ] && [ "$final_status" = "healthy" ]; then
        step_end 0
        log_info "reconcile 成功: id=${repo_id}, status=${final_status}"
        echo "STATUS=${final_status}"
        harness_exit 0
    else
        step_end 1
        log_error "reconcile 失败: id=${repo_id}, status=${final_status}, reason=${final_reason}"
        echo "STATUS=${final_status}"
        echo "REASON=${final_reason}"
        harness_exit 1
    fi
}

main "$@"