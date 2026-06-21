#!/bin/bash
# le.sh — Loop Engineering v2 统一 CLI 入口
# 用法:
#   le.sh run --suite boot-success --fixture <jsonl> --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
#   le.sh run --suite boot-success --host 127.0.0.1 --port 9700 --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../harness/lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../harness/lib/shell/harness_bootstrap.sh"

harness_init "le"

export PYTHONPATH="$(harness_pythonpath)${PYTHONPATH:+:$PYTHONPATH}"

python3 -m loop_core.cli "$@"
rc=$?

# 收尾清理 runs/ 下过期 run-id 子目录（失败不中断主流程）
# 保留份数由环境变量 LE_RUNS_KEEP 控制，默认 20
bash "$SCRIPT_DIR/le_runs_cleanup.sh" --keep "${LE_RUNS_KEEP:-20}" \
    || log_warn "runs 清理失败（不影响本次运行结果，退出码 $rc 已保留）"

harness_exit "$rc"
