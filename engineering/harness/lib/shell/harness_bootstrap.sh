#!/bin/bash
# ============================================================================
# harness_bootstrap.sh — harness 脚本统一入口
# 职责:
#   1. source harness_path_util.sh（REPO_ROOT 定位 + 路径 API）
#   2. export REPO_ROOT（向后兼容）
#   3. source harness_observability.sh（暴露全部公共 API）
#
# 业务脚本用法（仅需两行）:
#   # shellcheck source=../../lib/shell/harness_bootstrap.sh
#   source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"
#
# source 后即可调用 harness_init / log_* / step_* / on_err / artifact_register 等。
# REPO_ROOT 全局可用。
# 仅需路径能力（不需 observability）的脚本可直接 source harness_path_util.sh。
# ============================================================================

# 本文件所在目录
_H_BOOTSTRAP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# source 路径工具（REPO_ROOT 定位 + paths.conf 加载 + harness_path API）
# shellcheck source=harness_path_util.sh
source "$_H_BOOTSTRAP_DIR/harness_path_util.sh"

# 导出 REPO_ROOT（向后兼容：业务脚本依赖 $REPO_ROOT 全局可用）
REPO_ROOT=$(harness_repo_root)
export REPO_ROOT

# source 公共维测库
# shellcheck source=harness_observability.sh
source "$_H_BOOTSTRAP_DIR/harness_observability.sh"
