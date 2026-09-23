#!/bin/bash
# ============================================================
# lciod_recover.sh — lciod HAL 重启恢复验证（kill HAL → init 拉起 + daemon 重连）
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：补齐 lciod 侧"无重启恢复用例"空洞——kill lechao_lciod_hal 进程后
#   断言：
#   - init 自动拉起 HAL（init.svc.lechao_lciod_hal == running）
#   - daemon（lechao_lciod）经 IoHalClient 自动重连（binder 死亡回调 →
#     指数退避重连，logcat 出现 "hal_client: reconnected to HAL"）
#   架构：init → lechao_lciod (/system) --AIDL--> lechao_lciod_hal (/vendor)；
#   rc 均非 oneshot，init 自动重启崩溃进程（HAL 死亡时间窗收敛到秒级）。
# 参考 lcview_recover.sh 模板（kill→wait_service→wait 观测恢复信号）。
# 用法：lciod_recover.sh
# 退出码：0 通过 / 1 失败（失败现场打印）/ 2 参数错误
# ============================================================

set -u

TARGET="${1:-hal}"
if [ "$TARGET" != "hal" ]; then
  echo "ERROR: 用法 $0（当前仅支持 hal 分支；daemon 恢复语义不同，未实现）" >&2
  exit 2
fi

# 设备端点经 ws_adb_connect.ensure_connected（mDNS→静态 fallback）自动发现，
# 不用 host_port() 字面值——WSL2 镜像模式下 rp5.local DNS 解析失败连不上
# （PIT-1：静态 fallback 用 mDNS 域名），静态地址漂移也能兜底
CASES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ADB_TARGET="$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from ws_adb_connect import ensure_connected; print(ensure_connected() or "")' "$CASES_DIR/..")"
if [ -z "$ADB_TARGET" ]; then
  echo "ERROR: 设备不可达（ensure_connected 失败）"
  exit 1
fi
adb -s "${ADB_TARGET}" root >/dev/null 2>&1
sleep 2
adb -s "${ADB_TARGET}" connect >/dev/null 2>&1

ADB() { adb -s "${ADB_TARGET}" "$@"; }

wait_service() {
  # 轮询 init 服务状态恢复 running（最多 15s）+ 断言进程 pid 已更替：
  # svc running 只说明 init 认为服务在跑，若 kill 未生效（进程未死）或
  # init 未真正拉起新进程（pid 复用）会假绿——须比对 kill 前 pid 已变化。
  # $1 = 服务名，$2 = kill 前 pid，$3 = 进程名（pidof 定位新进程）
  local svc="$1" old_pid="$2" proc_name="$3" state="" new_pid=""
  local i
  for i in $(seq 1 15); do
    state=$(ADB shell "getprop init.svc.${svc}" 2>/dev/null | tr -d '\r')
    new_pid=$(ADB shell "pidof ${proc_name}" 2>/dev/null | tr -d '\r' \
              | tr ' ' '\n' | tail -1)
    if [ "$state" = "running" ] && [ -n "$new_pid" ] \
       && [ "$new_pid" != "$old_pid" ]; then
      echo "OK: ${svc} 由 init 拉起（pid ${old_pid} → ${new_pid}）"
      return 0
    fi
    sleep 1
  done
  echo "ERROR: ${svc} 未由 init 拉起或 pid 未更替（state=${state}, "
  echo "       pid ${old_pid} → ${new_pid}）"
  return 1
}

wait_reconnect() {
  # kill 后轮询（最多 60s）直到 logcat 出现 daemon 重连日志（binder 死亡
  # 回调 → IoHalClient 退避重连成功）。以 kill 前时间戳为锚只认新行：
  # logcat -d -t 5000 限行数历史窗，但窗内可能混入 kill 前的旧重连日志
  # （上一轮 kill 的残留），须按时间戳过滤——只认 kill 时刻之后出现的
  # 重连行（R-03 方向 2 回炉，防假绿）。
  # $1 = kill 前 logcat 最后一行时间戳（MM-DD HH:MM:SS.mmm）
  local anchor_ts="$1" line_ts="" matched=""
  local i
  for i in $(seq 1 60); do
    matched=$(ADB logcat -d -t 5000 2>/dev/null \
              | grep "hal_client: reconnected to HAL" | tail -1)
    if [ -n "$matched" ]; then
      # 命中行时间戳须晚于 kill 前锚点（时间窗锚定，不认历史残留）
      line_ts=$(printf '%s\n' "$matched" | awk '{print $1, $2}')
      if [ -z "$anchor_ts" ] || [ "$line_ts" \> "$anchor_ts" ]; then
        echo "OK: daemon 已重连 HAL（hal_client: reconnected to HAL, "
        echo "    ts=${line_ts} > kill 前 ${anchor_ts:-无}）"
        return 0
      fi
    fi
    sleep 1
  done
  echo "ERROR: kill HAL 后 daemon 未观测到新重连日志（60s 超时，"
  echo "       IoHalClient 退避重连失败？）"
  return 1
}

hal_pid() {
  # HAL 进程 pid（多 pid 取最后一个，通常为主进程）；无则输出空
  ADB shell "pidof lechao_lciod_hal" 2>/dev/null | tr -d '\r' \
    | tr ' ' '\n' | tail -1
}

logcat_last_ts() {
  # logcat 最近一行时间戳（MM-DD HH:MM:SS.mmm）；无输出则空。
  # kill 前采样作为重连判定的时间锚点——只认该时刻之后的新日志行
  ADB logcat -d -t 1 2>/dev/null | head -1 | awk '{print $1, $2}'
}

kill_hal() {
  # 取 HAL 进程 pid（多 pid 取最后一个，通常为主进程）并 kill
  local pid=""
  pid=$(hal_pid)
  [ -n "$pid" ] || { echo "ERROR: lechao_lciod_hal 进程不存在"; return 1; }
  ADB shell "kill ${pid}" >/dev/null 2>&1
  echo "killed lechao_lciod_hal pid=${pid}"
}

# 前置断言：HAL 服务存在且运行中（用例语义前提，缺失即无 kill 目标）
HAL_PID_BEFORE=$(hal_pid)
if [ -z "$HAL_PID_BEFORE" ]; then
  echo "ERROR: 前置断言失败——lechao_lciod_hal 未运行，无法验证恢复"
  exit 1
fi
# daemon 须存活（重连方存在；lcview 无此步因 daemon 即被测方）
if [ -z "$(ADB shell "pidof lechao_lciod" 2>/dev/null | tr -d '\r')" ]; then
  echo "ERROR: 前置断言失败——lechao_lciod daemon 未运行，无重连方"
  exit 1
fi

# 1. kill HAL + init 拉起（rc 非 oneshot，init 秒级自动重启）。
#    kill 前采样 HAL pid 与 logcat 时间锚（R-03 方向 2）：
#    - wait_service 比对 pid 更替（kill 未生效/pid 复用作假绿判红）
#    - wait_reconnect 只认时间锚之后的新重连日志（防历史残留假绿）
KILL_TS_ANCHOR=$(logcat_last_ts)
kill_hal || exit 1
wait_service lechao_lciod_hal "$HAL_PID_BEFORE" lechao_lciod_hal || exit 1
# 2. daemon 自动重连（IoHalClient binder 死亡回调 → 退避重连）
wait_reconnect "$KILL_TS_ANCHOR" || exit 1

echo "OK: kill HAL 后 init 拉起（pid ${HAL_PID_BEFORE} 更替）+ daemon 重连"
exit 0
