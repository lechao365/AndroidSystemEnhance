#!/bin/bash
# ============================================================================
# harness_bootstrap.sh — harness 脚本统一入口
# 职责:
#   1. 从 BASH_SOURCE 向上查找 REPO_ROOT（AGENTS.md 锚点）
#   2. export REPO_ROOT
#   3. source harness_observability.sh（暴露全部公共 API）
#
# 业务脚本用法（仅需两行）:
#   # shellcheck source=../../lib/harness_bootstrap.sh
#   source "$SCRIPT_DIR/../../lib/harness_bootstrap.sh"
#
# source 后即可调用 harness_init / log_* / step_* / on_err / artifact_register 等。
# REPO_ROOT 全局可用。
# ============================================================================

_h_bootstrap_find_root() {
    local bsrc="$1"
    local dir
    dir="$(cd "$(dirname "$bsrc")" && pwd)"
    local root="$dir"
    while [ "$root" != "/" ] && [ ! -f "$root/AGENTS.md" ]; do
        root="$(dirname "$root")"
    done
    if [ ! -f "$root/AGENTS.md" ]; then
        echo "ERROR: harness_bootstrap 未找到项目根（AGENTS.md 锚点缺失）" >&2
        exit 3
    fi
    printf '%s' "$root"
}

# 由调用者脚本路径推导 REPO_ROOT（兼容 source 时的 BASH_SOURCE）
REPO_ROOT=$(_h_bootstrap_find_root "${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}")
export REPO_ROOT

# source 公共维测库
# shellcheck source=harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"
