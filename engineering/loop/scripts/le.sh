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

# 主执行：分发到 loop_core.cli（run/control/setup 等子命令透传 "$@"）
step_begin "le run"
python3 -m loop_core.cli "$@" || on_err --continue "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
rc=$?
step_end "$rc"

# 收尾清理 runs/ 下过期 run-id 子目录（失败不中断主流程）
# 保留份数由环境变量 LE_RUNS_KEEP 控制，默认 20
step_begin "runs cleanup"
if bash "$SCRIPT_DIR/le_runs_cleanup.sh" --keep "${LE_RUNS_KEEP:-20}"; then
    step_end 0
else
    log_warn "runs 清理失败（不影响本次运行结果，退出码 $rc 已保留）"
    step_end 1
fi

harness_exit "$rc"
