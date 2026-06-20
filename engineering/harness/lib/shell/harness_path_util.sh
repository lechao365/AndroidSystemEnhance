#!/bin/bash
# ============================================================================
# harness_path_util.sh — 统一路径工具（shell 端）
# 规则详见: engineering/harness/rules/path-management.md (PATH-001)
#
# 职责:
#   1. 从 BASH_SOURCE / __file__ 向上查找 REPO_ROOT（AGENTS.md 锚点）
#   2. 加载 config/harness-paths.conf（单一事实源）
#   3. 提供路径查询公共 API（harness_path / harness_env_path / harness_pythonpath）
#
# 公共 API:
#   harness_repo_root              输出 REPO_ROOT 绝对路径
#   harness_path <KEY>             输出 harness-paths.conf 中 KEY 对应的绝对路径
#   harness_env_path <KEY>         输出环境可覆盖路径（先查 ENV，再查 config 默认值）
#   harness_pythonpath             输出拼好的 PYTHONPATH 字符串（绝对路径，冒号分隔）
#
# 用法:
#   source "$SCRIPT_DIR/../../lib/shell/harness_path_util.sh"
#   REPO_ROOT=$(harness_repo_root)
#   LOG_DIR=$(harness_path LOG_DIR)
#
# 注意:
#   - 本文件被 harness_bootstrap.sh source，是路径能力的底层依赖
#   - 业务脚本如仅需路径能力（不需 observability），可直接 source 本文件
# ============================================================================

# 防止重复 source
[ -n "${_HARNESS_PATH_UTIL_SOURCED:-}" ] && return 0
_HARNESS_PATH_UTIL_SOURCED=1

# ----------------------------------------------------------------------------
# 内部：定位 REPO_ROOT（AGENTS.md 锚点，与原 harness_bootstrap.sh 逻辑一致）
# ----------------------------------------------------------------------------
_h_path_find_root() {
    local bsrc="$1"
    local dir
    dir="$(cd "$(dirname "$bsrc")" && pwd)"
    local root="$dir"
    while [ "$root" != "/" ] && [ ! -f "$root/AGENTS.md" ]; do
        root="$(dirname "$root")"
    done
    if [ ! -f "$root/AGENTS.md" ]; then
        echo "ERROR: harness_path_util 未找到项目根（AGENTS.md 锚点缺失）" >&2
        exit 3
    fi
    printf '%s' "$root"
}

# 定位 REPO_ROOT（兼容 source 时的 BASH_SOURCE）
_H_PATH_REPO_ROOT=$(_h_path_find_root "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")

# ----------------------------------------------------------------------------
# 内部：加载 config/harness-paths.conf
# ----------------------------------------------------------------------------
# 解析 KEY="value" 格式到关联数组（bash 4+）
declare -gA _H_PATH_CONF 2>/dev/null || declare -A _H_PATH_CONF
_H_PATH_CONF_FILE="$_H_PATH_REPO_ROOT/engineering/harness/config/harness-paths.conf"

_h_path_load_conf() {
    local conf_file="$1"
    [ -f "$conf_file" ] || {
        echo "ERROR: harness-paths.conf 不存在: $conf_file" >&2
        exit 3
    }
    local line key val
    while IFS= read -r line || [ -n "$line" ]; do
        # 跳过空行和注释
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # 解析 KEY="value"
        if [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)=\"(.*)\"[[:space:]]*$ ]]; then
            key="${BASH_REMATCH[1]}"
            val="${BASH_REMATCH[2]}"
            _H_PATH_CONF["$key"]="$val"
        fi
    done < "$conf_file"
}
_h_path_load_conf "$_H_PATH_CONF_FILE"

# ----------------------------------------------------------------------------
# 公共 API
# ----------------------------------------------------------------------------

# harness_repo_root — 输出 REPO_ROOT 绝对路径
harness_repo_root() {
    printf '%s' "$_H_PATH_REPO_ROOT"
}

# harness_path <KEY> — 输出 harness-paths.conf 中 KEY 对应的绝对路径
# 相对路径基于 REPO_ROOT 解析；已是绝对路径则原样返回
harness_path() {
    local key="$1"
    local val="${_H_PATH_CONF[$key]:-}"
    if [ -z "$val" ]; then
        echo "ERROR: harness_path: 未知的路径 key '$key'" >&2
        return 1
    fi
    # 展开可能的 $HOME 等变量
    val=$(eval "echo \"$val\"")
    # 相对路径基于 REPO_ROOT 解析
    case "$val" in
        /*) printf '%s' "$val" ;;
        *)  printf '%s/%s' "$_H_PATH_REPO_ROOT" "$val" ;;
    esac
}

# harness_env_path <KEY> — 输出环境可覆盖路径
# KEY 为 harness-paths.conf 中的 ENV_* 键（如 ENV_KERNEL_WS）
# 逻辑: 取 ENV_* 的值（已含 ${ENV_VAR:-default}），eval 展开
harness_env_path() {
    local key="$1"
    local val="${_H_PATH_CONF[$key]:-}"
    if [ -z "$val" ]; then
        echo "ERROR: harness_env_path: 未知的路径 key '$key'" >&2
        return 1
    fi
    # ENV_* 值形如 "$HOME/workspace/..."，eval 展开环境变量
    eval "printf '%s' \"$val\""
}

# harness_pythonpath — 输出拼好的 PYTHONPATH 字符串（绝对路径，冒号分隔）
harness_pythonpath() {
    local roots="${_H_PATH_CONF[PYTHON_PATH_ROOTS]:-}"
    [ -z "$roots" ] && return 0
    local result=""
    local IFS=':'
    local root
    for root in $roots; do
        case "$root" in
            /*) [ -z "$result" ] && result="$root" || result="$result:$root" ;;
            *)  local abs="$_H_PATH_REPO_ROOT/$root"
                [ -z "$result" ] && result="$abs" || result="$result:$abs" ;;
        esac
    done
    printf '%s' "$result"
}
