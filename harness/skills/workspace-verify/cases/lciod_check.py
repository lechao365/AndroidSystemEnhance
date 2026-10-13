#!/usr/bin/env python3
# ============================================================
# lciod_check.py — lciod 业务数据板端校验器（host 侧执行）
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：通过 adb 在设备侧执行 lciod_probe（/system/bin，
#   内核 GET_STATS ioctl 取数工具），host 侧完成字段齐全性/
#   数值合法性/增量基线校验。设备侧无 python3，复杂解析全部
#   在 host 完成；设备侧只做单二进制最小操作。
#
# 模式：
#   stats    — 校验 probe 快照：≥1 设备、abi_version==5、字段齐全、
#              数值非负、vendor/product 非空、protocol∈{0,1}（字段映射
#              完整性回归点）
#   uas      — UAS 专项（R1 维测）：要求 ≥1 设备 protocol==1（UAS 设备）
#              且该设备传输统计字段齐全；无 UAS 设备记 skip（exit 0 并打印
#              "SKIP:" 标记，由验收层转为 skip 态不判红）
#   qos      — SD 卡写 QoS 限速校验（R4 方向 4/6）：依赖 daemon IoQosManager
#              建 lechao_bg cgroup + ioqos_level sysprop 驱动——adb root 直写
#              cgroup.procs 把测试进程迁入组，dd 写 /data/local/tmp 临时文件
#              测速，断言组内 ≤ bg 上限容差、组外显著高于（≥ 1.5x 组内或
#              ≥ 20MiB/s）；无破坏性写守卫依赖（临时文件走安全路径）
#   link     — 全局链路节点（R2 方向 7）：/dev/vendor_lechao_usbd_link
#              GET_LINK_STATS ioctl 快照校验——节点可打开、字段齐全、
#              值合法、abi_version==5（连接/断开/枚举失败/过流计数）
#   baseline — [--reset] 取快照存 --baseline（供 delta；
#              --reset 传给设备工具归零计数，delta 断言简化为绝对值）
#   delta    — 对比基线，聚合断言：≥1 设备在全部 --expect 字段严格增加
#              即视为触发生效（dd 只作用于一个块设备，多设备其余零增量属
#              预期）；无任一设备增量 / 缺基线 / 缺字段均判红（防假绿）
#   perf     — 性能采集（R-02 方向 2，只报数不设门禁）：dd 读块设备
#              --load-mb MB（默认 64）驱动真实 USB 传输 → probe 前后
#              快照差分算 per-IO ns（Δread_ns/Δread_cmds）、吞吐
#              （Δread_bytes/dd 秒）、最近传输延迟（last_transport_latency_ns）
#   storm    — 风暴规则校验（R3 方向 7）：用户态合成注入——adb root setprop
#              sys.lechao.lciod.fault_inject stall:20（daemon 每 tick 读该
#              sysprop，值变化时把 n 加进当前桶，幂等一次性）→ sleep 等
#              daemon 10s tick + 规则三评估窗口 → logcat 断言 tag
#              lechao_lciod_event 出现 "rule":"storm"（判红否则）
#   sd       — SD 卡用户态验收（A 批二 方向 3/4）：自驱产载（drop_caches +
#              dd 读 LCIOD_SD_BLOCK_DEV，默认 mmcblk0，只读到 /dev/null）+
#              三重断言——
#              (1) sysfs：/sys/class/lciod_sd/lciod_sd/ 下 enable/window_bytes/
#                  window_requests/stuck_count/ssr_speed_class/ssr_uhs_grade/
#                  ssr_valid 属性齐全且数值合法；
#              (2) SD_HEALTH：lcview 日志目录下含 sd_health 的 JSONL 文件中
#                  存在 "id":15 或 "name":"sd_health" 记录；
#              (3) sd JSON：logcat tag lechao_lciod_event 出现 "rule":"sd"。
#              缺证据（目录不可读/属性缺失/无事件/无匹配）均判红。
#   histo    — USB 传输延迟/块大小二维直方图用户态验收（C 批二 方向 3）：
#              lciod_probe --histo（GET_LATENCY_HISTOGRAM cmd5）取数 → 结构
#              断言（dirs=2/size_bins=8/latency_bins=12、counts 长度 192、
#              size/latency 档位上界严格递增且末档饱和、abi_version=5）→
#              drop_caches + dd 读块设备（默认 /dev/block/sda，LCVIEW_BLOCK_DEV
#              覆盖，只读到 /dev/null 无破坏面）→ 差分断言 ≥1 设备读方向
#              （dir 0）计数之和严格增量（防假绿；无增量判红）。
#   sd-inject — SD 自愈演练闭环校验（SD 自愈演练接口与故障面断言闭环）：
#              自驱向 sysfs inject_stuck（root-only，写一次触发一次）注入一次
#              合成 STUCK 事件（仅内存模拟，不依赖真卡异常）→ 四重断言——
#              (1) stuck_count 计数递增（注入前读基线）；
#              (2) lcview JSONL 新增 sd_stuck(id=16) 记录；
#              (3) last_speed 反映降档（等于新增事件 new_speed，且 old>new）；
#              (4) 内核留痕：logcat -b kernel（或 dmesg 计数增量）含 "sd stuck"。
#              缺证据（属性不可读/注入失败/计数未增/无新事件/速度不符/无留痕）
#              均判红。
#
# 退出码：0 校验通过 / 1 校验失败 / 2 设备不可达或参数错误
# ============================================================

import argparse
import json
import os
import re
import struct
import sys
import time
from pathlib import Path

# 设备定位复用 ws_adb_connect（勿自建 adb 层）：host_port 默认 rp5.local:5555，
# 支持 LC_VERIFY_ADB_HOST/PORT 环境变量覆盖，mDNS 发现逻辑不在此重复实现
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ws_adb_connect import (ensure_connected as ws_connected,  # noqa: E402
                            host_port, run_adb)

ADB_TARGET = host_port()
_EP = ADB_TARGET


def _default_baseline(env_name="LCIOD_BASELINE_FILE"):
    """按轮次隔离的基线路径（方向 4）：轮次编排层经环境变量注入按轮次唯一
    路径（hostcmd 侧 ${LCIOD_BASELINE_FILE:-...} 同步透传），防跨轮基线串扰
    （A 轮写的基线被 B 轮 delta 误读）；未设置时回退固定默认（独立 CLI/单测）。"""
    return os.environ.get(env_name) or "/tmp/lciod_baseline.json"


BASELINE_DEFAULT = _default_baseline()

# probe 输出必须齐全的字段（与 lciod_probe.c 输出、ioctl.h v5 ABI 对齐；
# 缺任一字段即字段映射回归，stats 模式判红）
REQUIRED_FIELDS = [
    "minor", "path", "vid", "pid", "protocol", "vendor", "product",
    "read_bytes", "write_bytes", "read_ns", "write_ns",
    "read_cmds", "write_cmds",
    "error_count", "reset_count", "probe_count",
    "disconnect_count", "degrade_count",
    "current_rate", "peak_rate", "last_transport_latency_ns",
    "last_event_ts_ns", "last_update",
    "stall_count", "corrupt_count", "timeout_count",
    "last_event_type", "enabled", "flags", "event_drop_count",
    "read_error_count", "write_error_count",
    "abi_version",
]
# 非数值字段（vendor/product 可含空格引号包裹；path 为字符串）
_TEXT_FIELDS = {"path", "vendor", "product"}
_EXPECTED_ABI = "5"

# key=value 解析：vendor="SanDisk Corp" 等引号包裹值支持空格
_TOKEN_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')

# ---- C 批二 方向 3：直方图模式（--mode histo）常量 ----
# 维度与内核 lciod_histo.h / ABI v5 编译期固定一致
_HISTO_DIRS = 2
_HISTO_SIZE_BINS = 8
_HISTO_LAT_BINS = 12
# 累计矩阵单元数 = dirs * size_bins * lat_bins = 192
_HISTO_COUNT_CELLS = _HISTO_DIRS * _HISTO_SIZE_BINS * _HISTO_LAT_BINS
# 档位上界（内核编译期固定，末档饱和 2^64-1）：size 为字节，latency 为纳秒
_HISTO_SIZE_BOUNDS = (512, 4096, 16384, 65536, 262144, 1048576, 8388608,
                      2 ** 64 - 1)
_HISTO_LAT_BOUNDS = (1000, 5000, 10000, 50000, 100000, 500000, 1000000,
                     5000000, 10000000, 50000000, 100000000, 2 ** 64 - 1)
# 读方向（dir 0）计数的矩阵切片长度：size_bins * lat_bins
_HISTO_READ_CELLS = _HISTO_SIZE_BINS * _HISTO_LAT_BINS


def adb(args, timeout=60):
    """执行 adb 命令，返回 (stdout, returncode)。"""
    return run_adb(["-s", _EP] + args, timeout=timeout)


def ensure_connected():
    """adb 连接（复用 ws_adb_connect mDNS→静态 fallback）+ root + 探活。"""
    global _EP
    ep = ws_connected()
    if not ep:
        print(f"ERROR: 设备不可达（mDNS 与静态 {ADB_TARGET} 均失败）")
        sys.exit(2)
    _EP = ep
    out, rc = run_adb(["-s", ep, "root"])
    if rc != 0:
        print(f"ERROR: 设备 {ADB_TARGET} adb root 失败 rc={rc}: {out.strip()}")
        sys.exit(2)
    if "already running" in out:
        # adbd 已在 root：快路径跳过 sleep+重连（对齐 ws_upload_tests
        # ensure_user，真实切换才重启 adbd）
        return
    time.sleep(2)
    ep = ws_connected()
    if not ep:
        print(f"ERROR: 设备 {ADB_TARGET} root 后重连失败")
        sys.exit(2)
    _EP = ep


def device_now():
    """设备侧当前 epoch 秒；失败返回 None（storm 模式 logcat 锚点用）。"""
    out, rc = adb(["shell", "date +%s"])
    if rc == 0 and out.strip().isdigit():
        return int(out.strip())
    return None


def parse_probe_output(text):
    """解析 lciod_probe 输出（每设备一行 key=value）→ dict 列表。

    缺 minor/path 的残缺行抛 ValueError——残缺数据不得静默当有效
    快照（防假绿），由调用方转 exit 1。
    """
    devices = []
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        fields = {}
        for m in _TOKEN_RE.finditer(line):
            fields[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
        if "minor" not in fields or "path" not in fields:
            raise ValueError(f"probe 行 {ln} 缺 minor/path: {line[:100]}")
        devices.append(fields)
    return devices


def run_probe(extra_args=None):
    """设备侧执行 lciod_probe，返回 stdout。失败/超时按语义退出。"""
    out, rc = adb(["shell", "lciod_probe"] + (extra_args or []))
    if rc == -1:
        # adb 超时透传：不得当"无输出"假绿（对齐 lcview_check files 模式）
        print("ERROR: adb 执行 lciod_probe 超时")
        sys.exit(2)
    if rc != 0:
        print(f"ERROR: lciod_probe 退出码 {rc}（open/ioctl 失败）")
        sys.exit(1)
    return out


def validate_devices(devices):
    """stats 模式校验 → 错误列表（空 = 通过）。

    判红项：零设备 / 字段缺失 / 数值字段非数字或负值 /
    vendor·product 为空 / abi_version != 5（镜像副本与内核真相源漂移）。
    """
    errors = []
    if not devices:
        return ["probe 输出为空：无 usbd 设备（内核驱动/HAL 未就绪？）"]
    numeric = [f for f in REQUIRED_FIELDS if f not in _TEXT_FIELDS]
    for i, dev in enumerate(devices):
        tag = f"device#{i}(minor={dev.get('minor', '?')})"
        missing = [f for f in REQUIRED_FIELDS if f not in dev]
        for f in missing:
            errors.append(f"{tag}: 缺字段 {f}")
        for f in numeric:
            if f in missing:
                continue
            try:
                if int(dev[f], 0) < 0:
                    errors.append(f"{tag}: {f} 为负值: {dev[f]}")
            except ValueError:
                errors.append(f"{tag}: {f} 非数字: {dev[f]}")
        # 累计错误/丢事件必须为 0：error_count/event_drop_count/read(write)_error_count
        # 为无符号计数，仅校验非负恒真，错误或丢事件累计再多仍判绿；加等于 0 断言（防假绿）
        for f in ("error_count", "event_drop_count", "read_error_count", "write_error_count"):
            if f in missing:
                continue
            try:
                if int(dev[f], 0) != 0:
                    errors.append(f"{tag}: {f}={dev[f]} != 0（累计错误/丢事件，链路异常）")
            except ValueError:
                pass  # 非数字已由 numeric 检查判红
        if not dev.get("vendor", "").strip():
            errors.append(f"{tag}: vendor 为空（内核未上报厂商串）")
        if not dev.get("product", "").strip():
            errors.append(f"{tag}: product 为空（内核未上报产品串）")
        if dev.get("abi_version") != _EXPECTED_ABI:
            errors.append(f"{tag}: abi_version={dev.get('abi_version')} != {_EXPECTED_ABI}")
        if dev.get("enabled") != "1":
            # 监控功能禁用（enabled=0）时统计仍存在但不再更新，
            # 不断言会导致"监控被禁用仍全绿"假绿（对齐 lcview logfield 故障可见性）
            errors.append(f"{tag}: enabled={dev.get('enabled')} != 1（设备监控被禁用）")
        # 传输协议：须为 VENDOR_LECHAO_USBD_PROTO_BOT(0)/UAS(1) 之一（ioctl.h 宏）。
        # protocol==1 表示 UAS 设备（usb-storage uas 驱动），仅标注不强制——UAS
        # 存在性由 uas 模式单独断言。
        if dev.get("protocol") not in ("0", "1"):
            errors.append(f"{tag}: protocol={dev.get('protocol')} 非法"
                          "（须 VENDOR_LECHAO_USBD_PROTO_BOT(0)/UAS(1)）")
    return errors


def validate_uas_devices(devices):
    """uas 模式校验（UAS 存在性已由调用方判定）→ 错误列表（空 = 通过）。

    UAS 用例语义（R1 维测）：验证 UAS 打点链路——板端接入 UAS 设备
    （protocol==1，VENDOR_LECHAO_USBD_PROTO_UAS）时，该设备的传输统计
    字段（read/write/current_rate/last_transport_latency_ns 等）须齐全。
    R4 方向 7：无 UAS 设备不再判红（由 --mode uas 分支记 skip），本函数
    只在 UAS 设备存在时校验其字段完整性与打点链路（有 UAS 但字段不齐
    仍判红，防无 UAS 假绿放宽为假绿）。
    """
    errors = []
    uas_devs = [d for d in devices if d.get("protocol") == "1"]
    if not uas_devs:
        return errors
    # UAS 设备传输统计字段齐全性（read_bytes/current_rate/延迟等为打点链路
    # 关键观测量，validate_devices 已保证 REQUIRED_FIELDS 存在，此处按 UAS
    # 语义显式复核 + 防字段集漂移）
    uas_fields = ("read_bytes", "read_cmds", "read_ns",
                  "write_bytes", "write_cmds", "write_ns",
                  "current_rate", "peak_rate", "last_transport_latency_ns")
    for i, dev in enumerate(uas_devs):
        tag = f"UAS device#{i}(minor={dev.get('minor', '?')})"
        for f in uas_fields:
            if f not in dev:
                errors.append(f"{tag}: 缺传输统计字段 {f}（UAS 打点链路不完整）")
    return errors


# ---- 方向 7：全局链路节点 GET_LINK_STATS 校验（--mode link） ----

_LINK_DEV = "/dev/vendor_lechao_usbd_link"

# lciod_probe --link（GET_LINK_STATS ioctl 取数）输出必须齐全的字段（与
# lciod_link.c 的 struct vendor_lechao_usbd_link_stats 一一对应；缺任一
# 字段即字段映射回归，link 模式判红）
REQUIRED_LINK_FIELDS = [
    "connect_count", "disconnect_count", "enum_fail_count", "overcurrent_count",
    "last_event_ts_ns", "last_event_type",
    "last_busnum", "last_port", "last_vid", "last_pid",
    "last_err", "last_count", "last_duration_ns",
    "abi_version",
]

# 链路事件类型合法范围：复用事件枚举 0..9（含 v4 链路事件 7/8/9）
_LINK_EVENT_TYPE_MAX = 9


def link_stats_struct_size():
    """复算 struct vendor_lechao_usbd_link_stats 尺寸（ARM64 LE 自然对齐）。

    方向 7：与 C 侧 _IOR('R', 4, struct ...) 联动的 Python 侧契约复核点——
    GET_LINK_STATS 命令号含结构尺寸位段（size<<16），字段增删未三方同步时
    尺寸漂移在此判红（对齐 DeviceIo_test.cpp 的 sizeof 断言精神）。
    """
    return struct.Struct("@QQQQQIHHHHiIQ8s").size


def check_link_node():
    """设备侧断言全局链路节点存在（缺失判红，防假绿）。"""
    out, rc = adb(["shell", "ls", "-ld", _LINK_DEV])
    return rc == 0 and "vendor_lechao_usbd_link" in out


def run_link_probe():
    """设备侧执行 lciod_probe --link（GET_LINK_STATS ioctl 取数），返回 stdout。

    失败/超时按语义退出——节点打不开或 ioctl 失败不得当"无输出"假绿
    （对齐 run_probe 的 adb 超时透传与退出码语义）。
    """
    out, rc = adb(["shell", "lciod_probe", "--link"])
    if rc == -1:
        print("ERROR: adb 执行 lciod_probe --link 超时")
        sys.exit(2)
    if rc != 0:
        print(f"ERROR: lciod_probe --link 退出码 {rc}（open/ioctl 失败）")
        sys.exit(1)
    return out


def parse_link_output(text):
    """解析 lciod_probe --link 输出（单行 key=value）→ dict。

    缺关键字段（connect_count）抛 ValueError——残缺数据不得静默当有效
    快照（防假绿），由调用方转 exit 1。
    """
    line = text.strip()
    fields = {}
    for m in _TOKEN_RE.finditer(line):
        fields[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
    if "connect_count" not in fields:
        raise ValueError(f"link 输出缺 connect_count: {line[:100]}")
    return fields


def validate_link_stats(fields):
    """link 模式校验 → 错误列表（空 = 通过）。

    判红项：字段缺失 / 无符号计数非数字或负值 / last_err 非数字（s32
    errno 允许负值，只查数字）/ last_event_type 超出枚举范围 /
    abi_version != 5（镜像副本与内核真相源漂移）。
    """
    errors = []
    missing = [f for f in REQUIRED_LINK_FIELDS if f not in fields]
    for f in missing:
        errors.append(f"link: 缺字段 {f}")
    non_neg = [f for f in REQUIRED_LINK_FIELDS
               if f not in missing and f not in ("last_err", "abi_version")]
    for f in non_neg:
        try:
            if int(fields[f], 0) < 0:
                errors.append(f"link: {f} 为负值: {fields[f]}")
        except ValueError:
            errors.append(f"link: {f} 非数字: {fields[f]}")
    if "last_err" not in missing:
        try:
            int(fields["last_err"], 0)
        except ValueError:
            errors.append(f"link: last_err 非数字: {fields['last_err']}")
    if "last_event_type" not in missing:
        try:
            et = int(fields["last_event_type"], 0)
            if et < 0 or et > _LINK_EVENT_TYPE_MAX:
                errors.append(f"link: last_event_type={fields['last_event_type']} "
                              f"超出枚举范围 0..{_LINK_EVENT_TYPE_MAX}")
        except ValueError:
            pass  # 非数字已由 non_neg 检查判红
    if fields.get("abi_version") != _EXPECTED_ABI:
        errors.append(f"link: abi_version={fields.get('abi_version')} != {_EXPECTED_ABI}")
    return errors


def load_baseline(path):
    """读基线文件 → {minor_str: {field: int|str}}；不存在/损坏返回 None。

    数值字段归一为 int（支持 0x 前缀）；vendor/product 文本字段保留
    字符串（不参与 delta 对比，误设为 expect 时由 diff 的数字校验判红）。
    """
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
        out = {}
        for k, fields in raw.items():
            out[str(k)] = {f: (int(v, 0) if isinstance(v, str) else int(v))
                           if f not in _TEXT_FIELDS else v
                           for f, v in fields.items()}
        return out
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        return None


def diff_devices(baseline, devices, expect_fields):
    """delta 对比 → (错误列表, 报告行列表)。

    多设备聚合语义：dd 只作用于一个块设备，其对应的 usbd 通道才有增量；
    其余设备（内核枚举顺序不定；UAS 占位通道 vid/pid 全 0）零增量属预期。
    因此核心判据为「至少一个设备在全部 expect 字段上严格增量」——dd 未触发
    任何设备增量才判红（防假绿）；单设备场景退化为该设备必须增量。
    设备在基线中缺失、expect 字段缺失/非数字仍逐台判红（结构完整性，
    与增量归属无关）。全设备均为虚拟 UAS 占位通道（protocol==1 且 vid/pid
    全 0）且无增量时豁免（无真实 UAS 流量源，记 SKIP 提示）。
    baseline 结构与 load_baseline 返回一致：{minor_str: {field: value}}。
    """
    errors, report = [], []
    if not expect_fields:
        # 空 expect 时增量断言循环不执行、errors 恒空会直接判绿；
        # yaml 漏写 --expect 即核心增量断言全跳过，必须判红（防假绿）
        errors.append("delta 模式未指定 --expect 字段（核心增量断言全跳过）")
        return errors, report
    base = baseline if isinstance(baseline, dict) else {}
    if not base:
        errors.append("基线无设备数据（baseline 未写入或损坏）")
        return errors, report
    if not devices:
        errors.append("当前 probe 输出为空，无法对比")
        return errors, report

    # 虚拟 UAS 占位通道：protocol==1 且 vid/pid 全 0（无真实 UAS 设备背书）。
    def _is_virtual_uas(dev):
        try:
            return (dev.get("protocol") == "1"
                    and int(dev.get("vid", "0"), 0) == 0
                    and int(dev.get("pid", "0"), 0) == 0)
        except ValueError:
            return False

    incremented_any = False       # 至少一个设备全部 expect 字段严格增量
    all_virtual = True            # 所有设备是否均为虚拟 UAS 占位通道
    for dev in devices:
        minor = str(dev.get("minor", "?"))
        tag = f"minor={minor}"
        if not _is_virtual_uas(dev):
            all_virtual = False
        if minor not in base:
            errors.append(f"{tag}: 设备不在基线中（基线后新出现？重跑 baseline）")
            continue
        dev_incremented = True
        for f in expect_fields:
            if f not in dev:
                errors.append(f"{tag}: 缺 expect 字段 {f}")
                dev_incremented = False
                continue
            try:
                now = int(dev[f], 0)
            except ValueError:
                errors.append(f"{tag}: {f} 非数字: {dev[f]}")
                dev_incremented = False
                continue
            before = base[minor].get(f)
            if before is None:
                errors.append(f"{tag}: 基线缺字段 {f}")
                dev_incremented = False
                continue
            # 类型归一防御：基线值须为数字（load_baseline 已转 int，
            # 此处兜底 str 形态，非数字判红不崩）
            try:
                before = int(before, 0) if isinstance(before, str) else int(before)
            except (TypeError, ValueError):
                errors.append(f"{tag}: 基线 {f} 非数字: {before}")
                dev_incremented = False
                continue
            delta = now - before
            report.append(f"{tag}: {f} {before} -> {now} (delta={delta})")
            if delta <= 0:
                dev_incremented = False
        if dev_incremented:
            incremented_any = True

    if not incremented_any:
        if all_virtual:
            report.append("SKIP: 全部设备均为虚拟 UAS 占位通道（protocol==1 "
                          "且 vid/pid 全 0，无真实 UAS 流量源），无增量豁免")
        elif not errors:
            # 无结构错误且无任一设备增量：dd 触发未生效，判红（防假绿）。
            # 保留 "未增加" 子串以兼容既有红灯用例断言。
            errors.append("所有设备在 expect 字段上均未增加（delta<=0），"
                          "dd 触发未生效（无任一设备增量）")
    return errors, report


def _snapshot_numeric(devices):
    """按 minor 索引数值字段快照（perf 前后差分用）；缺数值字段返回 None。

    read_bytes/read_cmds/read_ns 为内核累计计数，perf 需前后两次 probe 差值
    才得 dd 负载窗口增量。字段缺失（数值非数字）返回 None 由调用方判红。
    """
    snap = {}
    for dev in devices:
        minor = str(dev.get("minor", "?"))
        snap[minor] = {}
        for f in ("read_bytes", "read_cmds", "read_ns", "write_bytes",
                  "write_cmds", "write_ns", "last_transport_latency_ns"):
            if f not in dev:
                return None
            try:
                snap[minor][f] = int(dev[f], 0)
            except ValueError:
                return None
    return snap


def mode_perf(args):
    """性能采集（dd 负载驱动真实 USB 传输，probe 前后差分；只报数不设门禁）。

    dd 读块设备 --load-mb MB（默认 64，与 lcview-perf 同负载，对齐性能基线）
    触发 read_bytes/read_cmds 增量 → 算：
    - per-IO ns = Δread_ns / Δread_cmds（内核每次读命令平均耗时，纳秒）
    - 吞吐 MB/s = Δread_bytes / dd_s / 1e6（字节吞吐，与 lcview 事件吞吐
      视角互补——lciod 内核计数直接观测 USBD 栈传输量）
    - 最近传输延迟 ns = last_transport_latency_ns（内核记录最近一次传输
      端到端耗时，dd 后快照）
    输出 human 可读行 + METRICS JSON 行（供 ws_report --metrics 结构化存档）。
    dd 有 IO 副作用，不并入 liveness，按需触发（同 lcview-perf）。
    """
    load_mb = args.load_mb or 64
    dev = args.block_dev or "/dev/block/sda"

    def _probe():
        devices = parse_probe_output(run_probe())
        # perf 前/后快照都须通过 stats 校验（enabled=1/字段齐全/abi），
        # 防"监控被禁用仍全绿"假绿污染性能基线
        errors = validate_devices(devices)
        if errors:
            for e in errors:
                print(f"FAIL: {e}")
            return None
        return devices

    before = _probe()
    if before is None:
        print("ERROR: dd 前 probe 快照校验未通过，拒绝采集")
        return 1
    snap0 = _snapshot_numeric(before)
    if snap0 is None:
        print("ERROR: dd 前 probe 快照缺数值字段，拒绝采集")
        return 1

    # 强刷读路径：丢弃 page cache 使 dd 真实下发 USB SCSI——否则重复读同一
    # 区域命中块设备缓冲，lciod 内核计数不增，perf delta 恒 0 假红。
    adb(["shell", "echo 3 > /proc/sys/vm/drop_caches"])

    # dd 读负载（host 侧单调钟计时；与 lcview perf 同语义，不含人工 sleep）
    t0 = time.monotonic()
    out, rc = adb(["shell",
                   f"dd if={dev} of=/dev/null bs=1M count={load_mb} 2>/dev/null"],
                  timeout=args.dd_timeout or 300)
    dd_s = time.monotonic() - t0
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: dd 负载执行失败 rc={rc}（块设备 {dev} 不可读？）")
        return 1
    if dd_s <= 0:
        print("ERROR: dd 计时非正（dd_s<=0），负载未执行或计时异常，判红防假基线")
        return 1

    after = _probe()
    if after is None:
        print("ERROR: dd 后 probe 快照校验未通过，拒绝采集")
        return 1
    snap1 = _snapshot_numeric(after)
    if snap1 is None:
        print("ERROR: dd 后 probe 快照缺数值字段，拒绝采集")
        return 1

    # 差分计算（多设备聚合语义：dd 只作用于一个块设备，其对应 usbd 通道
    # 才有增量；其余设备零增量属预期，跳过不入基线。无任一设备增量判红
    # ——dd 未触发传输即 perf 无意义，拒绝假基线。单设备场景退化为必须增量。）
    metrics_list, lines = [], []
    incremented_any = False
    for dev_i in after:
        minor = str(dev_i.get("minor", "?"))
        tag = f"minor={minor}"
        if minor not in snap0:
            print(f"ERROR: {tag} 不在 dd 前快照中（新出现？重跑）")
            return 1
        d = snap1[minor]
        b = snap0[minor]
        rd = d["read_bytes"] - b["read_bytes"]
        rc_delta = d["read_cmds"] - b["read_cmds"]
        rns = d["read_ns"] - b["read_ns"]
        if rd <= 0 or rc_delta <= 0:
            print(f"{tag}: dd 后 read_bytes/read_cmds 无增量（非本次 dd 目标"
                  f"通道，跳过）")
            continue
        incremented_any = True
        per_io_ns = (rns / rc_delta) if rc_delta > 0 else 0.0
        mbps = (rd / 1e6) / dd_s if dd_s > 0 else 0.0
        lat_ns = d["last_transport_latency_ns"]
        metrics_list.append({
            "minor": minor,
            "per_io_ns": round(per_io_ns, 1),
            "throughput_mbps": round(mbps, 3),
            "last_latency_ns": lat_ns,
            "read_bytes_delta": rd,
            "read_cmds_delta": rc_delta,
        })
        lines.append(
            f"{tag}: per-IO={per_io_ns:.1f} ns，吞吐={mbps:.3f} MB/s，"
            f"最近传输延迟={lat_ns} ns（Δ{rd}B/{rc_delta}cmds）")

    if not incremented_any:
        print("ERROR: 无任一设备 dd 后 read_bytes/read_cmds 增量（触发未生效，"
              "块设备未就绪？），拒绝出 perf 基线")
        return 1

    metrics = {
        "load_mb": load_mb,
        "dd_s": round(dd_s, 3),
        "devices": metrics_list,
    }
    for ln in lines:
        print(ln)
    print("METRICS " + json.dumps(metrics, ensure_ascii=False))
    return 0


# ============================================================
# R3 方向 7：风暴规则校验（--mode storm，用户态合成注入）
# ============================================================

# daemon 每 tick 读的用户态合成注入 sysprop（值 "stall:<n>" 或 "timeout:<n>"，
# 值变化时把 n 加进当前环形桶，幂等一次性，不落内核计数）
_FAULT_INJECT_PROP = "sys.lechao.lciod.fault_inject"
# storm 事件日志 tag（仿 link_monitor.cpp EmitLinkEventJson 手拼 JSON 行）
_STORM_LOG_TAG = "lechao_lciod_event"


def mode_storm(args):
    """风暴规则校验：setprop 合成注入 → sleep 等 daemon 10s tick + 规则三
    评估窗口 → logcat 断言 tag lechao_lciod_event 出现 "rule":"storm"。

    抓取窗口覆盖注入后：--log-since 显式给出时以它为锚；未给时注入前先取
    设备 epoch 作锚（logcat -T <epoch> 只抓锚点后缓冲，避免注入前历史行
    当新命中）。判红项（防假绿）：时钟不可读 / setprop 失败 / logcat 抓取
    失败 / 无 "rule":"storm" 行（规则三未触发）。
    """
    since = args.log_since
    if since is None:
        now = device_now()
        if now is None:
            print("ERROR: 无法读取设备时钟（logcat 抓取窗口无法锚定）")
            return 1
        since = now
    out, rc = adb(["shell", "setprop", _FAULT_INJECT_PROP, "stall:20"])
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: setprop {_FAULT_INJECT_PROP} 失败 rc={rc}")
        return 1
    print(f"已注入 {_FAULT_INJECT_PROP}=stall:20（用户态合成注入）")
    time.sleep(args.storm_wait or 15)
    out, rc = adb(["logcat", "-d", "-T", str(int(since)),
                   "-s", _STORM_LOG_TAG])
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: logcat 抓取失败 rc={rc}（tag {_STORM_LOG_TAG}）")
        return 1
    if '"rule":"storm"' not in out:
        print('ERROR: logcat 无 "rule":"storm" 行（规则三未触发，注入未生效？）')
        return 1
    print('OK: 风暴规则触发确认（logcat 命中 "rule":"storm"）')
    return 0


# ============================================================
# A 批二 方向 3/4：SD 用户态验收（--mode sd）
# ============================================================

# 内核 lciod_sd 暴露的 sysfs 目录（/sys/class/lciod_sd/lciod_sd/）
_SD_SYSFS_DIR = "/sys/class/lciod_sd/lciod_sd"
# daemon SD 健康 ALOG tag（每 10s 打 JSON 行；复用 storm 的 tag 常量）
_SD_LOG_TAG = _STORM_LOG_TAG
# lcview daemon 落盘 JSONL 目录（sd_health 事件 id=15，文件名含 sd_health）
_SD_LVVIEW_LOG_DIR = "/data/vendor/lechao_lcview/logs"
# sysfs 必须齐全的属性（缺任一即内核打点回归，判红）
_SD_REQUIRED_ATTRS = ["enable", "window_bytes", "window_requests",
                      "stuck_count", "last_speed", "ssr_speed_class",
                      "ssr_uhs_grade", "ssr_valid"]
# 仅允许 0/1 的布尔属性（其余属性校验非负十进制数字）
_SD_BOOL_ATTRS = ("enable", "ssr_valid")
# SD 产载读块设备变量（方向 2）：独立变量 LCIOD_SD_BLOCK_DEV（默认 SD 卡
# mmcblk0），不复用 LCVIEW_BLOCK_DEV——后者在验证环境全局导出时指向 USB 块
# 设备（sda），会让 SD 产载错读 USB 通道致产载错配。只读到 /dev/null 无破坏面。
_SD_BLOCK_DEV_ENV = "LCIOD_SD_BLOCK_DEV"
_SD_BLOCK_DEV_DEFAULT = "/dev/block/mmcblk0"


def _sd_block_dev():
    """SD 产载目标块设备：LCIOD_SD_BLOCK_DEV 覆盖，缺省 SD 卡 mmcblk0。"""
    return os.environ.get(_SD_BLOCK_DEV_ENV) or _SD_BLOCK_DEV_DEFAULT


def _check_sd_sysfs():
    """sysfs 断言（A 批二 方向 3）→ (错误列表, adb 超时 bool)。

    ls sysfs 目录须 rc==0 且输出含全部 _SD_REQUIRED_ATTRS；逐个 cat：rc==0
    且 strip 后为数字（int(x, 0) 非负），enable/ssr_valid 额外限 ∈{0,1}。
    任一缺失/不可读/非数字判红。adb 超时 rc==-1 置 timeout=True 由调用方
    透传（不得当"无属性"假绿）。
    """
    errors = []
    out, rc = adb(["shell", "ls", _SD_SYSFS_DIR])
    if rc == -1:
        return errors, True
    if rc != 0:
        return [f"sysfs 目录 {_SD_SYSFS_DIR} 不可读 rc={rc}"
                "（lciod_sd 内核模块未加载？）"], False
    names = set(out.split())
    for attr in _SD_REQUIRED_ATTRS:
        if attr not in names:
            errors.append(f"sysfs 缺属性 {attr}（{_SD_SYSFS_DIR}）")
            continue
        val, vrc = adb(["shell", "cat", f"{_SD_SYSFS_DIR}/{attr}"])
        if vrc == -1:
            return errors, True
        if vrc != 0:
            errors.append(f"sysfs 属性 {attr} 不可读 rc={vrc}")
            continue
        s = val.strip()
        try:
            n = int(s, 0)
        except ValueError:
            errors.append(f"sysfs 属性 {attr} 非数字: {s!r}")
            continue
        if n < 0:
            errors.append(f"sysfs 属性 {attr} 为负值: {s}")
        elif attr in _SD_BOOL_ATTRS and n not in (0, 1):
            errors.append(f"sysfs 属性 {attr}={s} 非 0/1")
    return errors, False


def _check_sd_health():
    """SD_HEALTH 事件断言（A 批二 方向 4）→ (错误列表, adb 超时 bool)。

    列出 lcview 日志目录中含 sd_health 的文件并读取内容，逐行 json.loads
    容错解析（失败行跳过计数），断言存在 "id":15 或 "name":"sd_health" 的
    JSONL 记录。目录不可读/无 sd_health 文件/无匹配记录均判红（防假绿）。
    设备端 glob 未展开时回退整目录 ls 过滤（逐文件 cat）。
    """
    # 直接 ls 目录取 basename 后拼全路径 cat：设备端 `sh -c <glob>` 经
    # adb 多参拼接会被拆成 `sh -c ls <path>`（-c 只吃第一个 token，glob
    # 与 2>/dev/null 沦为位置参数），导致 glob 不展开、回退又 cat 相对
    # basename 而失败（A 批二首跑 sd_health 读取 rc=1 即此因）。
    out, rc = adb(["shell", "ls", _SD_LVVIEW_LOG_DIR])
    if rc == -1:
        return [], True
    if rc != 0:
        return [f"lcview 日志目录 {_SD_LVVIEW_LOG_DIR} 不可读 rc={rc}"], False
    files = [f.split("/")[-1] for f in out.split()
             if "sd_health" in f and "*" not in f]
    if not files:
        return [f"lcview 日志目录无含 sd_health 的日志文件"
                f"（SD_HEALTH 事件未落盘？dir={_SD_LVVIEW_LOG_DIR}）"], False

    matched = 0
    total = 0
    parsed_bad = 0
    for fn in files:
        body, brc = adb(["shell", "cat", f"{_SD_LVVIEW_LOG_DIR}/{fn}"])
        if brc == -1:
            return [], True
        if brc != 0:
            return [f"读取 sd_health 日志文件失败 rc={brc}: {fn}"], False
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            total += 1
            try:
                rec = json.loads(line)
            except ValueError:
                parsed_bad += 1
                continue
            if not isinstance(rec, dict):
                parsed_bad += 1
                continue
            if rec.get("id") == 15 or rec.get("name") == "sd_health":
                matched += 1
    if matched == 0:
        return [f"sd_health 日志无 id=15/name=sd_health 记录"
                f"（{len(files)} 文件 {total} 行，解析失败 {parsed_bad} 行）"], False
    return [], False


def mode_sd(args):
    """SD 用户态验收（A 批二 方向 3/4）：sysfs + SD_HEALTH + sd JSON 三重断言。

    自驱 SD 产载（方向 2）：drop_caches + dd 读 LCIOD_SD_BLOCK_DEV（默认
    /dev/block/mmcblk0）→ /dev/null，令 window_bytes/sd_health/SD 事件有数据
    ——产载设备经独立变量解析，不复用 LCVIEW_BLOCK_DEV（环境全局导出时指向
    USB 设备会错配）；dd 只读到 /dev/null 无破坏面，失败仅告警不判红（无 SD
    卡时仍以只读 sysfs/logcat 断言为准）。
    判红项（防假绿）：sysfs 目录/属性缺失或不可读或非数字 / 无 sd_health
    日志文件 / 无 id=15 记录 / 设备时钟不可读 / logcat 无 "rule":"sd" 行。
    任一步 adb 超时（rc==-1）return -1 由 main 透传为失败。
    """
    blockdev = _sd_block_dev()
    adb(["shell", "echo 3 > /proc/sys/vm/drop_caches"])
    _, lrc = adb(["shell",
                  f"dd if={blockdev} of=/dev/null bs=1M count=4 2>/dev/null"],
                 timeout=args.dd_timeout)
    if lrc == -1:
        return -1  # adb 超时透传
    if lrc != 0:
        print(f"warn: SD 产载 dd 读 {blockdev} 失败 rc={lrc}"
              "（无 SD 卡？继续只读断言）")
    time.sleep(2)  # 等 SD 流量与 daemon 10s 上报窗口（对齐原 case hostcmd sleep 2）

    errors, timeout = _check_sd_sysfs()
    if timeout:
        return -1
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print("ERROR: lciod sd 校验失败（sysfs）")
        return 1
    print("OK: sysfs 属性齐全且数值合法")

    errors, timeout = _check_sd_health()
    if timeout:
        return -1
    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print("ERROR: lciod sd 校验失败（SD_HEALTH）")
        return 1
    print("OK: SD_HEALTH 事件存在（lcview sd_health 落盘）")

    since = args.log_since
    if since is None:
        now = device_now()
        if now is None:
            print("ERROR: 无法读取设备时钟（logcat 抓取窗口无法锚定）")
            return 1
        since = now
    # daemon 每 10s 一轮 sd JSON：以锚点起轮询最多 ~18s（3x6s），避免锚点
    # 落在上一轮之后、下一轮之前时误判"未上报"（防假红）。判红仍以窗口内
    # 命中 "rule":"sd" 为唯一依据（防假绿）。
    found = False
    out = ""
    for _ in range(4):
        out, rc = adb(["logcat", "-d", "-t", str(int(since)), "-s", _SD_LOG_TAG])
        if rc == -1:
            return -1
        if rc != 0:
            print(f"ERROR: logcat 抓取失败 rc={rc}（tag {_SD_LOG_TAG}）")
            return 1
        if '"rule":"sd"' in out:
            found = True
            break
        time.sleep(6)
    if not found:
        print('FAIL: logcat 无 "rule":"sd" 行（daemon sd 规则未上报？）')
        print("ERROR: lciod sd 校验失败（sd JSON）")
        return 1
    print('OK: logcat 命中 "rule":"sd" JSON')
    print("OK: lciod sd 校验通过（sysfs + SD_HEALTH + sd JSON）")
    return 0


# ============================================================
# SD 自愈演练闭环：合成 STUCK 注入 + 故障面断言（--mode sd-inject）
# ============================================================

# 演练入口属性（root-only，写一次触发一次；内存构造合成 STUCK，不触碰真卡）
_SD_DRILL_ATTR = "inject_stuck"
# 演练事件 JSONL 文件名关键字（16_sd_stuck_<YYYYMMDD>_pN.jsonl）
_SD_STUCK_JSONL_KEY = "sd_stuck"
# 内核留痕正文（lciod_sd.c pr_warn_ratelimited "kernel_lechao_usb: sd stuck:"）
_SD_STUCK_KMSG = "sd stuck"


def _sd_sysfs_read(attr):
    """读单个 SD sysfs 属性 → (值 int 或 None, rc)。

    rc==-1 adb 超时透传；rc!=0 不可读；rc==2 内容非十进制（值 None）。调用
    方据 rc 区分"不可读"与"非法值"，不得静默当 0（防假绿）。
    """
    val, rc = adb(["shell", "cat", f"{_SD_SYSFS_DIR}/{attr}"])
    if rc == -1:
        return None, -1
    if rc != 0:
        return None, rc
    try:
        return int(val.strip(), 0), 0
    except ValueError:
        return None, 2


def _read_sd_stuck_records():
    """读取 lcview 日志目录全部 sd_stuck(id=16) 记录 → (记录列表, 超时, 错误列表)。

    文件名为 16_sd_stuck_<YYYYMMDD>_pN.jsonl，按文件名排序取读；逐行
    json.loads 容错（失败行跳过），收集 rec["id"]==16 的记录。JSONL 记录
    无 name 字段，判据只能用 id（照 _check_sd_health 口径）。目录/文件不可读
    返回错误（不得当"无事件"假绿）。
    """
    out, rc = adb(["shell", "ls", _SD_LVVIEW_LOG_DIR])
    if rc == -1:
        return [], True, []
    if rc != 0:
        return [], False, [f"lcview 日志目录 {_SD_LVVIEW_LOG_DIR} 不可读 rc={rc}"]
    files = sorted(f.split("/")[-1] for f in out.split()
                   if _SD_STUCK_JSONL_KEY in f and "*" not in f)
    records = []
    for fn in files:
        body, brc = adb(["shell", "cat", f"{_SD_LVVIEW_LOG_DIR}/{fn}"])
        if brc == -1:
            return records, True, []
        if brc != 0:
            return records, False, [f"读取 sd_stuck 日志文件失败 rc={brc}: {fn}"]
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("id") == 16:
                records.append(rec)
    return records, False, []


def _max_sd_stuck_seq(records):
    """记录集内 sd_stuck 最大 seq（无记录返 -1）——增量判据用 seq 而非条数，
    防日志轮转/文件读取顺序致条数比较误判。"""
    return max((r.get("seq", -1) for r in records), default=-1)


def _sd_stuck_kmsg_count():
    """dmesg 中 "sd stuck" 出现次数；adb 超时返 -1，dmesg 不可读返 None。"""
    out, rc = adb(["shell", "dmesg"])
    if rc == -1:
        return -1
    if rc != 0:
        return None
    return sum(1 for ln in out.splitlines() if _SD_STUCK_KMSG in ln)


def _sd_stuck_logcat_trace(since):
    """内核 "sd stuck" logcat 留痕检测 → (命中 bool, adb超时 bool)。

    优先 logcat -b kernel -T <since>（本设备 ro.logd.kernel=true，内核 printk
    入 kernel 缓冲）；仅判锚点后的行，避免旧留痕假绿；adb 超时透传。
    """
    out, rc = adb(["logcat", "-d", "-b", "kernel", "-T", str(int(since))])
    if rc == -1:
        return False, True
    return (rc == 0 and _SD_STUCK_KMSG in out), False


def mode_sd_inject(args):
    """SD 自愈演练闭环校验（--mode sd-inject）。

    自驱向 inject_stuck 注入一次合成 STUCK（仅内存模拟，不依赖真卡异常），
    四重断言（防假绿判红项）：
      1) stuck_count 递增（注入前读基线）；
      2) lcview JSONL 新增 sd_stuck(id=16) 记录（seq 增量判据）；
      3) last_speed 反映降档（== 新增事件 new_speed 且 old_speed > new_speed）；
      4) 内核留痕 "sd stuck"（logcat -b kernel 锚点后命中，或 dmesg 计数增量）；
    任一步 adb 超时（rc==-1）return -1 由 main 透传为失败。
    """
    # 1) 注入前基线：stuck_count + last_speed + 已有 sd_stuck 记录 + dmesg 计数
    base_count, crc = _sd_sysfs_read("stuck_count")
    if crc == -1:
        return -1
    if base_count is None:
        print(f"ERROR: stuck_count 不可读 rc={crc}（lciod_sd 未加载？）")
        return 1
    base_speed, src = _sd_sysfs_read("last_speed")
    if src == -1:
        return -1
    if base_speed is None:
        print(f"ERROR: last_speed 不可读 rc={src}（演练属性缺失？）")
        return 1
    recs_before, timeout, rerrs = _read_sd_stuck_records()
    if timeout:
        return -1
    if rerrs:
        for e in rerrs:
            print(f"FAIL: {e}")
        print("ERROR: lciod sd-inject 校验失败（读取 sd_stuck 基线）")
        return 1
    base_seq = _max_sd_stuck_seq(recs_before)
    base_kmsg = _sd_stuck_kmsg_count()
    if base_kmsg == -1:
        return -1

    # 2) 注入前取设备时钟锚点（logcat -T 只判锚点后缓冲，防旧留痕假绿）
    since = args.log_since
    if since is None:
        since = device_now()
        if since is None:
            print("ERROR: 无法读取设备时钟（logcat 抓取窗口无法锚定）")
            return 1

    # 3) 注入一次合成 STUCK（root-only 触发属性）
    out, rc = adb(["shell", f"echo 1 > {_SD_SYSFS_DIR}/{_SD_DRILL_ATTR}"])
    if rc == -1:
        return -1
    if rc != 0:
        print(f"ERROR: 注入 {_SD_DRILL_ATTR} 失败 rc={rc}"
              "（root-only 触发属性不可写？）")
        return 1

    # 4) 轮询等待：计数递增 + 事件经 daemon 消费落盘（最多 ~12s）
    after_count = base_count
    recs_after = recs_before
    for _ in range(6):
        time.sleep(2)
        c, c2 = _sd_sysfs_read("stuck_count")
        if c2 == -1:
            return -1
        if c is not None:
            after_count = c
        r, t, _ = _read_sd_stuck_records()
        if t:
            return -1
        recs_after = r
        if after_count > base_count and _max_sd_stuck_seq(r) > base_seq:
            break

    # 5) 断言：计数递增 + 新增事件降档 + last_speed 反映
    errors = []
    if after_count <= base_count:
        errors.append(f"stuck_count 未递增（{base_count}→{after_count}），注入未生效")
    new_recs = [r for r in recs_after if r.get("seq", -1) > base_seq]
    if not new_recs:
        errors.append(f"lcview JSONL 无新增 sd_stuck(id=16) 记录"
                      f"（基线 seq={base_seq}）")
    else:
        evt = max(new_recs, key=lambda r: r.get("seq", -1))
        fields = evt.get("f") or []
        if (len(fields) < 2 or not isinstance(fields[0], int)
                or not isinstance(fields[1], int)):
            errors.append(f"sd_stuck 记录字段非法（f={fields!r}）")
        else:
            old_speed, new_speed = fields[0], fields[1]
            if old_speed <= new_speed:
                errors.append(f"sd_stuck 未反映降档（old={old_speed} "
                              f"new={new_speed}）")
            after_speed, s2 = _sd_sysfs_read("last_speed")
            if s2 == -1:
                return -1
            if after_speed is None:
                errors.append("last_speed 注入后不可读")
            elif after_speed != new_speed:
                errors.append(f"last_speed={after_speed} 与事件 new_speed="
                              f"{new_speed} 不符（未反映降档）")

    # 6) 内核留痕：logcat -b kernel 锚点后命中，或 dmesg 计数增量
    trace, t = _sd_stuck_logcat_trace(since)
    if t:
        return -1
    if not trace:
        after_kmsg = _sd_stuck_kmsg_count()
        if after_kmsg == -1:
            return -1
        if (after_kmsg is not None and base_kmsg is not None
                and after_kmsg > base_kmsg):
            trace = True
    if not trace:
        errors.append('无内核留痕（logcat -b kernel -T 与 dmesg 均无 "sd stuck"）')

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print("ERROR: lciod sd-inject 校验失败")
        return 1
    print(f"OK: stuck_count {base_count}→{after_count}，新增 sd_stuck "
          f"{len(new_recs)} 条，last_speed 反映降档，内核留痕")
    print("OK: lciod sd-inject 校验通过（合成 STUCK 演练闭环）")
    return 0


# ============================================================
# QoS 延迟感知自动分级闭环校验（--mode qos-auto）
# ============================================================

# 自动档开关与延迟阈值 sysprop（与 io_qos.cpp 对齐）：
#   ioqos_auto 开关（"1" 开 / 空或 "0" 关）；ioqos_lat_ms 延迟阈值（毫秒）
_QOS_AUTO_PROP = "sys.lechao.lciod.ioqos_auto"
_QOS_LAT_PROP = "sys.lechao.lciod.ioqos_lat_ms"
# 延迟演练入口属性（root-only，写一次触发一次；合成慢 COMPLETE 入窗口/直方图）
_SD_LAT_DRILL_ATTR = "inject_latency"
# qos_auto 事件 JSONL 文件名关键字（18_qos_auto_adjust_<YYYYMMDD>_pN.jsonl）
_QOS_AUTO_JSONL_KEY = "qos_auto_adjust"
# daemon 自动档 JSON 留痕 rule 值（logcat tag lechao_lciod_event）
_QOS_AUTO_RULE = '"rule":"qos_auto"'
# 延迟阈值缺省（毫秒）：与 daemon kIoQosAutoLatThresholdMs 对齐
_QOS_AUTO_LAT_MS_DEFAULT = 50


def _read_qos_auto_records():
    """读取 lcview 日志目录全部 qos_auto_adjust(id=18) 记录。

    返回 (记录列表, 超时 bool, 错误列表)。文件名形如
    18_qos_auto_adjust_<YYYYMMDD>_pN.jsonl，按名排序取读；逐行 json.loads
    容错（失败行跳过），收集 rec["id"]==18 的记录（JSONL 无 name 字段，
    判据只能用 id，照 sd_stuck 口径）。目录/文件不可读返回错误（不得当
    "无事件"假绿）。
    """
    out, rc = adb(["shell", "ls", _SD_LVVIEW_LOG_DIR])
    if rc == -1:
        return [], True, []
    if rc != 0:
        return [], False, [f"lcview 日志目录 {_SD_LVVIEW_LOG_DIR} 不可读 rc={rc}"]
    files = sorted(f.split("/")[-1] for f in out.split()
                   if _QOS_AUTO_JSONL_KEY in f and "*" not in f)
    records = []
    for fn in files:
        body, brc = adb(["shell", "cat", f"{_SD_LVVIEW_LOG_DIR}/{fn}"])
        if brc == -1:
            return records, True, []
        if brc != 0:
            return records, False, [f"读取 qos_auto 日志文件失败 rc={brc}: {fn}"]
        for line in body.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("id") == 18:
                records.append(rec)
    return records, False, []


def _max_qos_auto_seq(records):
    """记录集内 qos_auto 最大 seq（无记录返 -1）——增量判据用 seq 而非条数，
    防日志轮转/文件读取顺序致条数比较误判。"""
    return max((r.get("seq", -1) for r in records), default=-1)


def _qos_auto_logcat_trace(since):
    """daemon "rule":"qos_auto" logcat 留痕检测 → (命中 bool, 超时 bool)。

    抓 tag lechao_lciod_event（IoQosManager 自动档档位变化经 EVENT_ALOG 打
    JSON 行）；只判锚点后的行，避免旧留痕假绿；adb 超时透传。
    """
    out, rc = adb(["logcat", "-d", "-T", str(int(since)), "-s", _SD_LOG_TAG])
    if rc == -1:
        return False, True
    return (rc == 0 and _QOS_AUTO_RULE in out), False


def _qos_limit_contains_bg(paths):
    """读限速文件判断当前是否处于 bg 收紧态 → (state, 超时 bool)。

    state ∈ {"bg","off","unreadable"}：含 bg 限速值（8388608）为 "bg"；off
    档（内核移除/清空限速条目）读回空/0/max 均属合法"放松"态为 "off"；
    文件不可读为 "unreadable"（调用方判红，不得当放松假绿）。
    """
    out, rc = adb(["shell", "cat", paths["limit_file"]])
    if rc == -1:
        return "unreadable", True
    if rc != 0:
        return "unreadable", False
    if _QOS_BG_BYTES in out:
        return "bg", False
    return "off", False


def mode_qos_auto(args):
    """QoS 延迟感知自动分级闭环校验（--mode qos-auto）。

    自驱向 inject_latency 注入慢 COMPLETE（仅内存模拟，不依赖真卡异常）
    抬升 SD 窗口延迟 → daemon IoQosManager 自动档据反馈收紧 bg 限速；停止
    注入待 10s 窗回落 → 断言放松。四重断言（防假绿判红项）：
      1) setprop ioqos_auto=1 + 阈值后注入抬延迟 → 限速文件进入 bg
         （含 8388608，自动档收紧）；
      2) lcview JSONL 新增 qos_auto_adjust(id=18) 记录（seq 增量判据）；
      3) logcat tag lechao_lciod_event 新增 "rule":"qos_auto" 行（档位变化
         留痕）；
      4) 停止注入待窗口回落 → 限速文件退出 bg（自动档放松）。
    产载沿临时文件/内存注入模式，禁块设备裸写（inject_latency 仅内存合成
    慢 COMPLETE，无任何块设备读写）。teardown（finally 兜底）：关自动档 +
    清延迟阈值。任一步 adb 超时（rc==-1）return -1 由 main 透传为失败。
    """
    paths = _qos_cgroup_paths()
    if paths is None:
        print("ERROR: 无法探测 cgroup（v2 cgroup.controllers 无 io 且 "
              "v1 blkio 目录缺失），qos-auto 校验无法进行")
        return 1
    out, rc = adb(["shell", "ls", "-d", paths["group_dir"]])
    if rc != 0 or not out.strip():
        print(f"ERROR: cgroup 组 {paths['group_dir']} 不存在"
              "（daemon IoQosManager 未建组？）")
        return 1

    # 基线：qos_auto 事件 seq + 设备时钟锚点（logcat -T 只判锚点后缓冲）
    recs_before, t, rerrs = _read_qos_auto_records()
    if t:
        return -1
    if rerrs:
        for e in rerrs:
            print(f"FAIL: {e}")
        print("ERROR: lciod qos-auto 校验失败（读取 qos_auto 基线）")
        return 1
    base_seq = _max_qos_auto_seq(recs_before)

    since = args.log_since
    if since is None:
        since = device_now()
        if since is None:
            print("ERROR: 无法读取设备时钟（logcat 抓取窗口无法锚定）")
            return 1

    try:
        # 打开自动档 + 阈值（阈值约束平均延迟分支；长尾分支独立触发）
        for prop, val in ((_QOS_LAT_PROP, str(args.qos_auto_lat_ms)),
                          (_QOS_AUTO_PROP, "1")):
            _out, prc = adb(["shell", "setprop", prop, val])
            if prc == -1:
                return -1
            if prc != 0:
                print(f"ERROR: setprop {prop}={val} 失败 rc={prc}")
                return 1

        # 注入抬延迟 → 轮询等待：daemon 收紧 bg + id=18 + logcat 留痕。
        # 每轮重复注入防 10s 窗到期清零后延迟回落致误判放松。
        bg_ok = False
        new_seq = base_seq
        trace_ok = False
        for _ in range(args.qos_auto_rounds):
            out, rc = adb(["shell",
                           f"echo 1 > {_SD_SYSFS_DIR}/{_SD_LAT_DRILL_ATTR}"])
            if rc == -1:
                return -1
            if rc != 0:
                print(f"ERROR: 注入 {_SD_LAT_DRILL_ATTR} 失败 rc={rc}"
                      "（root-only 触发属性不可写？）")
                return 1
            time.sleep(args.qos_auto_sleep)
            state, st = _qos_limit_contains_bg(paths)
            if st:
                return -1
            if state == "unreadable":
                print(f"ERROR: 限速文件 {paths['limit_file']} 不可读")
                return 1
            recs, rt, _ = _read_qos_auto_records()
            if rt:
                return -1
            new_seq = max(new_seq, _max_qos_auto_seq(recs))
            tr, tt = _qos_auto_logcat_trace(since)
            if tt:
                return -1
            bg_ok = (state == "bg")
            trace_ok = trace_ok or tr
            if bg_ok and new_seq > base_seq and trace_ok:
                break

        errors = []
        if not bg_ok:
            errors.append("注入抬延迟后限速文件未进入 bg（自动档未收紧）")
        if new_seq <= base_seq:
            errors.append(f"lcview JSONL 无新增 qos_auto_adjust(id=18) 记录"
                          f"（基线 seq={base_seq}）")
        if not trace_ok:
            errors.append('logcat 无 "rule":"qos_auto" 行（自动档留痕缺失）')
        if errors:
            for e in errors:
                print(f"FAIL: {e}")
            print("ERROR: lciod qos-auto 校验失败（收紧阶段）")
            return 1
        print(f"OK: 自动档收紧 bg（窗口延迟超阈），新增 qos_auto_adjust "
              f"(seq {base_seq}→{new_seq})，logcat 留痕")

        # 停止注入 → 待 10s 窗回落（窗口清零后无长尾）→ 断言放松
        relaxed = False
        for _ in range(args.qos_auto_relax_rounds):
            time.sleep(args.qos_auto_relax_sleep)
            state, st = _qos_limit_contains_bg(paths)
            if st:
                return -1
            if state == "unreadable":
                print(f"ERROR: 限速文件 {paths['limit_file']} 不可读")
                return 1
            if state != "bg":
                relaxed = True
                break
        if not relaxed:
            print("FAIL: 停止注入后限速文件仍为 bg（延迟回落未放松）")
            print("ERROR: lciod qos-auto 校验失败（放松阶段）")
            return 1
        print("OK: 延迟回落后自动档放松（限速文件退出 bg）")
        print("OK: lciod qos-auto 校验通过（延迟感知自动分级闭环）")
        return 0
    finally:
        # teardown：关自动档 + 清延迟阈值（回落手动 ioqos_level 常规驱动）
        adb(["shell", "setprop", _QOS_AUTO_PROP, ""])
        adb(["shell", "setprop", _QOS_LAT_PROP, ""])


# ============================================================
# C 批二 方向 3：USB 传输延迟/块大小二维直方图用户态验收（--mode histo）
# ============================================================


def run_histo_probe():
    """设备侧执行 lciod_probe --histo（GET_LATENCY_HISTOGRAM ioctl 取数），
    返回 stdout。失败/超时按语义退出——节点打不开或 ioctl 失败不得当
    "无输出"假绿（对齐 run_link_probe/run_probe 的 adb 超时透传与退出码语义）。
    """
    out, rc = adb(["shell", "lciod_probe", "--histo"])
    if rc == -1:
        print("ERROR: adb 执行 lciod_probe --histo 超时")
        sys.exit(2)
    if rc != 0:
        print(f"ERROR: lciod_probe --histo 退出码 {rc}（open/ioctl 失败）")
        sys.exit(1)
    return out


def parse_histo_output(text):
    """解析 lciod_probe --histo 输出（每设备一行 key=value）→
    {minor_str: {"minor","dirs","size_bins","latency_bins","mono_ts_ns",
    "size_bounds","latency_bounds","counts","abi_version"}}。

    仅识别同时含 minor 与 counts 的行（跳过空行/非直方图噪声）。某行含
    minor 但缺必需字段或数组元素非十进制时抛 ValueError——残缺数据不得
    静默当有效快照（防假绿），由调用方转 exit 1。
    """
    out = {}
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        fields = {}
        for m in _TOKEN_RE.finditer(line):
            fields[m.group(1)] = (m.group(2) if m.group(2) is not None
                                  else m.group(3))
        if "minor" not in fields or "counts" not in fields:
            continue
        minor = fields["minor"]
        try:
            int(minor, 0)
        except ValueError:
            raise ValueError(f"histo 行 {ln} minor 非整数: {minor!r}")
        rec = {"minor": minor}
        for f in ("dirs", "size_bins", "latency_bins", "mono_ts_ns",
                  "abi_version"):
            if f not in fields:
                raise ValueError(f"histo 行 {ln}(minor={minor}) 缺字段 {f}")
            try:
                rec[f] = int(fields[f], 0)
            except ValueError:
                raise ValueError(
                    f"histo 行 {ln}(minor={minor}) {f} 非整数: {fields[f]!r}")
        for f in ("size_bounds", "latency_bounds", "counts"):
            if f not in fields:
                raise ValueError(
                    f"histo 行 {ln}(minor={minor}) 缺数组字段 {f}")
            try:
                rec[f] = [int(x, 0) for x in fields[f].split(",") if x != ""]
            except ValueError:
                raise ValueError(f"histo 行 {ln}(minor={minor}) {f} 含非整数"
                                 f"元素: {fields[f][:80]!r}")
        out[str(minor)] = rec
    return out


def validate_histo(dev):
    """直方图结构断言 → 错误列表（空 = 通过）。

    判红项：维度字段不符（dirs/size_bins/latency_bins）/ abi_version 漂移 /
    counts 单元数 != 192 / size_bounds·latency_bounds 长度或取值与内核
    编译期档位不符 / 档位上界非严格递增或末档非饱和（2^64-1）/ counts 含负值。
    """
    errors = []
    tag = f"minor={dev.get('minor', '?')}"
    if dev.get("dirs") != _HISTO_DIRS:
        errors.append(f"{tag}: dirs={dev.get('dirs')} != {_HISTO_DIRS}")
    if dev.get("size_bins") != _HISTO_SIZE_BINS:
        errors.append(f"{tag}: size_bins={dev.get('size_bins')} "
                      f"!= {_HISTO_SIZE_BINS}")
    if dev.get("latency_bins") != _HISTO_LAT_BINS:
        errors.append(f"{tag}: latency_bins={dev.get('latency_bins')} "
                      f"!= {_HISTO_LAT_BINS}")
    if dev.get("abi_version") != int(_EXPECTED_ABI):
        errors.append(f"{tag}: abi_version={dev.get('abi_version')} "
                      f"!= {_EXPECTED_ABI}")
    counts = dev.get("counts")
    if not isinstance(counts, list) or len(counts) != _HISTO_COUNT_CELLS:
        errors.append(f"{tag}: counts 长度="
                      f"{len(counts) if isinstance(counts, list) else counts}"
                      f" != {_HISTO_COUNT_CELLS}")
    elif any(c < 0 for c in counts):
        errors.append(f"{tag}: counts 含负值")
    sb = dev.get("size_bounds")
    if not isinstance(sb, list) or tuple(sb) != _HISTO_SIZE_BOUNDS:
        errors.append(f"{tag}: size_bounds 与内核档位不符: {sb}")
    lb = dev.get("latency_bounds")
    if not isinstance(lb, list) or tuple(lb) != _HISTO_LAT_BOUNDS:
        errors.append(f"{tag}: latency_bounds 与内核档位不符: {lb}")
    for name, bounds in (("size_bounds", sb), ("latency_bounds", lb)):
        if not isinstance(bounds, list) or len(bounds) < 2:
            continue
        if any(bounds[i] <= bounds[i - 1] for i in range(1, len(bounds))):
            errors.append(f"{tag}: {name} 非严格递增: {bounds}")
        elif bounds[-1] != 2 ** 64 - 1:
            errors.append(f"{tag}: {name} 末档非饱和: {bounds[-1]}")
    return errors


def mode_histo(args):
    """USB 传输延迟/块大小二维直方图用户态验收（C 批二 方向 3）。

    语义：lciod_probe --histo 取数（GET_LATENCY_HISTOGRAM cmd5）→ 结构断言
    （dirs=2/size_bins=8/latency_bins=12、counts 长度 192、size/latency 档位
    上界严格递增且末档饱和、abi_version=5）→ drop_caches + dd 读块设备
    4MB（LCVIEW_BLOCK_DEV 覆盖，默认 /dev/block/sda USB 通道，只读到
    /dev/null 无破坏面）→ 差分断言 ≥1 设备读方向（dir 0）计数之和严格增量。
    无任一读方向增量即判红（防假绿）。返回 0 通过 / 1 判红 / -1 adb 超时。
    """
    snap0 = parse_histo_output(run_histo_probe())
    if not snap0:
        print("ERROR: 无设备直方图快照（probe 输出为空，内核驱动/HAL 未就绪？）")
        return 1
    all_errors = []
    for rec in snap0.values():
        all_errors += validate_histo(rec)
    if all_errors:
        for e in all_errors:
            print(f"FAIL: {e}")
        print("ERROR: lciod histo 校验失败（结构断言）")
        return 1
    print(f"OK: 直方图结构断言通过（{len(snap0)} 设备）")

    blockdev = os.environ.get("LCVIEW_BLOCK_DEV", "/dev/block/sda")
    # 强刷读路径：丢弃 page cache 使 dd 真实下发 USB SCSI（对齐 mode_perf）
    adb(["shell", "echo 3 > /proc/sys/vm/drop_caches"])
    out, rc = adb(["shell",
                   f"dd if={blockdev} of=/dev/null bs=1M count=4 2>/dev/null"])
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: dd 读 {blockdev} 失败 rc={rc}（块设备不可读？）")
        return 1

    snap1 = parse_histo_output(run_histo_probe())
    incremented_any = False
    for minor, rec0 in snap0.items():
        if minor not in snap1:
            continue
        c0 = sum(rec0["counts"][:_HISTO_READ_CELLS])
        c1 = sum(snap1[minor]["counts"][:_HISTO_READ_CELLS])
        delta = c1 - c0
        if delta > 0:
            incremented_any = True
            print(f"OK: minor={minor} 读方向直方图计数增量 {delta}"
                  f"（{c0} -> {c1}）")
        else:
            print(f"minor={minor}: 读方向计数无增量（delta={delta}，"
                  "非 dd 目标通道）")
    if not incremented_any:
        print("FAIL: 无任一设备读方向直方图计数增量（dd 触发未生效？）")
        print("ERROR: lciod histo 校验失败（差分断言，防假绿）")
        return 1
    print("OK: lciod histo 校验通过（结构断言 + 读方向增量）")
    return 0


# ============================================================
# R4 方向 6：SD 卡写 QoS 限速校验（--mode qos）
# ============================================================

# daemon IoQosManager 读写的 sysprop（与 io_qos.cpp 对齐）：
#   ioqos_level 档位值 "top"/"fg"/"bg"/"off"（空视为 off）；ioqos_dev 覆盖
#   QoS 目标设备（如 "179:0"），缺省 daemon 探测 /sys/block/mmcblk0/dev
_QOS_LEVEL_PROP = "sys.lechao.lciod.ioqos_level"
_QOS_DEV_PROP = "sys.lechao.lciod.ioqos_dev"
# lechao_bg 组名 + cgroup 挂载根：v2 组在 <root>/lechao_bg（io.max 限速），
# v1 组在 <root>/blkio/lechao_bg（blkio.throttle.write_bps_device 限速）
_QOS_GROUP_NAME = "lechao_bg"
_QOS_CGROUP_ROOT = "/sys/fs/cgroup"
# bg 档限速值（IoQosManager LevelToBytesPerSec：kBg=8MiB/s=8388608），
# setprop bg 后 cat 限速文件须含该值（v2 "wbps=8388608" / v1 "179:0 8388608"）
_QOS_BG_BYTES = "8388608"
# 组内 dd 写速率上限容差（bg 8MiB/s 留 1.5x 余量，允许限速引擎启动瞬态）
_QOS_IN_GROUP_MAX_MIB = 12.0
# 组外速率须显著高于：≥ 1.5x 组内 或 ≥ 20MiB/s（SD 卡写不受限的合理下限）
_QOS_OUT_MIN_MIB = 20.0
_QOS_OUT_MIN_RATIO = 1.5


def _parse_dd_speed_mib(out):
    """解析 dd 输出速率 → MiB/s 浮点；解析失败返回 None。

    dd（toybox）stderr 格式："N bytes (N MB) copied, T s, S MB/s"。
    以 bytes/T 复算（对 MB/s/MiB/s/GB/s 单位差异鲁棒，不依赖末尾单位）。
    """
    m = re.search(r"(\d+)\s+bytes.*copied,\s+([\d.]+)\s+s", out)
    if not m:
        return None
    try:
        return int(m.group(1)) / 1048576.0 / float(m.group(2))
    except (ValueError, ZeroDivisionError):
        return None


def _qos_cgroup_paths():
    """探测 cgroup 版本并返回组限速文件/迁移路径字典；均不命中返回 None。

    v2 判据：<root>/cgroup.controllers 存在且含 io（io.max 可用）；
    v1 判据：<root>/blkio 目录存在 或 真机 /dev/blkio（Android v1 blkio
            挂载点，RPi5 实测；root=/sys/fs/cgroup 时无 <root>/blkio）。
    返回 {"version", "limit_file", "group_dir", "group_procs", "root_procs"}；
    None 表示设备 cgroup 未启用 io 管控，调用方判红（防假绿）。
    """
    out, rc = adb(["shell", "cat", f"{_QOS_CGROUP_ROOT}/cgroup.controllers"])
    if rc == 0 and "io" in out.split():
        return {
            "version": "v2",
            "limit_file": f"{_QOS_CGROUP_ROOT}/{_QOS_GROUP_NAME}/io.max",
            "group_dir": f"{_QOS_CGROUP_ROOT}/{_QOS_GROUP_NAME}",
            "group_procs": f"{_QOS_CGROUP_ROOT}/{_QOS_GROUP_NAME}/cgroup.procs",
            "root_procs": f"{_QOS_CGROUP_ROOT}/cgroup.procs",
        }
    blkio_root = None
    out, rc = adb(["shell", "ls", "-d", f"{_QOS_CGROUP_ROOT}/blkio"])
    if rc == 0 and out.strip():
        blkio_root = f"{_QOS_CGROUP_ROOT}/blkio"
    else:
        out, rc = adb(["shell", "ls", "-d", "/dev/blkio"])
        if rc == 0 and out.strip():
            blkio_root = "/dev/blkio"
    if blkio_root:
        return {
            "version": "v1",
            "limit_file": f"{blkio_root}/{_QOS_GROUP_NAME}/"
                          "blkio.throttle.write_bps_device",
            "group_dir": f"{blkio_root}/{_QOS_GROUP_NAME}",
            "group_procs": f"{blkio_root}/{_QOS_GROUP_NAME}/tasks",
            "root_procs": f"{blkio_root}/tasks",
        }
    return None


def _run_qos_dd(path, mb, group_procs, root_procs, timeout):
    """设备侧 dd 写 --qos-dd-mb 到临时文件并返回速率 MiB/s。

    group_procs/root_procs 非 None（组内）时：设备 shell 自迁 pid（$$）入
    lechao_bg 组（adb root 直写 cgroup.procs/tasks）→ dd 写临时文件 →
    自迁回根组（进程随退出离组，组不留持久态）；None（组外）时在默认
    cgroup 跑（不受限基准）。dd 输出（2>&1 合并 stderr）经 _parse_dd_speed_mib
    复算速率。返回：float 速率 / -1（adb 超时透传）/ None（dd 失败或速率
    解析失败，判红防假绿）。
    """
    # conv=fsync 强制同步写回：裸 dd 写文件靠 page cache（实测 >1GB/s），
    # 写回由内核 kthread（root cgroup）提交，不计入 lechao_bg → blk-throttle
    # 失效（组内不压制假红）。fsync 令 bio 在调用进程上下文提交，blkio 组
    # 限速才生效（实测组内 8MiB/s / 组外 48MiB/s）。
    dd_cmd = f"dd if=/dev/zero of={path} bs=1M count={mb} conv=fsync 2>&1"
    if group_procs:
        # 自迁入组须先成功（写 cgroup.procs 失败即整段判红，防假压制）；
        # dd 结束后自迁回根组，shell 退出后进程即离组
        shell_cmd = (f'echo $$ > {group_procs} || {{ echo "MIGRATE_FAIL"; '
                     f'exit 1; }}; {dd_cmd}; rc=$?; echo $$ > {root_procs}; '
                     f"exit $rc")
    else:
        shell_cmd = dd_cmd
    # 单参下发整段复合命令：adb 多参拼接（shell sh -c <cmd>）不保留引号，
    # 设备外层 shell 会把 >/;/|| 重新解析（echo $$ > tasks 退化成写非法内容
    # 致 MIGRATE_FAIL，rc=1）——同 748 行既有认知，须作为单个 shell 参数
    # 原样下发由设备 shell 解析。
    out, rc = adb(["shell", shell_cmd], timeout=timeout)
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: dd 写 {path} 失败 rc={rc}: {out.strip()[:200]}")
        return None
    speed = _parse_dd_speed_mib(out)
    if speed is None:
        print(f"ERROR: dd 速率解析失败（输出无 bytes/copied 段）: "
              f"{out.strip()[:200]!r}")
        return None
    return speed


def mode_qos(args):
    """SD 卡写 QoS 限速校验（R4 方向 4/6，依赖 daemon IoQosManager 建组）。

    前置（adb root 由 main ensure_connected 承担）：lechao_bg 组存在且
    限速文件可读（off 档内核移除限速条目致读回为空，属合法态不判红）；
    setprop ioqos_level=bg → sleep 2（等 daemon 1s 周期应用）→ cat 限速
    文件断言含 8388608（v2 io.max / v1 blkio 限速值）。
    组内压制：设备 shell 自迁 pid（$$）入组，dd 写 /data/local/tmp 临时
    文件 --qos-dd-mb MB → 复算速率断言 ≤ --qos-in-max-mib（默认 12，
    bg 8MiB/s 留 1.5x 容差）。
    组外不受限：默认 cgroup 的 dd 写同大小临时文件 → 断言速率 ≥
    max(--qos-out-min-mib 20, 1.5x 组内)。
    teardown（finally 兜底）：删临时文件 + setprop ioqos_level 清空。
    无破坏性写守卫依赖（临时文件在 /data/local/tmp 安全路径，非裸块写）。
    判红项（防假绿）：组不存在 / 限速文件空 / setprop 失败 / 限速值未
    生效 / dd 失败或速率解析失败 / 组内未压制 / 组外未显著高于。
    """
    paths = _qos_cgroup_paths()
    if paths is None:
        print("ERROR: 无法探测 cgroup（v2 cgroup.controllers 无 io 且 "
              "v1 blkio 目录缺失），QoS 校验无法进行")
        return 1
    out, rc = adb(["shell", "ls", "-d", paths["group_dir"]])
    if rc != 0 or not out.strip():
        print(f"ERROR: cgroup 组 {paths['group_dir']} 不存在"
              "（daemon IoQosManager 未建组？）")
        return 1
    out, rc = adb(["shell", "cat", paths["limit_file"]])
    if rc != 0:
        print(f"ERROR: 限速文件 {paths['limit_file']} 不可读"
              "（cgroup 未配置限速？）")
        return 1
    # off 档（ioqos_level 空/off）时内核会移除限速条目，限速文件读回为空属
    # 合法态（真机 v1 /dev/blkio 实测：写 0 即移除条目，读回空字符串）。
    # 不得因起点为空判红（假红）——bg 生效断言在下文 setprop 之后，组存在性
    # 已由上方 group_dir 检查覆盖。
    print(f"cgroup 版本 {paths['version']}，限速文件当前值: {out.strip()!r}"
          "（off 档可为空）")

    out, rc = adb(["shell", "setprop", _QOS_LEVEL_PROP, "bg"])
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: setprop {_QOS_LEVEL_PROP}=bg 失败 rc={rc}")
        return 1
    time.sleep(args.qos_sleep or 2)
    out, rc = adb(["shell", "cat", paths["limit_file"]])
    if rc != 0 or _QOS_BG_BYTES not in out:
        print(f"ERROR: setprop bg 后限速文件无 {_QOS_BG_BYTES}"
              f"（daemon 1s 周期未应用？当前: {out.strip()!r}）")
        return 1
    print(f"OK: bg 限速生效（限速文件含 {_QOS_BG_BYTES}）")

    in_file = f"/data/local/tmp/lciod_qos_in_{os.getpid()}"
    out_file = f"/data/local/tmp/lciod_qos_out_{os.getpid()}"
    try:
        timeout = args.dd_timeout or 300
        in_speed = _run_qos_dd(in_file, args.qos_dd_mb, paths["group_procs"],
                               paths["root_procs"], timeout)
        if in_speed == -1:
            return -1  # adb 超时透传
        if in_speed is None:
            return 1
        if in_speed > args.qos_in_max_mib:
            print(f"FAIL: 组内 dd 写速率 {in_speed:.2f} MiB/s > 上限容差 "
                  f"{args.qos_in_max_mib:.0f} MiB/s（bg 8MiB/s 未压制）")
            return 1
        print(f"OK: 组内 dd 写被压制 {in_speed:.2f} MiB/s"
              f"（≤ {args.qos_in_max_mib:.0f}）")

        out_speed = _run_qos_dd(out_file, args.qos_dd_mb, None, None, timeout)
        if out_speed == -1:
            return -1  # adb 超时透传
        if out_speed is None:
            return 1
        threshold = max(args.qos_out_min_mib, in_speed * _QOS_OUT_MIN_RATIO)
        if out_speed < threshold:
            print(f"FAIL: 组外 dd 写速率 {out_speed:.2f} MiB/s < "
                  f"{threshold:.2f} MiB/s（组外未显著高于组内 {in_speed:.2f}）")
            return 1
        print(f"OK: 组外不受限 {out_speed:.2f} MiB/s（≥ {threshold:.2f}）")
        print(f"OK: SD 卡写 QoS 限速校验通过（组内 {in_speed:.2f} / "
              f"组外 {out_speed:.2f} MiB/s，{paths['version']}）")
        return 0
    finally:
        # teardown：删临时文件（安全路径）+ setprop 清空 ioqos_level
        adb(["shell", f"rm -f {in_file} {out_file}"])
        adb(["shell", "setprop", _QOS_LEVEL_PROP, ""])


def main():
    ap = argparse.ArgumentParser(description="lciod 板端数据校验器（host 侧）")
    ap.add_argument("--mode", required=True,
                    choices=["stats", "baseline", "delta", "perf", "uas",
                             "link", "storm", "qos", "qos-auto", "sd",
                             "sd-inject", "histo"])
    ap.add_argument("--reset", action="store_true",
                    help="baseline 模式：设备侧 lciod_probe --reset 归零计数")
    ap.add_argument("--expect", nargs="+", default=[],
                    help="delta 模式：必须严格增加的字段（如 read_bytes）")
    ap.add_argument("--baseline", default=BASELINE_DEFAULT,
                    help="baseline/delta 快照路径")
    ap.add_argument("--load-mb", type=int, default=64,
                    help="perf 模式 dd 读负载（MB），默认 64（与 lcview-perf 一致）")
    ap.add_argument("--block-dev", default="/dev/block/sda",
                    help="perf 模式 dd 读块设备路径")
    ap.add_argument("--dd-timeout", type=int, default=300,
                    help="perf/qos 模式 dd 执行 adb 超时（秒）")
    ap.add_argument("--storm-wait", type=int, default=15,
                    help="storm 模式注入后等待秒数（等 daemon 10s tick + "
                         "规则三评估窗口）")
    ap.add_argument("--log-since", type=int, default=None,
                    help="storm/sd 模式 logcat 抓取锚点（设备 epoch，只判锚点后 "
                         "缓冲；未给时注入前自动取设备时钟）")
    ap.add_argument("--qos-dd-mb", type=int, default=16,
                    help="qos 模式 dd 写临时文件大小（MB），默认 16")
    ap.add_argument("--qos-in-max-mib", type=float, default=_QOS_IN_GROUP_MAX_MIB,
                    help="qos 模式组内 dd 写速率上限容差（MiB/s），默认 12"
                         "（bg 8MiB/s 留 1.5x 余量）")
    ap.add_argument("--qos-out-min-mib", type=float, default=_QOS_OUT_MIN_MIB,
                    help="qos 模式组外 dd 写速率下限（MiB/s），默认 20"
                         "（另须 ≥ 1.5x 组内）")
    ap.add_argument("--qos-sleep", type=float, default=2.0,
                    help="qos 模式 setprop 后等待秒数（等 daemon 1s 周期应用），"
                         "默认 2")
    ap.add_argument("--qos-auto-lat-ms", type=int,
                    default=_QOS_AUTO_LAT_MS_DEFAULT,
                    help="qos-auto 模式自动档延迟阈值（毫秒），默认 50")
    ap.add_argument("--qos-auto-rounds", type=int, default=8,
                    help="qos-auto 模式收紧阶段注入/轮询轮数（每轮注入一次抬"
                         "延迟），默认 8")
    ap.add_argument("--qos-auto-sleep", type=float, default=2.0,
                    help="qos-auto 模式收紧阶段每轮等待秒数（等 daemon 1s 周期"
                         "与 10s 窗），默认 2")
    ap.add_argument("--qos-auto-relax-rounds", type=int, default=10,
                    help="qos-auto 模式放松阶段轮询轮数（等 10s 窗回落），默认 10")
    ap.add_argument("--qos-auto-relax-sleep", type=float, default=2.0,
                    help="qos-auto 模式放松阶段每轮等待秒数，默认 2")
    args = ap.parse_args()

    ensure_connected()
    report = []  # delta 模式增量报告；stats/baseline 无 report（统一打印）

    if args.mode == "perf":
        rc = mode_perf(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "storm":
        # R3 方向 7：风暴规则校验（用户态合成注入，不入 batch 验收列表）
        rc = mode_storm(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "qos":
        # R4 方向 6：SD 卡写 QoS 限速校验（依赖 daemon IoQosManager cgroup）
        rc = mode_qos(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "sd":
        # A 批二 方向 3/4：SD 用户态验收（sysfs + SD_HEALTH + sd JSON）
        rc = mode_sd(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "sd-inject":
        # SD 自愈演练闭环：合成 STUCK 注入 + 故障面四重断言
        rc = mode_sd_inject(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "qos-auto":
        # QoS 延迟感知自动分级闭环：注入抬延迟 → 收紧/放松断言
        rc = mode_qos_auto(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "histo":
        # C 批二 方向 3：USB 传输延迟/块大小二维直方图用户态验收
        rc = mode_histo(args)
        if rc == -1:
            print("ERROR: adb 执行超时")
            return 1
        return 0 if rc == 0 else 1

    if args.mode == "link":
        # 方向 7：全局链路节点 GET_LINK_STATS 快照校验（被动读取，无 IO 副作用）
        if not check_link_node():
            print(f"ERROR: 全局链路节点 {_LINK_DEV} 不存在（lciod_link 内核模块未加载？）")
            sys.exit(1)
        try:
            fields = parse_link_output(run_link_probe())
        except ValueError as ex:
            print(f"FAIL: {ex}")
            print("ERROR: lciod link 校验失败")
            sys.exit(1)
        errors = validate_link_stats(fields)
        if errors:
            for e in errors:
                print(f"FAIL: {e}")
            print("ERROR: lciod link 校验失败")
            sys.exit(1)
        print("OK: lciod link 校验通过（全局链路节点 GET_LINK_STATS 正常）")
        sys.exit(0)

    if args.mode == "stats":
        devices = parse_probe_output(run_probe())
        errors = validate_devices(devices)
    elif args.mode == "uas":
        # UAS 专项（R1 维测）：先按 stats 全字段校验（复用 validate_devices，
        # 协议范围已在其中校验）——真实故障（probe 空/字段缺失/enabled=0）
        # 保持判红 exit 1 不变；通过后单独判定 UAS 存在性（R4 方向 7）：
        # 无 protocol==1 设备记 skip（打印 "SKIP:" 标记 exit 0，验收层转
        # skip 态不判红）；有 UAS 设备但 validate_uas_devices 字段不齐仍
        # exit 1（UAS 打点链路未验证不得假绿）
        devices = parse_probe_output(run_probe())
        errors = validate_devices(devices)
        if not errors and not [d for d in devices if d.get("protocol") == "1"]:
            print("SKIP: 板上无 UAS 设备（protocol==1）")
            sys.exit(0)
        errors += validate_uas_devices(devices)
    elif args.mode == "baseline":
        probe_args = ["--reset"] if args.reset else []
        devices = parse_probe_output(run_probe(probe_args))
        errors = validate_devices(devices)
        if errors:
            # 零设备/字段不齐不得写基线（对齐 lcview baseline 防假绿原则）
            for e in errors:
                print(f"FAIL: {e}")
            print("ERROR: 快照校验未通过，拒绝写基线")
            sys.exit(1)
        snap = {d["minor"]: {f: d[f] for f in REQUIRED_FIELDS} for d in devices}
        Path(args.baseline).write_text(json.dumps(snap, indent=1), encoding="utf-8")
        print(f"OK: baseline 写入 {args.baseline}（{len(devices)} 设备"
              f"{'，已归零计数' if args.reset else ''}）")
        sys.exit(0)
    else:  # delta
        baseline = load_baseline(args.baseline)
        if baseline is None:
            print(f"ERROR: 基线不可读或损坏: {args.baseline}")
            sys.exit(1)
        devices = parse_probe_output(run_probe())
        # delta 取数后先跑 stats 校验：enabled=0/缺字段/abi 漂移等
        # 在 trigger 末步（diff 增量断言）被绕过，先校验不得假绿
        errors = validate_devices(devices)
        d_errors, report = diff_devices(baseline, devices, args.expect)
        errors += d_errors

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print(f"ERROR: lciod {args.mode} 校验失败")
        sys.exit(1)
    for line in report:
        print(line)
    print(f"OK: lciod {args.mode} 校验通过（{len(devices)} 设备）")
    sys.exit(0)


if __name__ == "__main__":
    # raise SystemExit(main())：perf/storm/qos/sd/histo 等模式内部用 return
    # 返回 0/1，若此入口只写 main() 则返回值被丢弃、进程 rc 恒 0（判红模式
    # 假绿）；对齐 lcview_check.py 入口，把 main 返回值透传为进程退出码。
    raise SystemExit(main())
