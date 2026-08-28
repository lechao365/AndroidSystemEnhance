#!/bin/bash
# ============================================================
# lciod_check.sh — lciod_check.py 的包装入口
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：供 verify-cases 的 hostcmd 用例调用。先做 adb 连接与
#   root（lciod_probe 需访问 /dev 节点），再执行校验器并透传退出码。
# 用法：lciod_check.sh <lciod_check.py 参数...>
# ============================================================

set -u

CASES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$CASES_DIR/lciod_check.py"

# 设备定位统一走 ws_adb_connect.host_port（默认 rp5.local:5555，
# LC_VERIFY_ADB_HOST/PORT 环境变量覆盖），禁止在此硬编码 IP/端口
ADB_TARGET="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from ws_adb_connect import host_port; print(host_port())' "$CASES_DIR/..")"

# 连接 + root + 重连（adb root 会重启 adbd，需重新 connect）
adb -s "${ADB_TARGET}" connect >/dev/null 2>&1
adb -s "${ADB_TARGET}" root >/dev/null 2>&1
sleep 2
adb -s "${ADB_TARGET}" connect >/dev/null 2>&1

exec python3 "$PY" "$@"
