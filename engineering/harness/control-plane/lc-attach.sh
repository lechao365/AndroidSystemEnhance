#!/bin/bash
set -uo pipefail

# ============================================================================
# lc-attach.sh — LcHarness Repo 附加工具
#
# 职责:
#   将外部 repo 注册到 LcHarness 并注入 overlay。是用户接入 harness
#   入口脚本。流程：
#     1. 验证 repo 路径可读
#     2. 调用 lc-repo-registry.sh add 注册（获取 id）
#     3. 调用 lc-inject.sh 创建 overlay
#     4. 健康检查（内联，待 lc-validate.sh 就绪后替换）
#     5. 输出 Attach summary
#
# 用法:
#   lc-attach.sh <repo-path> --profile <name>
#
# 退出码:
#   0  成功（已附加且健康）
#   1  参数错误 / 路径无效 / 已注册 / 注入失败
#   3  环境错误
# ============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"

harness_init "lc-attach"

# ============================================================================
# 工具函数
# ============================================================================

# 内联健康检查：验证 overlay 目录存在 + 标记文件有效 + state 为 injected
# 返回 0 健康，1 不健康
inline_health_check() {
    local repo_id="$1"

    # 获取 overlay_root
    local repo_info overlay_root marker_file
    repo_info=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" get "$repo_id" 2>&1) || return 1
    overlay_root=$(echo "$repo_info" | grep -E '^overlay_root=' | sed 's/^overlay_root=//')
    marker_file="${overlay_root}/.lcharness-overlay"

    # 检查 overlay 目录
    if [ ! -d "$overlay_root" ]; then
        log_error "健康检查失败: overlay 目录不存在: ${overlay_root}"
        return 1
    fi

    # 检查标记文件
    if [ ! -f "$marker_file" ]; then
        log_error "健康检查失败: 标记文件不存在: ${marker_file}"
        return 1
    fi

    # 检查标记文件是否为有效 JSON
    if ! python3 -c "import json; json.load(open('${marker_file}'))" 2>/dev/null; then
        log_error "健康检查失败: 标记文件格式无效: ${marker_file}"
        return 1
    fi

    # 检查 registry state 是否为 injected
    local state
    state=$(echo "$repo_info" | grep -E '^state=' | sed 's/^state=//')
    if [ "$state" != "injected" ]; then
        log_error "健康检查失败: state 为 ${state}，期望 injected"
        return 1
    fi

    return 0
}

# ============================================================================
# 主逻辑
# ============================================================================

main() {
    local repo_path="" profile_name=""

    # 解析参数
    while [ $# -gt 0 ]; do
        case "$1" in
            --profile)
                shift
                profile_name="$1"
                ;;
            --*)
                log_error "未知选项: $1"
                echo "用法: $(basename "$0") <repo-path> --profile <name>"
                harness_exit 1
                ;;
            *)
                if [ -z "$repo_path" ]; then
                    repo_path="$1"
                else
                    log_error "多余的参数: $1"
                    harness_exit 1
                fi
                ;;
        esac
        shift
    done

    # 参数校验
    if [ -z "$repo_path" ]; then
        log_error "缺少 <repo-path> 参数"
        echo "用法: $(basename "$0") <repo-path> --profile <name>"
        harness_exit 1
    fi
    if [ -z "$profile_name" ]; then
        log_error "缺少 --profile <name> 参数"
        echo "用法: $(basename "$0") <repo-path> --profile <name>"
        harness_exit 1
    fi

    # Step 1: 验证 repo 路径可读
    step_begin "验证仓库路径: ${repo_path}"

    # 解析绝对路径
    repo_path="$(realpath -m "$repo_path" 2>/dev/null || realpath "$repo_path" 2>/dev/null || echo "$repo_path")"

    if [ ! -d "$repo_path" ]; then
        log_error "路径不是目录或不存在: ${repo_path}"
        step_end 1
        harness_exit 1
    fi
    if [ ! -r "$repo_path" ]; then
        log_error "路径不可读: ${repo_path}"
        step_end 1
        harness_exit 1
    fi
    step_end 0

    # Step 2: 检查路径是否已注册
    step_begin "检查路径是否已注册"

    local existing_id
    existing_id=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" list 2>/dev/null | awk -v p="$repo_path" -F'\t' '$2 == p {print $1}')
    if [ -n "$existing_id" ]; then
        log_error "仓库路径已注册: ${repo_path} (id=${existing_id})"
        step_end 1
        harness_exit 1
    fi
    step_end 0

    # Step 3: 注册到 registry
    step_begin "注册仓库到 registry"

    local repo_id
    # registry add 输出 id 到 stdout（log_info 走 stderr），捕获 stdout
    # 注意: registry add 的 harness_init/exit 也会输出汇总到 stderr，不影响
    local add_output
    add_output=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" add "$repo_path" --profile "$profile_name" 2>/dev/null)
    local add_rc=$?
    # 取第一行（id），跳过 harness 汇总行
    repo_id=$(echo "$add_output" | grep -E '^[a-f0-9]{12}$' | head -1)
    if [ "$add_rc" -ne 0 ] || [ -z "$repo_id" ]; then
        log_error "注册仓库失败: ${repo_path}"
        step_end 1
        harness_exit 1
    fi

    log_info "仓库已注册，id=${repo_id}"
    step_end 0

    # Step 4: 注入 overlay
    step_begin "注入 overlay (id=${repo_id})"

    local overlay_root inject_output
    inject_output=$(bash "$SCRIPT_DIR/lc-inject.sh" "$repo_id" 2>&1)
    local inject_rc=$?
    # 提取以 / 开头的路径行作为 overlay_root
    overlay_root=$(echo "$inject_output" | grep -E '^/' | tail -1)

    if [ "$inject_rc" -ne 0 ]; then
        log_error "注入失败，执行回滚"
        step_end 1

        # 回滚：从 registry 中移除条目
        step_begin "回滚: 删除 registry 条目"
        local rm_output
        rm_output=$(bash "$SCRIPT_DIR/lc-repo-registry.sh" remove "$repo_id" 2>&1)
        local rm_rc=$?
        if [ "$rm_rc" -eq 0 ]; then
            log_info "回滚成功: 已移除 registry 条目 ${repo_id}"
            step_end 0
        else
            log_warn "回滚部分失败: ${rm_output}"
            step_end 1
        fi
        harness_exit 1
    fi

    log_info "overlay 注入完成: ${overlay_root}"
    step_end 0

    # Step 5: 健康检查（内联）
    step_begin "健康检查 (id=${repo_id})"

    local healthy=false
    if inline_health_check "$repo_id"; then
        healthy=true
        step_end 0
    else
        log_warn "附加已完成但健康检查未通过，状态标记为 unhealthy"
        log_warn "请手动执行 reconcile: lc-repo-registry.sh update ${repo_id} state broken"
        step_end 1
    fi

    # Step 6: 输出 summary
    local health_status="healthy"
    if [ "$healthy" = false ]; then
        health_status="unhealthy"
    fi

    echo ""
    log_info "==========================================="
    log_info "  Attach 完成"
    log_info "  ID:       ${repo_id}"
    log_info "  Path:     ${repo_path}"
    log_info "  Profile:  ${profile_name}"
    log_info "  Overlay:  ${overlay_root}"
    log_info "  Status:   ${health_status}"
    log_info "==========================================="

    # stdout 输出 summary（供程序消费）
    echo "Attached: ${repo_id} -> ${repo_path} [${health_status}]"

    if [ "$healthy" = false ]; then
        harness_exit 1
    fi
    harness_exit 0
}

main "$@"