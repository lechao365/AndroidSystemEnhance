#!/usr/bin/env python3
# ============================================================
# ws_forensics.py — 失败时有界取证（workspace-verify）
# 所属模块：workspace-verify — 失败现场采集
# 设计目的：verify 失败后收据正文只有零散摘录，AI 复盘缺系统现场；
#   本脚本一次收齐 host 侧 stdout/stderr 与设备侧只读快照，全部有界
#   （单文件/总量双上限），只读不改设备态（命令白名单硬编码，无写动作）。
# 采集项：
#   - host 侧现场：--stdout-file / --stderr-file（调用方保存的失败输出）
#   - logcat crash buffer：adb logcat -d -b crash（崩溃专属缓冲，不受
#     5000 行主缓冲滚动影响）
#   - getprop / df / ps -A：设备态快照（属性/存储/进程）
#   - 尽力取：dmesg（无权限/失败仅记录 error，不算整体失败）；
#     pstore（ls /sys/fs/pstore 后逐文件 cat，最多 MAX_PSTORE_FILES 个）
#   - tombstone 只取本轮新增：/data/tombstones/ 下 mtime 晚于
#     --since-epoch 的文件（--since-epoch 传验证开始时刻；最多
#     MAX_TOMBSTONES 个，文件名白名单过滤防注入）
# 上限：单文件 MAX_FILE_BYTES 截断；总量 MAX_TOTAL_BYTES 达到即跳过后续
#   采集项（manifest 如实记录 truncated/skipped，不静默丢失）。
# 用法：python3 ws_forensics.py [--endpoint <ip:port>]
#   [--stdout-file <f>] [--stderr-file <f>] [--since-epoch <秒>]
#   [--out-dir <目录>]（默认 harness/log/forensics/）
# 退出码：0 取证完成（尽力而为，单项失败不阻断）/ 2 参数错误
# ============================================================

import argparse
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

# 单文件上限（字节）：超限截断并标记，防单条巨日志撑爆磁盘与收据链
MAX_FILE_BYTES = 512 * 1024
# 总量上限（字节）：达到即跳过后续采集项（manifest 记 skipped）
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_PSTORE_FILES = 8
MAX_TOMBSTONES = 8
# tombstone 文件名白名单（来自设备 ls 输出，过滤防命令注入）
_TOMBSTONE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# 设备侧采集命令（全部只读；顺序即落盘编号，01 留给 host stdout）
_DEVICE_CASES = [
    ("02-logcat-crash.txt", ["logcat", "-d", "-b", "crash"]),
    ("03-getprop.txt", ["shell", "getprop"]),
    ("04-df.txt", ["shell", "df"]),
    ("05-ps.txt", ["shell", "ps", "-A"]),
]
# 尽力项：失败仅记录 error，不算整体失败（dmesg 常因无权限失败）
_BEST_EFFORT_CASES = [
    ("06-dmesg.txt", ["shell", "dmesg"]),
]


def adb_run(ep, args, timeout=60):
    """adb -s <ep> <args...>；返回 (stdout+stderr, returncode)。"""
    try:
        p = subprocess.run(["adb", "-s", ep] + args,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.stdout + p.stderr, p.returncode
    except (subprocess.TimeoutExpired, OSError):
        return "", -1


def _bounded_text(text):
    """按单文件上限截断文本，返回 (落盘内容, truncated)。"""
    data = (text or "").encode("utf-8", errors="replace")
    if len(data) <= MAX_FILE_BYTES:
        return text or "", False
    return data[:MAX_FILE_BYTES].decode("utf-8", errors="ignore"), True


class _Budget:
    """总量预算（方向 5）：剩余字节不足即拒绝后续写入。"""

    def __init__(self, total=MAX_TOTAL_BYTES):
        self.left = total

    def take(self, n):
        if self.left <= 0:
            return False
        self.left -= n
        return True

    def exhausted(self):
        return self.left <= 0


def _atomic_write_json(path, data):
    """原子写 manifest：先写临时文件再 os.replace，防半截 manifest 被当证据。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def _read_host_file(path):
    """读 host 侧现场文件（stdout/stderr），返回 (text, err)。"""
    if not path:
        return "", None
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace"), None
    except OSError as e:
        return "", f"读取失败: {e}"


def _device_ok(ep):
    """设备可达性轻探测（探活失败仍继续：host 侧现场照收）。"""
    _, rc = adb_run(ep, ["shell", "echo ok"], timeout=15)
    return rc == 0


def _list_pstore(ep):
    """列 /sys/fs/pstore 文件名（尽力；失败返空列表）。"""
    out, rc = adb_run(ep, ["shell", "ls /sys/fs/pstore 2>/dev/null"], timeout=30)
    if rc != 0:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _collect_pstore(ep, out_dir, budget, items):
    """pstore 逐文件 cat（最多 MAX_PSTORE_FILES 个，尽力项）。"""
    for name in _list_pstore(ep)[:MAX_PSTORE_FILES]:
        if not _TOMBSTONE_NAME_RE.match(name):
            continue
        rel = f"07-pstore-{name}"
        if budget.exhausted():
            items.append({"name": rel, "source": "pstore", "skipped": "total_budget"})
            continue
        out, rc = adb_run(ep, ["shell", f"cat /sys/fs/pstore/{name}"], timeout=30)
        _write_item(out_dir / rel, out, "pstore", rc, budget, items)


def _list_new_tombstones(ep, since_epoch):
    """列 /data/tombstones/ 下 mtime 晚于 since_epoch 的新增文件（只取本轮新增）。"""
    out, rc = adb_run(
        ep, ["shell", f"ls /data/tombstones 2>/dev/null"], timeout=30)
    if rc != 0:
        return []
    names = [ln.strip() for ln in out.splitlines() if ln.strip()]
    names = [n for n in names if _TOMBSTONE_NAME_RE.match(n)]
    if not names:
        return []
    # 逐个 stat mtime（toybox stat -c '%Y %n'）；探测失败的文件按未新增处理
    fresh = []
    for n in names:
        out, rc = adb_run(
            ep, ["shell", f"stat -c '%Y %n' /data/tombstones/{n}"], timeout=15)
        if rc != 0:
            continue
        parts = out.strip().split(None, 1)
        if parts and parts[0].isdigit() and int(parts[0]) > since_epoch:
            fresh.append(n)
    return fresh[:MAX_TOMBSTONES]


def _collect_tombstones(ep, out_dir, budget, items, since_epoch):
    """tombstone 只取本轮新增（方向 4）：逐个 cat 落盘。"""
    for n in _list_new_tombstones(ep, since_epoch):
        rel = f"08-tombstone-{n}"
        if budget.exhausted():
            items.append({"name": rel, "source": "tombstone",
                          "skipped": "total_budget"})
            continue
        out, rc = adb_run(ep, ["shell", f"cat /data/tombstones/{n}"], timeout=30)
        _write_item(out_dir / rel, out, "tombstone", rc, budget, items)


def _write_item(path, text, source, rc, budget, items):
    """单文件有界落盘 + items 登记（单文件截断/总量跳过均如实记录）。"""
    if not budget.take(len(text.encode("utf-8", errors="replace"))):
        items.append({"name": path.name, "source": source,
                      "skipped": "total_budget"})
        return
    body, truncated = _bounded_text(text)
    path.write_text(body, encoding="utf-8")
    items.append({"name": path.name, "source": source, "rc": rc,
                  "bytes": len(body.encode("utf-8")), "truncated": truncated})


def collect(ep=None, out_dir=None, stdout_file=None, stderr_file=None,
            since_epoch=0):
    """执行一轮取证，返回 (manifest, run_dir)。

    采集顺序：host stdout/stderr → logcat crash → getprop/df/ps →
    尽力 dmesg → pstore → 新增 tombstone。设备不可达时设备侧各项
    记 error，host 侧现场照收（尽力而为，exit 0）。
    """
    run_dir = Path(out_dir) if out_dir else \
        Path(__file__).resolve().parents[2] / "log" / "forensics" \
        / f"run-{time.strftime('%Y%m%d-%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    budget = _Budget()
    items = []

    # 01 host 侧现场
    for rel, path, source in (("01-stdout.txt", stdout_file, "host-stdout"),
                              ("01b-stderr.txt", stderr_file, "host-stderr")):
        if budget.exhausted():
            items.append({"name": rel, "source": source,
                          "skipped": "total_budget"})
            continue
        text, err = _read_host_file(path)
        if err:
            items.append({"name": rel, "source": source, "error": err})
            continue
        if not text:
            continue
        _write_item(run_dir / rel, text, source, 0, budget, items)

    if ep and _device_ok(ep):
        for rel, args in _DEVICE_CASES:
            if budget.exhausted():
                items.append({"name": rel, "source": "device",
                              "skipped": "total_budget"})
                continue
            out, rc = adb_run(ep, args, timeout=60)
            _write_item(run_dir / rel, out, "device", rc, budget, items)
        # 尽力项：失败仅记录
        for rel, args in _BEST_EFFORT_CASES:
            if budget.exhausted():
                items.append({"name": rel, "source": "device-best-effort",
                              "skipped": "total_budget"})
                continue
            out, rc = adb_run(ep, args, timeout=60)
            if rc != 0:
                items.append({"name": rel, "source": "device-best-effort",
                              "error": f"rc={rc}（尽力项，不算失败）"})
                continue
            _write_item(run_dir / rel, out, "device-best-effort", rc,
                        budget, items)
        try:
            _collect_pstore(ep, run_dir, budget, items)
        except (OSError, subprocess.TimeoutExpired) as e:
            items.append({"name": "07-pstore-*", "source": "pstore",
                          "error": f"尽力项失败: {e}"})
        try:
            _collect_tombstones(ep, run_dir, budget, items, since_epoch)
        except (OSError, subprocess.TimeoutExpired) as e:
            items.append({"name": "08-tombstone-*", "source": "tombstone",
                          "error": f"尽力项失败: {e}"})
    else:
        items.append({"name": "device", "source": "device",
                      "error": "设备不可达，设备侧快照未采集（host 侧现场照收）"})

    manifest = {
        "run_id": uuid.uuid4().hex,
        "endpoint": ep or "",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "since_epoch": since_epoch,
        "limits": {"file": MAX_FILE_BYTES, "total": MAX_TOTAL_BYTES},
        "items": items,
        "readonly": True,
    }
    _atomic_write_json(run_dir / "manifest.json", manifest)
    return manifest, run_dir


def main(argv=None):
    ap = argparse.ArgumentParser(description="失败时有界取证（只读）")
    ap.add_argument("--endpoint", default=None,
                    help="设备端点（缺省走 ensure_connected 自动发现）")
    ap.add_argument("--stdout-file", default=None,
                    help="host 侧失败 stdout 文件（调用方保存的现场）")
    ap.add_argument("--stderr-file", default=None,
                    help="host 侧失败 stderr 文件")
    ap.add_argument("--since-epoch", type=int, default=0,
                    help="本轮起始时刻（epoch 秒）；tombstone 只取 mtime 晚于它的"
                         "新增文件（应传验证开始时刻，缺省 0 = 全量旧 tombstone）")
    ap.add_argument("--out-dir", default=None,
                    help="取证产物目录（默认 harness/log/forensics/run-<ts>）")
    args = ap.parse_args(argv)

    ep = args.endpoint
    if not ep:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import ws_adb_connect as ac
            ep = ac.ensure_connected(rescue_enabled=False)
        except (OSError, ImportError):
            ep = None
    manifest, run_dir = collect(ep=ep, out_dir=args.out_dir,
                                stdout_file=args.stdout_file,
                                stderr_file=args.stderr_file,
                                since_epoch=args.since_epoch)
    print(f"forensics: {run_dir}")
    print(f"run_id: {manifest['run_id']}")
    for it in manifest["items"]:
        if "skipped" in it:
            print(f"  [SKIP] {it['name']}（{it['skipped']}）")
        elif "error" in it:
            print(f"  [ERR ] {it['name']}（{it['error']}）")
        else:
            print(f"  [OK  ] {it['name']} {it.get('bytes', 0)}B"
                  f"{'（截断）' if it.get('truncated') else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
