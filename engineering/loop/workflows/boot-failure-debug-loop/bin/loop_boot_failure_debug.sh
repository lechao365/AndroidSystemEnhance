#!/bin/bash
# loop_boot_failure_debug.sh — boot-failure-debug-loop v1 WSL2 入口
# 用法:
#   离线回放: loop_boot_failure_debug.sh --fixture <jsonl> --device-profile <json> --workflow-profile <json> --artifacts-dir <dir>
#   live 模式: loop_boot_failure_debug.sh --host 127.0.0.1 --port 9700 --device-profile <json> --workflow-profile <json> --artifacts-dir <dir>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../harness/lib/harness_bootstrap.sh"

harness_init "loop-boot-failure-debug"

PYTHON_ROOT="$SCRIPT_DIR/../python"
PROVIDER_ROOT="$SCRIPT_DIR/../../../connection/providers/rp5-serial/python"
export PYTHONPATH="$PYTHON_ROOT:$PROVIDER_ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 -m boot_failure_debug.cli "$@"
rc=$?

harness_exit "$rc"
