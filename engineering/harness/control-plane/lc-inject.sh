#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-inject.sh — LcHarness Overlay 注入工具
#
# 职责:
#   为已注册的 repo 创建 overlay 目录结构（.lcharness-overlay 标记、
#   .gitignore、capabilities/），并将 registry state 更新为 injected。
#
# 用法:
#   lc-inject.sh <repo-id>
#
# 退出码:
#   0  成功 / 已注入（幂等）
#   1  参数错误 / id 不存在 / 注入失败
#   3  环境错误
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-inject"

# ============================================================================
# 工具函数
# ============================================================================

# 获取当前 ISO 时间戳
now_iso() {
    date '+%Y-%m-%dT%H:%M:%S%z'
}

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

    # Step 1: 从 registry 获取 repo 信息
    step_begin "查询 registry 中 id=${repo_id} 的信息"

    local repo_info
    repo_info=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" get "$repo_id" 2>&1)
    local rc=$?
    if [ "$rc" -ne 0 ]; then
        step_end 1
        log_error "id 不在 registry 中: $repo_id"
        harness_exit 1
    fi

    # 解析 path 和 overlay_root
    local repo_path overlay_root profile_name
    repo_path=$(echo "$repo_info" | grep -E '^path=' | sed 's/^path=//')
    overlay_root=$(echo "$repo_info" | grep -E '^overlay_root=' | sed 's/^overlay_root=//')
    profile_name=$(echo "$repo_info" | grep -E '^profile=' | sed 's/^profile=//')

    if [ -z "$overlay_root" ]; then
        step_end 1
        log_error "registry 中 overlay_root 为空"
        harness_exit 1
    fi

    step_end 0

    # Step 2: 检查 overlay 目录状态
    step_begin "检查 overlay 目录: ${overlay_root}"

    local marker_file="${overlay_root}/.lcharness-overlay"

    if [ -d "$overlay_root" ]; then
        if [ -f "$marker_file" ]; then
            # 检查标记文件是否有效 JSON
            if python3 -c "import json; json.load(open('${marker_file}'))" 2>/dev/null; then
                # 额外检查 registry state 是否为 injected
                local current_state
                current_state=$(echo "$repo_info" | grep -E '^state=' | sed 's/^state=//')
                if [ "$current_state" = "injected" ]; then
                    log_info "overlay 已存在且标记文件有效，跳过注入"
                    step_end 0
                    echo "$overlay_root"
                    harness_exit 0
                else
                    log_info "overlay 存在但 state=${current_state}（期望 injected），继续注入"
                    step_end 0
                fi
            else
                log_warn "overlay 存在但标记文件损坏，重新注入: ${marker_file}"
                step_end 0
                step_begin "重新注入: 清理旧内容"
                # 清理旧内容但保留目录（下面会重新创建）
                rm -f "$marker_file"
                rm -f "${overlay_root}/.gitignore"
                rm -rf "${overlay_root}/capabilities"
            fi
        else
            log_info "overlay 目录存在但无标记文件，继续注入"
            step_end 0
        fi
    else
        log_info "创建 overlay 目录: ${overlay_root}"
        mkdir -p "$overlay_root"
        step_end 0
    fi

    # Step 3: 写入 .lcharness-overlay 标记文件
    step_begin "写入 .lcharness-overlay 标记文件"
    local attached_at
    attached_at=$(now_iso)

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
        log_error "写入标记文件失败: ${marker_file}"
        step_end 1
        harness_exit 1
    fi
    step_end 0

    # Step 4: 写入 .gitignore（防止 overlay 内容被 repo 跟踪）
    step_begin "写入 .gitignore"
    echo "*" > "${overlay_root}/.gitignore"
    if [ ! -f "${overlay_root}/.gitignore" ]; then
        log_error "写入 .gitignore 失败"
        step_end 1
        harness_exit 1
    fi
    step_end 0

    # Step 5: 创建 capabilities/ 目录及占位文件
    step_begin "创建 capabilities/ 目录"
    mkdir -p "${overlay_root}/capabilities"
    touch "${overlay_root}/capabilities/.placeholder"
    if [ ! -f "${overlay_root}/capabilities/.placeholder" ]; then
        log_error "创建 capabilities/.placeholder 失败"
        step_end 1
        harness_exit 1
    fi
    step_end 0

    # Step 6: 更新 registry state 为 injected
    step_begin "更新 registry state 为 injected"
    local update_result
    update_result=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" update "$repo_id" state injected 2>&1)
    local update_rc=$?
    if [ "$update_rc" -ne 0 ]; then
        log_error "更新 registry state 失败: ${update_result}"
        step_end 1
        harness_exit 1
    fi
    step_end 0

    # 输出 overlay 路径
    echo "$overlay_root"
    log_info "注入完成: repo_id=${repo_id}, overlay=${overlay_root}"
    harness_exit 0
}

main "$@"
