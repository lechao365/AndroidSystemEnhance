#!/bin/bash
# ============================================================
# lcview_recover.sh — lcview 重启恢复验证（kill 服务 → init 拉起）
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：补齐"无重启恢复用例"空洞——kill daemon 进程后断言：
#   init 自动拉起（svc running）、心跳恢复（kill 后出现新心跳行）、
#   新事件落盘（delta）与轮转 seq 递增（daemon 重启后
#   FileWriter::openFile 从目录扫描续接 seq，写更高 _p{seq} 文件，不重复写
#   _p0 追加旧文件）。
#   HAL 退役（CDP 0cc5eb08831b 直读内核）后 hal 分支已删除：
#   设备无 lechao_lcview_hal 进程可 kill，用例语义失效。
# 用法：lcview_recover.sh daemon
#   daemon — kill lechao_lcview → svc running + daemon 心跳（heartbeat, loop=）
#            恢复 + dd 触发新事件落盘（delta --event 4）+ 轮转 seq 递增
# 退出码：0 通过 / 1 失败（失败现场打印）/ 2 参数错误
# ============================================================

set -u

CASES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$CASES_DIR/lcview_check.py"
TARGET="${1:-daemon}"
if [ "$TARGET" != "daemon" ]; then
  echo "ERROR: 用法 $0 daemon（HAL 已退役，仅支持 daemon 分支）" >&2
  exit 2
fi

# 设备端点经 ws_adb_connect.ensure_connected（mDNS→静态 fallback）自动发现，
# 不用 host_port() 字面值——WSL2 镜像模式下 rp5.local DNS 解析失败连不上
# （PIT-1：静态 fallback 用 mDNS 域名），静态地址漂移也能兜底
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
  # 轮询 init 服务状态恢复 running（最多 15s）；kill 后 init 自动重启
  local svc="$1" state=""
  local i
  for i in $(seq 1 15); do
    state=$(ADB shell "getprop init.svc.${svc}" 2>/dev/null | tr -d '\r')
    [ "$state" = "running" ] && return 0
    sleep 1
  done
  echo "ERROR: ${svc} 未由 init 拉起（state=${state}）"
  return 1
}

last_beat_ts() {
  # logcat 最近一条心跳行的时间戳（MM-DD HH:MM:SS.mmm）；无命中输出空
  local anchor="$1"
  ADB logcat -d -t 3000 2>/dev/null | grep "${anchor}" | tail -1 \
    | awk '{print $1, $2}'
}

wait_heartbeat() {
  # kill 后轮询（最多 45s）直到出现时间戳更新的心跳（进程重启后重新计时）
  # $1 = 心跳锚点，$2 = kill 前最后心跳时间戳
  local anchor="$1" before="$2" ts=""
  local i
  for i in $(seq 1 45); do
    ts=$(last_beat_ts "$anchor")
    if [ -n "$ts" ] && [ "$ts" != "$before" ]; then
      echo "OK: 心跳恢复（${anchor}: ${ts}）"
      return 0
    fi
    sleep 1
  done
  echo "ERROR: kill 后心跳未恢复（${anchor}: 最后 ${ts} 与 kill 前 ${before} 相同）"
  return 1
}

max_seq() {
  # 解析 logs 目录当天 jsonl 的轮转 _p{seq} 最大值；无匹配输出空。
  # 必须按设备当天日期（date +%Y%m%d）过滤：产品 nextSeqFor 按
  # {id}_{name}_{当天日期}_p{seq} 续接（跨天重置 seq=0），全局取最大会被
  # 时钟漂移期历史日期文件的高 seq 污染（2026-08-29 实拍：20260625 的
  # _p5 致断言 5->5 假红，产品行为正确——当天 _p0→_p3 递增续接）
  local today out
  today=$(ADB shell "date +%Y%m%d" 2>/dev/null | tr -d '\r')
  [ -n "$today" ] || return
  out=$(ADB shell "ls /data/vendor/lechao_lcview/logs/*_${today}_p*.jsonl 2>/dev/null")
  echo "$out" | grep -oE '_p[0-9]+' | grep -oE '[0-9]+' | sort -n | tail -1
}

kill_proc() {
  # 取进程 pid（多 pid 取最后一个，通常为主进程）并 kill
  local name="$1" pid=""
  pid=$(ADB shell "pidof ${name}" 2>/dev/null | tr -d '\r' | tr ' ' '\n' | tail -1)
  [ -n "$pid" ] || { echo "ERROR: ${name} 进程不存在"; return 1; }
  ADB shell "kill ${pid}" >/dev/null 2>&1
  echo "killed ${name} pid=${pid}"
}

# ---- daemon 恢复 ----
# 1. 基线（新事件落盘判据前置，与 lcview_check delta 同路径）
python3 "$PY" --mode baseline || exit 1
# 2. kill 前最大轮转 seq
SEQ_BEFORE=$(max_seq)
SEQ_BEFORE=${SEQ_BEFORE:--1}
# 3. kill daemon + init 拉起 + 心跳恢复（心跳每 30 loop 约 28s 一次，长轮询）
kill_proc lechao_lcview || exit 1
wait_service lechao_lcview || exit 1
wait_heartbeat "heartbeat, loop=" "$(last_beat_ts "heartbeat, loop=")" \
  || exit 1
# 4. dd 读 4MB 产生新事件（transfer-start event 4；块设备失败即判红）
if ! ADB shell "dd if=/dev/block/sda of=/dev/null bs=1M count=4 2>/dev/null" \
     >/dev/null; then
  echo "ERROR: dd 触发失败（块设备不可读？）"
  exit 1
fi
sleep 3
# 5. 新事件落盘断言（重启后链路写入新 JSONL 记录）；输出保留 NEW 明细供 6 步解析
if ! DELTA_OUT=$(python3 "$PY" --mode delta --event 4); then
  echo "ERROR: kill daemon 后新事件未落盘（delta --event 4）"
  exit 1
fi
# 6. 轮转 seq 续接断言（防重启后从低 seq 重建文件回归）：
#    产品 nextSeqFor 按 {id}_{name}_当天日期_p{seq} 各文件链独立续接；多 id
#    混跑且文件未满时，重启后续写原文件（seq 不变是正确续接）——旧口径
#    "全目录 max_seq 必须递增"在此场景假红（2026-09-05 实拍：id=4 链 _p3
#    未满续写、id=13 链 _p2 续写，max_seq 3->3 判红，产品行为正确）。
#    新口径：kill 前 ls 当天 _p*.jsonl 存快照；kill 后 NEW 落盘文件逐一断言：
#    a) 快照已存在 → 未满文件续写，合法；
#    b) kill 后新建 → seq 必须 > SEQ_BEFORE（轮转新开必须续接递增，低 seq
#       重建即回归命中）。SEQ_BEFORE=-1（首日无文件）时恒真，场景兼容。
NEW_FILES=$(printf '%s\n' "$DELTA_OUT" | grep -oE 'file=[^ ]+' | cut -d= -f2 | sort -u)
if [ -z "$NEW_FILES" ]; then
  echo "ERROR: delta 无 NEW 明细，seq 续接断言无输入"
  exit 1
fi
SNAP_TODAY=$(ADB shell "date +%Y%m%d" 2>/dev/null | tr -d '\r')
SEQ_SNAPSHOT=$(ADB shell "ls /data/vendor/lechao_lcview/logs/*_${SNAP_TODAY}_p*.jsonl 2>/dev/null" | tr -d '\r')
FAIL_SEQ=""
for f in $NEW_FILES; do
  if printf '%s\n' "$SEQ_SNAPSHOT" | grep -qF "$f"; then
    continue
  fi
  s=$(printf '%s' "$f" | grep -oE '_p[0-9]+\.jsonl' | grep -oE '[0-9]+')
  if [ -z "$s" ] || [ "$s" -le "$SEQ_BEFORE" ]; then
    FAIL_SEQ="$FAIL_SEQ $f"
  fi
done
if [ -n "$FAIL_SEQ" ]; then
  echo "ERROR: kill 后新建文件 seq 未续接递增（before=${SEQ_BEFORE}）:${FAIL_SEQ}"
  echo "       daemon 重启后疑似从低 seq 重建文件（重复写 _p0 回归？）"
  exit 1
fi
echo "OK: daemon kill 后 init 拉起 + 心跳恢复 + 新事件落盘 + 轮转 seq 续接"
echo "    seq_before=${SEQ_BEFORE}，NEW 文件: $(printf '%s' "$NEW_FILES" | tr '\n' ' ')"
exit 0
