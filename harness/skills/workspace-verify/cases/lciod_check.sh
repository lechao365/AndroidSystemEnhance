#!/bin/bash
# ============================================================
# lciod_check.sh — lciod_check.py 的包装入口
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：供 verify-cases 的 hostcmd 用例调用。瘦身为纯 exec python3——
#   adb 连接 + root + 重连已由 lciod_check.py 内 ensure_connected 承担
#   （含 root already-running 快路径），wrapper 不再重复 connect/root/sleep。
# 用法：lciod_check.sh <lciod_check.py 参数...>
# ============================================================

set -u

CASES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$CASES_DIR/lciod_check.py"

exec python3 "$PY" "$@"
