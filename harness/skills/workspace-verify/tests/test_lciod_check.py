# ============================================================
# test_lciod_check.py — lciod_check 纯函数级测试
# 所属模块：workspace-verify — 验证用例测试
# 覆盖：probe 输出解析（引号值/残缺行）、stats 校验判红项全集、
#       基线读写、delta 增量对比（未增/缺基线/缺字段均判红）。
#       不依赖设备（adb 主流程由板上用例实测兜底）。
# ============================================================

import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cases"))
import lciod_check as lc


class TestDefaultBaseline(unittest.TestCase):
    """方向 4：基线文件按轮次隔离——LCIOD_BASELINE_FILE 环境变量覆盖默认路径。"""

    def test_env_overrides_default(self):
        with mock.patch.dict(os.environ,
                             {"LCIOD_BASELINE_FILE": "/tmp/lciod_baseline_r1.json"}):
            self.assertEqual(lc._default_baseline(),
                             "/tmp/lciod_baseline_r1.json")

    def test_unset_falls_back(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lc._default_baseline(), "/tmp/lciod_baseline.json")


class TestEnsureConnected(unittest.TestCase):
    """方向 3：ensure_connected 的 root already-running 快路径（对齐
    ws_upload_tests ensure_user）——adbd 已在 root 时跳过 sleep+重连。"""

    def test_root_already_running_skips_reconnect(self):
        # adb root 输出 already running → 不二次连（ws_connected 仅首连一次）
        calls = []

        def fake_ws():
            calls.append("ws")
            return "10.0.0.5:5555"

        with mock.patch.object(lc, "run_adb",
                               return_value=("already running\n", 0)) as ra, \
                mock.patch.object(lc, "ws_connected", side_effect=fake_ws), \
                mock.patch.object(lc.time, "sleep"):
            lc.ensure_connected()
        ra.assert_called_once()
        self.assertEqual(len(calls), 1, "already running 不得二次重连")

    def test_root_switch_reconnects(self):
        # adb root 真实切换（重启 adbd）→ sleep 后重连探活（原行为保留）
        calls = []

        def fake_ws():
            calls.append("ws")
            return "10.0.0.5:5555"

        with mock.patch.object(lc, "run_adb",
                               return_value=("restarting adbd...\n", 0)) as ra, \
                mock.patch.object(lc, "ws_connected", side_effect=fake_ws), \
                mock.patch.object(lc.time, "sleep") as sl:
            lc.ensure_connected()
        ra.assert_called_once()
        self.assertEqual(len(calls), 2, "真实切换须首连 + root 后重连")
        sl.assert_called_once()

    def test_root_failure_exits(self):
        # adb root 失败 rc!=0 → 直接退出（不进入快路径/重连）
        with mock.patch.object(lc, "run_adb", return_value=("denied", 1)), \
                mock.patch.object(lc, "ws_connected",
                                  return_value="10.0.0.5:5555"), \
                mock.patch.object(lc.time, "sleep"), \
                mock.patch.object(lc.sys, "exit") as ex:
            lc.ensure_connected()
        ex.assert_called_once_with(2)

# 与 lciod_probe.c 输出同构的合法单行样本（vendor 含空格验证引号解析；
# 与 ioctl.h v5 ABI 对齐：read/write_error_count + abi_version=5）
VALID_LINE = (
    'device minor=0 path=/dev/vendor_lechao_usbd0 vid=0x04e8 pid=0x6344 protocol=0 '
    'vendor="SanDisk Corp" product="Ultra USB 3.0" '
    'read_bytes=4194304 write_bytes=1048576 read_ns=500000000 write_ns=200000000 '
    'read_cmds=64 write_cmds=16 error_count=0 reset_count=0 '
    'probe_count=1 disconnect_count=0 degrade_count=0 '
    'current_rate=0 peak_rate=8388608 last_transport_latency_ns=1200000 '
    'last_event_ts_ns=987654321 last_update=111222333 stall_count=0 '
    'corrupt_count=0 timeout_count=0 last_event_type=5 '
    'enabled=1 flags=0 event_drop_count=0 read_error_count=0 write_error_count=0 '
    'abi_version=5'
)


def _devices(*lines):
    return lc.parse_probe_output("\n".join(lines))


def _baseline_obj(line=VALID_LINE):
    devs = _devices(line)
    return {d["minor"]: {f: d[f] for f in lc.REQUIRED_FIELDS} for d in devs}


class ParseProbeOutputTest(unittest.TestCase):
    def test_valid_line_all_fields_parsed(self):
        devs = _devices(VALID_LINE)
        self.assertEqual(len(devs), 1)
        dev = devs[0]
        self.assertEqual(dev["minor"], "0")
        self.assertEqual(dev["path"], "/dev/vendor_lechao_usbd0")
        self.assertEqual(dev["vendor"], "SanDisk Corp")
        self.assertEqual(dev["product"], "Ultra USB 3.0")
        self.assertEqual(dev["read_bytes"], "4194304")
        self.assertEqual(dev["abi_version"], "5")
        self.assertEqual(dev["read_error_count"], "0")
        self.assertEqual(dev["write_error_count"], "0")
        self.assertEqual(dev["last_update"], "111222333")

    def test_blank_lines_skipped(self):
        self.assertEqual(len(_devices("", VALID_LINE, "")), 1)

    def test_multiple_devices(self):
        second = VALID_LINE.replace("minor=0", "minor=1").replace("usbd0", "usbd1")
        devs = _devices(VALID_LINE, second)
        self.assertEqual([d["minor"] for d in devs], ["0", "1"])

    def test_missing_minor_raises(self):
        with self.assertRaises(ValueError):
            _devices("device path=/dev/x foo=1")

    def test_garbage_line_raises(self):
        with self.assertRaises(ValueError):
            _devices("kernel panic at somewhere")

    def test_empty_output(self):
        self.assertEqual(_devices(""), [])


class ValidateDevicesTest(unittest.TestCase):
    def test_valid_sample_passes(self):
        self.assertEqual(lc.validate_devices(_devices(VALID_LINE)), [])

    def test_zero_devices_is_error(self):
        self.assertTrue(lc.validate_devices([]))

    def test_missing_field_is_error(self):
        line = VALID_LINE.replace("write_bytes=1048576 ", "")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("write_bytes" in e for e in errors))

    def test_non_numeric_field_is_error(self):
        line = VALID_LINE.replace("read_bytes=4194304", "read_bytes=4MB")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("read_bytes" in e and "非数字" in e for e in errors))

    def test_negative_field_is_error(self):
        line = VALID_LINE.replace("error_count=0", "error_count=-2")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("error_count" in e and "负值" in e for e in errors))

    def test_empty_vendor_is_error(self):
        line = VALID_LINE.replace('vendor="SanDisk Corp"', 'vendor=""')
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("vendor" in e for e in errors))

    def test_abi_drift_is_error(self):
        # 旧版 ABI（abi_version=2）判红：镜像副本与内核真相源漂移不得静默
        line = VALID_LINE.replace("abi_version=5", "abi_version=2")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("abi_version" in e for e in errors))

    def test_enabled_zero_is_error(self):
        # 监控被禁用（enabled=0）必须判红，不得"监控关着还全绿"
        line = VALID_LINE.replace("enabled=1", "enabled=0")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("enabled" in e and "被禁用" in e for e in errors))

    def test_enabled_missing_is_error(self):
        line = VALID_LINE.replace("enabled=1 ", "")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("enabled" in e for e in errors))

    def test_error_count_nonzero_is_error(self):
        # 无符号计数非负恒真，error_count 累计 >0 必须判红（防假绿）
        line = VALID_LINE.replace("error_count=0", "error_count=7")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("error_count" in e and "!= 0" in e for e in errors))

    def test_event_drop_count_nonzero_is_error(self):
        # event_drop_count 累计丢事件 >0 必须判红（防假绿）
        line = VALID_LINE.replace("event_drop_count=0", "event_drop_count=3")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("event_drop_count" in e and "!= 0" in e for e in errors))

    def test_read_error_count_nonzero_is_error(self):
        # R-14 方向 2：读方向错误分项累计 >0 必须判红（防假绿）
        line = VALID_LINE.replace("read_error_count=0", "read_error_count=2")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("read_error_count" in e and "!= 0" in e for e in errors))

    def test_write_error_count_nonzero_is_error(self):
        line = VALID_LINE.replace("write_error_count=0", "write_error_count=2")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("write_error_count" in e and "!= 0" in e for e in errors))

    def test_protocol_missing_is_error(self):
        # R1 UAS：protocol 已入 REQUIRED_FIELDS，缺失判红（字段映射回归点）
        line = VALID_LINE.replace("protocol=0 ", "")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("protocol" in e for e in errors))

    def test_protocol_invalid_is_error(self):
        # R1 UAS：protocol 仅允许 0(BOT)/1(UAS)，非法值判红
        line = VALID_LINE.replace("protocol=0", "protocol=5")
        errors = lc.validate_devices(_devices(line))
        self.assertTrue(any("protocol" in e and "非法" in e for e in errors))

    def test_protocol_uas_valid(self):
        # R1 UAS：protocol=1（UAS 设备）合法，不判红（存在性由 uas 模式断言）
        line = VALID_LINE.replace("protocol=0", "protocol=1")
        self.assertEqual(lc.validate_devices(_devices(line)), [])


class ValidateUasDevicesTest(unittest.TestCase):
    """R1 UAS 维测：uas 模式（lciod-uas 用例）校验逻辑。

    R4 方向 7：无 UAS 设备不再判红（存在性由 --mode uas 分支单独判定记
    skip），validate_uas_devices 只在 UAS 设备存在时校验其传输统计字段
    齐全（有 UAS 但字段不齐仍判红，防无 UAS 假绿放宽为假绿）。
    """

    def _uas_line(self):
        return VALID_LINE.replace("protocol=0", "protocol=1")

    def test_uas_present_passes(self):
        self.assertEqual(lc.validate_uas_devices(_devices(self._uas_line())), [])

    def test_no_uas_passes_for_skip(self):
        # R4 方向 7：仅 BOT 设备（protocol=0）→ 无 UAS 设备 → 不判红
        #（存在性由 --mode uas 分支单独判定，记 skip 不判红）
        self.assertEqual(lc.validate_uas_devices(_devices(VALID_LINE)), [])

    def test_empty_passes_for_skip(self):
        # R4 方向 7：probe 空（无任何设备）→ validate_uas_devices 不判红
        #（validate_devices 已判红"输出为空"，UAS 存在性判定走 skip）
        self.assertEqual(lc.validate_uas_devices([]), [])

    def test_uas_missing_transport_fields_is_error(self):
        # UAS 设备缺传输统计字段（read_bytes 等）→ 判红（打点链路不完整）
        line = self._uas_line().replace("read_bytes=4194304 ", "")
        errors = lc.validate_uas_devices(_devices(line))
        self.assertTrue(any("read_bytes" in e for e in errors))

    def test_uas_branch_no_device_skips_exit_zero(self):
        # R4 方向 7 端到端：--mode uas 且板上无 UAS 设备（仅 BOT protocol=0）
        # → uas 分支记 skip——打印 "SKIP:" 标记行并 exit 0（验收层 hostcmd
        # 检出行首 SKIP: 转 skip 态不判红）；validate_devices 通过（probe
        # 空/字段缺失仍判红 exit 1 不变）
        import contextlib
        import io
        buf = io.StringIO()
        with mock.patch.object(lc, "ensure_connected"), \
                mock.patch.object(lc, "run_probe", return_value=VALID_LINE + "\n"), \
                mock.patch.object(lc.sys, "exit", side_effect=SystemExit) as ex, \
                mock.patch.object(lc.sys, "argv",
                                  ["lciod_check.py", "--mode", "uas"]), \
                contextlib.redirect_stdout(buf):
            with self.assertRaises(SystemExit):
                lc.main()
        ex.assert_called_once_with(0)
        self.assertIn("SKIP: 板上无 UAS 设备（protocol==1）", buf.getvalue())


# 方向 7：--mode link 样例（与 lciod_probe --link 输出同构：GET_LINK_STATS
# ioctl 快照单行 key=value，含 v4 链路事件计数与最近事件字段）
VALID_LINK_LINE = (
    'link connect_count=3 disconnect_count=1 enum_fail_count=0 overcurrent_count=1 '
    'last_event_ts_ns=123456789 last_event_type=9 '
    'last_busnum=1 last_port=2 last_vid=0x0781 last_pid=0x5583 '
    'last_err=0 last_count=1 last_duration_ns=500000 abi_version=5'
)


def _link_fields(line=VALID_LINK_LINE):
    return lc.parse_link_output(line)


class LinkModeTest(unittest.TestCase):
    """方向 7：--mode link（全局链路节点 GET_LINK_STATS 校验）纯函数级测试。

    覆盖解析（含残缺行）、字段齐全性/数值合法性/abi 漂移判红、节点缺失
    判红与 ioctl 失败判红。adb 主流程由板上用例实测兜底。
    """

    def test_parse_valid_link_line(self):
        fields = _link_fields()
        self.assertEqual(fields["connect_count"], "3")
        self.assertEqual(fields["overcurrent_count"], "1")
        self.assertEqual(fields["last_event_type"], "9")
        self.assertEqual(fields["last_vid"], "0x0781")
        self.assertEqual(fields["abi_version"], "5")

    def test_parse_missing_connect_count_raises(self):
        with self.assertRaises(ValueError):
            _link_fields(VALID_LINK_LINE.replace("connect_count=3 ", ""))

    def test_valid_link_passes(self):
        self.assertEqual(lc.validate_link_stats(_link_fields()), [])

    def test_missing_field_is_error(self):
        line = VALID_LINK_LINE.replace("disconnect_count=1 ", "")
        errors = lc.validate_link_stats(_link_fields(line))
        self.assertTrue(any("disconnect_count" in e for e in errors))

    def test_bad_abi_is_error(self):
        # ABI 漂移（3 != 5）判红
        line = VALID_LINK_LINE.replace("abi_version=5", "abi_version=3")
        errors = lc.validate_link_stats(_link_fields(line))
        self.assertTrue(any("abi_version" in e for e in errors))

    def test_negative_count_is_error(self):
        line = VALID_LINK_LINE.replace("connect_count=3", "connect_count=-1")
        errors = lc.validate_link_stats(_link_fields(line))
        self.assertTrue(any("connect_count" in e and "负值" in e for e in errors))

    def test_non_numeric_count_is_error(self):
        line = VALID_LINK_LINE.replace("connect_count=3", "connect_count=3x")
        errors = lc.validate_link_stats(_link_fields(line))
        self.assertTrue(any("connect_count" in e and "非数字" in e for e in errors))

    def test_event_type_out_of_range_is_error(self):
        # 事件枚举 0..9（v4 含链路事件 7/8/9），10 越界判红
        line = VALID_LINK_LINE.replace("last_event_type=9", "last_event_type=10")
        errors = lc.validate_link_stats(_link_fields(line))
        self.assertTrue(any("last_event_type" in e and "超出枚举" in e for e in errors))

    def test_negative_last_err_allowed(self):
        # s32 errno 可为负（-EPIPE 等），不得判红
        line = VALID_LINK_LINE.replace("last_err=0", "last_err=-71")
        self.assertEqual(lc.validate_link_stats(_link_fields(line)), [])

    def test_link_node_missing_is_red(self):
        # 节点缺失：ls 失败 → check_link_node False → 判红（防假绿）
        with mock.patch.object(lc, "adb", return_value=("", 1)):
            self.assertFalse(lc.check_link_node())

    def test_link_node_present(self):
        with mock.patch.object(
                lc, "adb",
                return_value=("drwxr-xr-x root root vendor_lechao_usbd_link", 0)):
            self.assertTrue(lc.check_link_node())

    def test_link_ioctl_failure_exits(self):
        # GET_LINK_STATS ioctl 失败（lciod_probe --link rc!=0）→ 判红退出（防假绿）
        with mock.patch.object(lc, "run_adb", return_value=("error", 1)), \
                mock.patch.object(lc.sys, "exit") as ex:
            lc.run_link_probe()
        ex.assert_called_once_with(1)

    def test_link_stats_size_frozen(self):
        # struct vendor_lechao_usbd_link_stats 尺寸契约（与内核真相源布局一致）
        self.assertEqual(lc.link_stats_struct_size(), 80)


class BaselineTest(unittest.TestCase):
    def test_load_written_baseline_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "b.json")
            snap = _baseline_obj()
            Path(path).write_text(json.dumps(snap), encoding="utf-8")
            # load_baseline 契约：数值字段归一为 int，vendor/product 文本保留 str
            expected = {k: {f: (int(v, 0) if isinstance(v, str) else int(v))
                            if f not in lc._TEXT_FIELDS else v
                            for f, v in fields.items()}
                        for k, fields in snap.items()}
            self.assertEqual(lc.load_baseline(path), expected)

    def test_load_missing_returns_none(self):
        self.assertIsNone(lc.load_baseline("/nonexistent/lciod_base.json"))

    def test_load_corrupt_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertIsNone(lc.load_baseline(str(path)))


class DiffDevicesTest(unittest.TestCase):
    def setUp(self):
        # baseline 结构与 load_baseline 返回一致：{minor_str: {field: value}}
        self.baseline = _baseline_obj()

    def _current(self, **overrides):
        devs = _devices(VALID_LINE)
        for k, v in overrides.items():
            devs[0][k] = str(v)
        return devs

    def test_expect_increment_passes(self):
        cur = self._current(read_bytes=5194304, read_cmds=65)
        errors, report = lc.diff_devices(self.baseline, cur, ["read_bytes", "read_cmds"])
        self.assertEqual(errors, [])
        self.assertEqual(len(report), 2)

    def test_zero_delta_is_error(self):
        # dd 未生效 → 计数未增 → 必须判红（防假绿核心）
        errors, _ = lc.diff_devices(self.baseline, self._current(), ["read_bytes"])
        self.assertTrue(any("未增加" in e for e in errors))

    def test_decreased_is_error(self):
        cur = self._current(read_bytes=1)
        errors, _ = lc.diff_devices(self.baseline, cur, ["read_bytes"])
        self.assertTrue(any("未增加" in e for e in errors))

    def test_device_not_in_baseline_is_error(self):
        cur = self._current(minor=7)
        errors, _ = lc.diff_devices(self.baseline, cur, ["read_bytes"])
        self.assertTrue(any("不在基线中" in e for e in errors))

    def test_missing_expect_field_is_error(self):
        cur = _devices(VALID_LINE.replace("read_bytes=4194304 ", ""))
        errors, _ = lc.diff_devices(self.baseline, cur, ["read_bytes"])
        self.assertTrue(any("缺 expect 字段" in e for e in errors))

    def test_zero_current_devices_is_error(self):
        errors, _ = lc.diff_devices(self.baseline, [], ["read_bytes"])
        self.assertTrue(any("输出为空" in e for e in errors))

    def test_empty_baseline_is_error(self):
        errors, _ = lc.diff_devices({}, self._current(), ["read_bytes"])
        self.assertTrue(any("基线无设备" in e for e in errors))

    def test_empty_expect_fields_is_error(self):
        # 空 expect：增量断言循环不执行、errors 恒空直接判绿（假绿根源），
        # 必须判红，yaml 漏写 --expect 即核心增量断言全跳过
        errors, _ = lc.diff_devices(self.baseline, self._current(), [])
        self.assertTrue(any("未指定 --expect" in e for e in errors))

    def test_non_dict_baseline_is_error(self):
        errors, _ = lc.diff_devices("corrupt", self._current(), ["read_bytes"])
        self.assertTrue(any("基线无设备" in e for e in errors))

    def test_virtual_uas_zero_delta_exempted(self):
        # 虚拟 UAS 通道（protocol=1 vid=0 pid=0）无增量属无 UAS 流量源预期，
        # 豁免不判红（记 SKIP 提示行）——真实环境无 UAS 设备时 lciod-trigger
        # 的 delta 断言不得因虚拟通道恒 0 假红
        line = VALID_LINE.replace("protocol=0", "protocol=1") \
            .replace("vid=0x04e8 pid=0x6344", "vid=0x0000 pid=0x0000")
        base = _baseline_obj(line)
        devs = _devices(line)  # 无增量（与基线同值）
        errors, report = lc.diff_devices(base, devs, ["read_bytes"])
        self.assertEqual(errors, [])
        self.assertTrue(any("SKIP" in r and "虚拟 UAS" in r for r in report))

    def test_real_uas_zero_delta_still_error(self):
        # 真实 UAS 设备（protocol=1 且 vid/pid 非零）无增量仍判红——打点链路
        # 真故障不得因豁免假绿（豁免仅限 vid/pid 全 0 的虚拟占位通道）
        line = VALID_LINE.replace("protocol=0", "protocol=1") \
            .replace("vid=0x04e8 pid=0x6344", "vid=0x04e8 pid=0x6300")
        base = _baseline_obj(line)
        devs = _devices(line)  # 无增量
        errors, _ = lc.diff_devices(base, devs, ["read_bytes"])
        self.assertTrue(any("未增加" in e for e in errors))

    def test_virtual_uas_but_bot_no_increment_still_error(self):
        # 虚拟 UAS 豁免不掩盖 BOT 设备无增量：真实 BOT 设备（protocol=0）无
        # 增量仍须判红——豁免仅放行虚拟通道，不改变"触发未生效"核心门禁
        base = _baseline_obj()
        devs = _devices(VALID_LINE)  # BOT 设备无增量（与基线同值）
        errors, _ = lc.diff_devices(base, devs, ["read_bytes"])
        self.assertTrue(any("未增加" in e for e in errors))

    def test_multi_device_partial_increment_passes(self):
        # 真实多设备拓扑回归：dd 只作用于一个块设备，其对应 usbd 通道增量，
        # 其余设备（内核枚举顺序不定）零增量属预期——聚合断言 ≥1 设备在
        # 全部 expect 字段严格增即通过，不再因非目标设备零增量误判红。
        line0 = VALID_LINE  # minor=0，dd 目标，有增量
        line1 = (VALID_LINE.replace("minor=0", "minor=1")
                 .replace("usbd0", "usbd1")
                 .replace("vid=0x04e8 pid=0x6344", "vid=0x0000 pid=0x0000")
                 .replace("protocol=0", "protocol=1"))  # 非目标，零增量
        base = {d["minor"]: {f: d[f] for f in lc.REQUIRED_FIELDS}
                for d in _devices(line0, line1)}
        cur0 = (line0.replace("read_bytes=4194304", "read_bytes=8388608")
                .replace("read_cmds=64", "read_cmds=128"))
        errors, report = lc.diff_devices(base, _devices(cur0, line1),
                                         ["read_bytes", "read_cmds"])
        self.assertEqual(errors, [])
        self.assertTrue(any("minor=1" in r for r in report))

    def test_multi_device_none_increment_error(self):
        # 多设备均零增量：dd 触发未生效，聚合断言判红（防假绿）
        line0 = VALID_LINE
        line1 = (VALID_LINE.replace("minor=0", "minor=1")
                 .replace("usbd0", "usbd1")
                 .replace("vid=0x04e8 pid=0x6344", "vid=0x04e8 pid=0x6300"))
        base = {d["minor"]: {f: d[f] for f in lc.REQUIRED_FIELDS}
                for d in _devices(line0, line1)}
        errors, _ = lc.diff_devices(base, _devices(line0, line1), ["read_bytes"])
        self.assertTrue(any("未增加" in e for e in errors))


# ============================================================
# R3 方向 7：--mode storm 风暴规则校验（TestModeStorm）
# 覆盖：注入后 logcat 命中 "rule":"storm" 判绿 / 未命中判红 / setprop 失败
# 判红 / 设备时钟不可读判红 / 显式 --log-since 时跳过时钟锚定。CDP-DOD-001：
# 每个判红逻辑配套破坏场景红灯用例。
# ============================================================

# storm 事件日志行（仿 link_monitor EmitLinkEventJson 手拼 JSON，tag
# lechao_lciod_event）
_STORM_HIT = ('07-01 12:00:00.000  1234  1234 I lechao_lciod_event: '
              '{"rule":"storm","device":0,"count":22,"window_s":60,'
              '"threshold":10}\n')
_STORM_MISS = ('07-01 12:00:00.000  1234  1234 I lechao_lciod_event: '
               '{"rule":"other","device":0}\n')


def _args(**kw):
    a = argparse.Namespace()
    a.storm_wait = kw.get("storm_wait", 15)
    a.log_since = kw.get("log_since", None)
    return a


class FakeAdb:
    """伪 adb（storm 模式专用）：支持 shell setprop / shell date / logcat
    抓取，各子命令可注入 rc 模拟失败/超时。"""

    def __init__(self, setprop_rc=0, setprop_out="",
                 date_out="", date_rc=0, logcat_out="", logcat_rc=0):
        self.setprop_rc = setprop_rc
        self.setprop_out = setprop_out
        self.date_out = date_out
        self.date_rc = date_rc
        self.logcat_out = logcat_out
        self.logcat_rc = logcat_rc
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "shell":
            cmd = args[1]
            if cmd.startswith("setprop"):
                return (self.setprop_out, self.setprop_rc)
            if cmd.startswith("date "):
                return (self.date_out, self.date_rc)
            return ("", 0)
        if args[0] == "logcat":
            return (self.logcat_out, self.logcat_rc)
        return ("", 0)


class TestModeStorm(unittest.TestCase):
    def _run(self, fake, **kw):
        with mock.patch.object(lc, "adb", fake):
            with mock.patch.object(lc.time, "sleep"):
                return lc.mode_storm(_args(**kw))

    def test_storm_rule_hit_passes(self):
        # 注入后 logcat 命中 "rule":"storm" → 通过
        fake = FakeAdb(date_out="1000\n", date_rc=0,
                       logcat_out=_STORM_HIT, logcat_rc=0)
        self.assertEqual(self._run(fake), 0)

    def test_storm_no_rule_red(self):
        # logcat 无 "rule":"storm" 行（规则三未触发/注入未生效）→ 判红
        fake = FakeAdb(date_out="1000\n", date_rc=0,
                       logcat_out=_STORM_MISS, logcat_rc=0)
        self.assertEqual(self._run(fake), 1)

    def test_storm_setprop_fail_red(self):
        # setprop 注入失败（rc!=0）→ 判红（注入不可用不得当"未触发"蒙混）
        fake = FakeAdb(date_out="1000\n", date_rc=0,
                       setprop_rc=1, logcat_out=_STORM_HIT, logcat_rc=0)
        self.assertEqual(self._run(fake), 1)

    def test_storm_clock_unreadable_red(self):
        # 未显式 --log-since 且设备时钟不可读 → 判红（logcat 窗口无法锚定）
        fake = FakeAdb(date_out="bad", date_rc=1)
        self.assertEqual(self._run(fake), 1)

    def test_storm_explicit_log_since_skips_clock(self):
        # 显式 --log-since 时不得取设备时钟（注入前时钟锚定被跳过）
        fake = FakeAdb(logcat_out=_STORM_HIT, logcat_rc=0)
        rc = self._run(fake, log_since=999)
        self.assertEqual(rc, 0)
        self.assertFalse(any(c[0] == "shell" and str(c[1]).startswith("date ")
                             for c in fake.calls))


# ============================================================
# R4 方向 6：--mode qos SD 卡写 QoS 限速校验（TestModeQos）
# 覆盖：cgroup 探测（v2/v1）/限速文件断言 8388608/组内压制 ≤12 MiB/s/
# 组外 ≥ max(20, 1.5x 组内)/无 cgroup 判红/组缺失判红/限速未生效判红。
# CDP-DOD-001：每个判红逻辑配套破坏场景红灯用例。
# ============================================================

# dd 速率样本：组内压制 ~8 MiB/s（16777216B/2.0s），组外不受限 ~40 MiB/s
_QOS_IN_DD = "16777216 bytes (17 MB) copied, 2.0 s, 8.4 MB/s\n"
_QOS_OUT_DD = "16777216 bytes (17 MB) copied, 0.4 s, 42 MB/s\n"
# 组内未压制（~53 MiB/s）与组外不足（~8 MiB/s）的破坏场景样本
_QOS_IN_FAST_DD = "16777216 bytes (17 MB) copied, 0.3 s, 55 MB/s\n"


def _qos_args(**kw):
    a = argparse.Namespace()
    a.qos_dd_mb = kw.get("qos_dd_mb", 16)
    a.qos_in_max_mib = kw.get("qos_in_max_mib", lc._QOS_IN_GROUP_MAX_MIB)
    a.qos_out_min_mib = kw.get("qos_out_min_mib", lc._QOS_OUT_MIN_MIB)
    a.qos_sleep = kw.get("qos_sleep", 2.0)
    a.dd_timeout = kw.get("dd_timeout", 300)
    return a


class FakeAdbQos:
    """伪 adb（qos 模式专用）：模拟 cgroup 版本探测/组与限速文件/两次 dd 测速。

    各命令结果可注入 rc 模拟失败；dd 测速按临时文件 in/out 区分（组内含
    cgroup.procs 迁移，组外为裸 dd）。"""

    def __init__(self, controllers="cpu io cpuset", group_rc=0,
                 limit="8:0 rbps=8388608 wbps=8388608\n", limit_rc=0,
                 in_dd=_QOS_IN_DD, out_dd=_QOS_OUT_DD, dd_rc=0,
                 std_blkio=False, dev_blkio=False, limit_after_bg=None):
        self.controllers = controllers
        self.group_rc = group_rc
        self.limit = limit
        self.limit_rc = limit_rc
        # setprop ioqos_level 之后限速文件读回值（None=与 limit 相同）：
        # 真机 off→bg 场景起点为空、setprop 后由 daemon 写入限速值。
        self.limit_after_bg = limit_after_bg
        self._setprop_done = False
        self.in_dd = in_dd
        self.out_dd = out_dd
        self.dd_rc = dd_rc
        self.std_blkio = std_blkio   # <root>/blkio 存在（标准 v1 布局）
        self.dev_blkio = dev_blkio   # /dev/blkio 存在（真机 Android v1 布局）
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "shell":
            if args[1] == "cat":
                if "cgroup.controllers" in args[2]:
                    return (self.controllers, 0)
                # io.max / blkio 限速文件：setprop 后若注入了 limit_after_bg
                # 则返回 bg 生效值（模拟 daemon 1s 周期写入）
                if self._setprop_done and self.limit_after_bg is not None:
                    return (self.limit_after_bg, self.limit_rc)
                return (self.limit, self.limit_rc)
            if args[1] == "ls":
                if "/lechao_bg" in args[-1]:
                    return (args[-1] + "\n", self.group_rc)
                if "/sys/fs/cgroup/blkio" in args[-1]:
                    return (args[-1] + "\n", 0) if self.std_blkio else ("", 1)
                if "/dev/blkio" in args[-1]:
                    return (args[-1] + "\n", 0) if self.dev_blkio else ("", 1)
                return (args[-1] + "\n", self.group_rc)
            if args[1] == "setprop":
                self._setprop_done = True
                return ("", 0)
            if args[1] == "rm":
                return ("", 0)
            # dd 测速：单参下发（["shell", <cmd>]），命令串直接是 args[1]
            if "lciod_qos_in_" in args[1]:
                return (self.in_dd, self.dd_rc)
            if "lciod_qos_out_" in args[1]:
                return (self.out_dd, self.dd_rc)
            return ("", 0)
        return ("", 0)


class TestModeQos(unittest.TestCase):
    def _run(self, fake, **kw):
        with mock.patch.object(lc, "adb", fake), \
                mock.patch.object(lc.time, "sleep"):
            return lc.mode_qos(_qos_args(**kw))

    def test_qos_pass_ok(self):
        # v2 cgroup + 组存在 + 限速含 8388608 + 组内压制 + 组外不受限 → 通过
        fake = FakeAdbQos()
        self.assertEqual(self._run(fake), 0)

    def test_qos_no_cgroup_red(self):
        # v2 无 io 且 v1 blkio 目录缺失（标准与 /dev/blkio 均无）→ 判红（防假绿）
        fake = FakeAdbQos(controllers="cpu cpuset")
        self.assertEqual(self._run(fake), 1)

    def test_qos_v1_std_blkio_pass(self):
        # 标准 v1 布局：<root>/blkio 存在（无 io controller）→ 限速路径走 blkio
        fake = FakeAdbQos(controllers="cpu cpuset", std_blkio=True,
                          group_rc=0)
        self.assertEqual(self._run(fake), 0)

    def test_qos_v1_dev_blkio_pass(self):
        # 真机 Android v1 布局：<root>/blkio 缺失但 /dev/blkio 存在（RPi5 实测）
        fake = FakeAdbQos(controllers="cpu cpuset", dev_blkio=True,
                          group_rc=0)
        self.assertEqual(self._run(fake), 0)

    def test_qos_v1_dev_blkio_group_missing_red(self):
        # /dev/blkio 探测成功但 lechao_bg 组不存在 → 判红
        fake = FakeAdbQos(controllers="cpu cpuset", dev_blkio=True, group_rc=1)
        self.assertEqual(self._run(fake), 1)

    def test_qos_group_missing_red(self):
        # v2 可探测但 lechao_bg 组不存在（daemon 未建组）→ 判红
        fake = FakeAdbQos(group_rc=1)
        self.assertEqual(self._run(fake), 1)

    def test_qos_limit_file_unreadable_red(self):
        # 限速文件不可读（cat rc!=0）→ 判红（读不到无从判 bg 是否生效）
        fake = FakeAdbQos(limit="", limit_rc=1)
        self.assertEqual(self._run(fake), 1)

    def test_qos_off_empty_then_bg_applied_passes(self):
        # 方向 2 假红修复：真机 off 档起点限速文件为空（内核移除限速条目）属
        # 合法态不得判红；setprop bg 后 daemon 写入限速值 → 通过
        fake = FakeAdbQos(limit="", limit_rc=0,
                          limit_after_bg="179:0 8388608\n")
        self.assertEqual(self._run(fake), 0)

    def test_qos_off_empty_and_bg_not_applied_red(self):
        # 起点空 + setprop 后仍空（daemon 未应用 bg）→ 判红（防假绿）
        fake = FakeAdbQos(limit="", limit_rc=0, limit_after_bg="")
        self.assertEqual(self._run(fake), 1)

    def test_qos_bg_limit_not_applied_red(self):
        # setprop bg 后限速文件无 8388608（daemon 周期未应用）→ 判红
        fake = FakeAdbQos(limit="8:0 rbps=max wbps=max\n")
        self.assertEqual(self._run(fake), 1)

    def test_qos_in_group_not_throttled_red(self):
        # 组内 dd 速率 ~53 MiB/s > 12 MiB/s 容差（bg 8MiB/s 未压制）→ 判红
        fake = FakeAdbQos(in_dd=_QOS_IN_FAST_DD)
        self.assertEqual(self._run(fake), 1)

    def test_qos_out_group_not_fast_enough_red(self):
        # 组外 dd 速率 ~8 MiB/s < max(20, 1.5x 组内)（未显著高于组内）→ 判红
        fake = FakeAdbQos(out_dd=_QOS_IN_DD)
        self.assertEqual(self._run(fake), 1)

    def test_qos_dd_failure_red(self):
        # dd 执行失败（rc!=0）→ 判红（测速不可得不得当压制/不受限蒙混）
        fake = FakeAdbQos(dd_rc=1)
        self.assertEqual(self._run(fake), 1)

    def test_qos_dd_command_single_arg_and_sync(self):
        # 回归锚点（CDP-DOD-001）：qos dd 测速须「单参下发」且带 conv=fsync。
        # 多参 ["shell","sh","-c",cmd] 经 adb 拼接会被设备外层 shell 重解析
        # （echo $$ > tasks 退化写非法内容 → MIGRATE_FAIL）；裸 dd 靠 page
        # cache 令写回由内核 kthread 提交、不计入 lechao_bg → blk-throttle 失效。
        fake = FakeAdbQos()
        self.assertEqual(self._run(fake), 0)
        dd_calls = [c for c in fake.calls
                    if len(c) == 2 and isinstance(c[1], str)
                    and "dd if=/dev/zero" in c[1]]
        self.assertTrue(dd_calls, "须有 dd 测速调用")
        for c in dd_calls:
            self.assertEqual(c[0], "shell")
            self.assertNotIn("sh", c[:1])
            self.assertIn("conv=fsync", c[1])


# ============================================================
# C 批二 方向 3：--mode histo USB 传输延迟/块大小二维直方图验收
# （TestParseHisto / TestValidateHisto / TestModeHisto）
# 覆盖：解析（引号数组/跳过非直方图行/残缺与非法元素抛 ValueError）、
# 结构断言全判红项（维度/bounds/counts 长度/abi/负值）、mode 端到端
# （结构通过 + 读方向增量判绿 / 无设备 / 结构失败 / 无增量 / dd 超时）。
# CDP-DOD-001：每个判红逻辑配套破坏场景红灯用例。
# ============================================================


def _histo_line(minor=0, dirs=lc._HISTO_DIRS, size_bins=lc._HISTO_SIZE_BINS,
                lat_bins=lc._HISTO_LAT_BINS, abi=5,
                size_bounds=lc._HISTO_SIZE_BOUNDS,
                lat_bounds=lc._HISTO_LAT_BOUNDS, counts=None):
    """构造与 lciod_probe --histo 输出同构的单行样本。"""
    if counts is None:
        counts = [0] * lc._HISTO_COUNT_CELLS
    sb = ",".join(str(x) for x in size_bounds)
    lb = ",".join(str(x) for x in lat_bounds)
    cnt = ",".join(str(x) for x in counts)
    return (f'histo minor={minor} dirs={dirs} size_bins={size_bins} '
            f'latency_bins={lat_bins} mono_ts_ns=123456789 '
            f'size_bounds="{sb}" latency_bounds="{lb}" counts="{cnt}" '
            f'abi_version={abi}')


class TestParseHisto(unittest.TestCase):
    def test_valid_line_all_fields_parsed(self):
        out = lc.parse_histo_output(_histo_line())
        self.assertEqual(list(out), ["0"])
        rec = out["0"]
        self.assertEqual(rec["dirs"], 2)
        self.assertEqual(rec["size_bins"], 8)
        self.assertEqual(rec["latency_bins"], 12)
        self.assertEqual(rec["abi_version"], 5)
        self.assertEqual(len(rec["counts"]), 192)
        self.assertEqual(tuple(rec["size_bounds"]), lc._HISTO_SIZE_BOUNDS)
        self.assertEqual(tuple(rec["latency_bounds"]), lc._HISTO_LAT_BOUNDS)

    def test_non_histo_lines_skipped(self):
        # 同时需 minor 与 counts 才识别；普通 stats 行/空白行跳过
        text = "\n".join([VALID_LINE, "", "kernel noise line"])
        self.assertEqual(lc.parse_histo_output(text), {})

    def test_missing_array_field_raises(self):
        # 含 minor 与 counts 但缺 size_bounds → 判为残缺行抛 ValueError
        line = _histo_line().replace('size_bounds="', 'sizebnds="', 1)
        with self.assertRaises(ValueError):
            lc.parse_histo_output(line)

    def test_bad_array_element_raises(self):
        # counts 含非十进制元素 → 抛 ValueError（残缺数据不得静默当有效）
        line = _histo_line(counts=[0] * 191 + ["x"])
        with self.assertRaises(ValueError):
            lc.parse_histo_output(line)

    def test_missing_scalar_field_raises(self):
        line = _histo_line().replace("dirs=2 ", "")
        with self.assertRaises(ValueError):
            lc.parse_histo_output(line)


class TestValidateHisto(unittest.TestCase):
    def _rec(self, **kw):
        return lc.parse_histo_output(_histo_line(**kw))["0"]

    def test_valid_passes(self):
        self.assertEqual(lc.validate_histo(self._rec()), [])

    def test_dirs_wrong_is_error(self):
        errors = lc.validate_histo(self._rec(dirs=3))
        self.assertTrue(any("dirs" in e for e in errors))

    def test_size_bins_wrong_is_error(self):
        errors = lc.validate_histo(self._rec(size_bins=7))
        self.assertTrue(any("size_bins" in e for e in errors))

    def test_latency_bins_wrong_is_error(self):
        errors = lc.validate_histo(self._rec(lat_bins=11))
        self.assertTrue(any("latency_bins" in e for e in errors))

    def test_abi_drift_is_error(self):
        errors = lc.validate_histo(self._rec(abi=4))
        self.assertTrue(any("abi_version" in e for e in errors))

    def test_counts_length_is_error(self):
        errors = lc.validate_histo(self._rec(counts=[0] * 191))
        self.assertTrue(any("counts 长度" in e for e in errors))

    def test_size_bounds_mismatch_is_error(self):
        bad = tuple(list(lc._HISTO_SIZE_BOUNDS[:-1]) + [123])
        errors = lc.validate_histo(self._rec(size_bounds=bad))
        self.assertTrue(any("size_bounds" in e for e in errors))

    def test_latency_bounds_mismatch_is_error(self):
        bad = tuple(list(lc._HISTO_LAT_BOUNDS[:-1]) + [456])
        errors = lc.validate_histo(self._rec(lat_bounds=bad))
        self.assertTrue(any("latency_bounds" in e for e in errors))

    def test_counts_negative_is_error(self):
        counts = [0] * lc._HISTO_COUNT_CELLS
        counts[0] = -1
        errors = lc.validate_histo(self._rec(counts=counts))
        self.assertTrue(any("负值" in e for e in errors))


class FakeAdbHisto:
    """伪 adb（histo 模式专用）：仅模拟 drop_caches 与 dd 读调用。"""

    def __init__(self, dd_rc=0):
        self.dd_rc = dd_rc
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "shell" and "dd if=" in str(args[1]):
            return ("", self.dd_rc)
        return ("", 0)


def _histo_args(**kw):
    a = argparse.Namespace()
    a.dd_timeout = kw.get("dd_timeout", 300)
    return a


class TestModeHisto(unittest.TestCase):
    def _run(self, probes, dd_rc=0):
        fake = FakeAdbHisto(dd_rc=dd_rc)
        with mock.patch.object(lc, "run_histo_probe", side_effect=probes), \
                mock.patch.object(lc, "adb", fake):
            return lc.mode_histo(_histo_args()), fake

    def test_pass_with_read_increment(self):
        # 读方向（dir 0）首格 +5 → 结构通过 + 增量判绿
        after = [0] * lc._HISTO_COUNT_CELLS
        after[0] = 5
        rc, fake = self._run([_histo_line(), _histo_line(counts=after)])
        self.assertEqual(rc, 0)
        # dd 只读 /dev/null（无破坏面）
        self.assertTrue(any("of=/dev/null" in str(c[1]) for c in fake.calls))

    def test_no_devices_red(self):
        rc, _ = self._run([""])
        self.assertEqual(rc, 1)

    def test_structure_failure_red(self):
        rc, _ = self._run([_histo_line(dirs=3)])
        self.assertEqual(rc, 1)

    def test_no_read_increment_red(self):
        # 前后读方向计数均 0（dd 触发未生效）→ 判红（防假绿）
        rc, _ = self._run([_histo_line(), _histo_line()])
        self.assertEqual(rc, 1)

    def test_write_only_increment_red(self):
        # 仅写方向（dir 1）增量、读方向零增量 → 不得判绿（读方向为唯一判据）
        after = [0] * lc._HISTO_COUNT_CELLS
        after[lc._HISTO_READ_CELLS] = 9  # 写方向首格
        rc, _ = self._run([_histo_line(), _histo_line(counts=after)])
        self.assertEqual(rc, 1)

    def test_dd_timeout_returns_minus_one(self):
        after = [0] * lc._HISTO_COUNT_CELLS
        after[0] = 5
        rc, _ = self._run([_histo_line(), _histo_line(counts=after)],
                          dd_rc=-1)
        self.assertEqual(rc, -1)


# ============================================================
# 方向 2：SD 产载设备读独立变量 LCIOD_SD_BLOCK_DEV（不复用 LCVIEW_BLOCK_DEV）。
# 防外部全局导出 LCVIEW_BLOCK_DEV（指向 USB 设备）时 SD 产载错配。
# ============================================================
class TestSdBlockDev(unittest.TestCase):
    def test_env_overrides_default(self):
        with mock.patch.dict(os.environ,
                             {"LCIOD_SD_BLOCK_DEV": "/dev/block/mmcblk1"}):
            self.assertEqual(lc._sd_block_dev(), "/dev/block/mmcblk1")

    def test_default_is_sd_mmcblk0(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lc._sd_block_dev(), "/dev/block/mmcblk0")

    def test_ignores_lcview_block_dev(self):
        # 关键回归锚点：LCVIEW_BLOCK_DEV 全局导出（USB 设备）不得影响 SD 产载
        with mock.patch.dict(os.environ,
                             {"LCVIEW_BLOCK_DEV": "/dev/block/sda"},
                             clear=True):
            self.assertEqual(lc._sd_block_dev(), "/dev/block/mmcblk0")


class _FakeAdbSd:
    """伪 adb（mode_sd 产载检查）：记录调用、统一返回空成功。"""

    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        return ("", 0)


class TestModeSdLoadDevice(unittest.TestCase):
    def _args(self):
        a = argparse.Namespace()
        a.dd_timeout = 300
        a.log_since = 123
        return a

    def test_load_uses_sd_var_not_lcview(self):
        # mode_sd 自驱产载须读 LCIOD_SD_BLOCK_DEV，且绝不读 LCVIEW_BLOCK_DEV
        fake = _FakeAdbSd()
        env = {"LCVIEW_BLOCK_DEV": "/dev/block/sda",
               "LCIOD_SD_BLOCK_DEV": "/dev/block/mmcblk0"}
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(lc, "adb", fake), \
                mock.patch.object(lc.time, "sleep"):
            rc = lc.mode_sd(self._args())
        dd_calls = [c for c in fake.calls if "dd if=" in str(c)]
        self.assertTrue(dd_calls, "mode_sd 须自驱产载 dd")
        self.assertTrue(any("mmcblk0" in str(c) for c in dd_calls))
        self.assertFalse(any("/dev/block/sda" in str(c) for c in fake.calls))
        self.assertEqual(rc, 1)  # 伪 adb 无 sysfs 属性 → 正常判红退出


# ============================================================
# SD 自愈演练闭环：--mode sd-inject 合成 STUCK 注入 + 故障面四重断言
# （TestModeSdInject）覆盖：计数递增 / 新增 sd_stuck 事件 / last_speed 反映
# 降档 / 内核留痕四判据的判绿与各破坏场景判红。CDP-DOD-001：每个判红逻辑
# 配套破坏场景红灯用例。
# ============================================================

class _FakeAdbSdInject:
    """伪 adb（sd-inject 模式）：模拟 sysfs 计数/速度、sd_stuck JSONL、
    dmesg 内核留痕与 logcat -b kernel。注入前后按 self.injected 切换态。"""

    def __init__(self, stuck_inc=True, speed_after=3, event_after=True,
                 old=4, new=3, logcat_hit=True, kmsg_hit=True,
                 inject_rc=0, sysfs_rc=0):
        self.stuck_inc = stuck_inc
        self.speed_after = speed_after
        self.event_after = event_after
        self.old = old
        self.new = new
        self.logcat_hit = logcat_hit
        self.kmsg_hit = kmsg_hit
        self.inject_rc = inject_rc
        self.sysfs_rc = sysfs_rc
        self.injected = False
        self.calls = []
        # 注入前已有 1 条 sd_stuck 记录（seq=100），注入后追加 seq=101
        self._before = [{"ts": 1, "id": 16, "level": 2, "seq": 100,
                         "mono": 1, "f": [4, 3, -5, 1]}]

    def _recs(self):
        recs = list(self._before)
        if self.injected and self.event_after:
            recs.append({"ts": 2, "id": 16, "level": 2, "seq": 101,
                         "mono": 2, "f": [self.old, self.new, -5, 2]})
        return recs

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "logcat":
            return ("X kernel_lechao_usb: sd stuck\n" if self.logcat_hit
                    else "", 0)
        if args[0] != "shell":
            return ("", 0)
        if len(args) >= 3 and args[1] == "cat":
            path = args[2]
            if path.endswith("/stuck_count"):
                if self.sysfs_rc:
                    return ("", self.sysfs_rc)
                n = 1 + (1 if self.injected and self.stuck_inc else 0)
                return (f"{n}\n", 0)
            if path.endswith("/last_speed"):
                if self.sysfs_rc:
                    return ("", self.sysfs_rc)
                return (f"{self.speed_after if self.injected else 4}\n", 0)
            body = "\n".join(json.dumps(r) for r in self._recs())
            return (body, 0)
        if len(args) >= 2 and args[1] == "ls":
            return ("/data/vendor/lechao_lcview/logs/"
                    "16_sd_stuck_20261013_p0.jsonl\n", 0)
        if len(args) >= 2 and str(args[1]).startswith("echo 1 >"):
            if self.inject_rc:
                return ("", self.inject_rc)
            self.injected = True
            return ("", 0)
        if len(args) >= 2 and args[1] == "dmesg":
            return ("kernel_lechao_usb: sd stuck\n"
                    if (self.injected and self.kmsg_hit) else "", 0)
        return ("", 0)


def _sd_inject_args(**kw):
    a = argparse.Namespace()
    a.log_since = kw.get("log_since", 123)
    return a


class TestModeSdInject(unittest.TestCase):
    def _run(self, fake, **kw):
        with mock.patch.object(lc, "adb", fake), \
                mock.patch.object(lc.time, "sleep"):
            return lc.mode_sd_inject(_sd_inject_args(**kw))

    def test_pass_all_four_assertions(self):
        # 计数递增 + 新增降档事件 + last_speed==new_speed + 内核留痕 → 判绿
        self.assertEqual(self._run(_FakeAdbSdInject()), 0)

    def test_pass_via_dmesg_fallback(self):
        # logcat -b kernel 未命中但 dmesg 留痕计数增量 → 判绿（降级兜底）
        fake = _FakeAdbSdInject(logcat_hit=False, kmsg_hit=True)
        self.assertEqual(self._run(fake), 0)

    def test_count_not_incremented_red(self):
        # 注入后 stuck_count 未增（注入未生效）→ 判红
        self.assertEqual(self._run(_FakeAdbSdInject(stuck_inc=False)), 1)

    def test_no_new_event_red(self):
        # 无新增 sd_stuck(id=16) 记录（daemon 未落盘）→ 判红
        self.assertEqual(self._run(_FakeAdbSdInject(event_after=False)), 1)

    def test_last_speed_mismatch_red(self):
        # last_speed 与事件 new_speed 不符（未反映降档）→ 判红
        self.assertEqual(self._run(_FakeAdbSdInject(speed_after=2)), 1)

    def test_no_downgrade_red(self):
        # 事件 old_speed<=new_speed（未发生降档）→ 判红
        self.assertEqual(self._run(_FakeAdbSdInject(old=3, new=3)), 1)

    def test_inject_write_fail_red(self):
        # inject_stuck 写失败（root-only 属性不可写）→ 判红（注入不可用不蒙混）
        self.assertEqual(self._run(_FakeAdbSdInject(inject_rc=1)), 1)

    def test_no_kernel_trace_red(self):
        # logcat -b kernel 与 dmesg 均无 "sd stuck" → 判红（内核留痕缺失）
        self.assertEqual(
            self._run(_FakeAdbSdInject(logcat_hit=False, kmsg_hit=False)), 1)

    def test_sysfs_unreadable_red(self):
        # stuck_count 不可读（lciod_sd 未加载）→ 判红（不得当基线 0 假绿）
        self.assertEqual(self._run(_FakeAdbSdInject(sysfs_rc=1)), 1)

    def test_inject_writes_drill_attr(self):
        # 回归锚点：注入须写 sysfs inject_stuck（root-only 触发属性）
        fake = _FakeAdbSdInject()
        self.assertEqual(self._run(fake), 0)
        self.assertTrue(any("inject_stuck" in str(c) for c in fake.calls))


# ============================================================
# QoS 延迟感知自动分级：--mode qos-auto 注入抬延迟 → 收紧/放松断言
# （TestModeQosAuto）覆盖：bg 收紧 / 新增 qos_auto_adjust(id=18) / logcat
# 留痕 / 回落放松四判据的判绿与各破坏场景判红。CDP-DOD-001：每个判红逻辑
# 配套破坏场景红灯用例。
# ============================================================

class _FakeAdbQosAuto:
    """伪 adb（qos-auto 模式）：模拟 cgroup v2 探测、限速文件、qos_auto
    JSONL 与 logcat。注入后限速文件进入 bg；停止注入（连续 3 次未注入读
    限速）后回落 off，模拟 10s 窗延迟回落放松。"""

    def __init__(self, bg_after_inject=True, relax=True, event_after=True,
                 logcat_hit=True, inject_rc=0, limit_unreadable=False):
        self.bg_after_inject = bg_after_inject
        self.relax = relax
        self.event_after = event_after
        self.logcat_hit = logcat_hit
        self.inject_rc = inject_rc
        self.limit_unreadable = limit_unreadable
        self.injected = False
        self.limit_bg = False
        self.cats_since_inject = 0
        self.calls = []
        # 注入前已有 1 条 qos_auto_adjust 记录（seq=200），注入后追加 seq=201
        self._before = [{"ts": 1, "id": 18, "level": 2, "seq": 200,
                         "mono": 1, "f": [500000, 1, 50000]}]

    def _recs(self):
        recs = list(self._before)
        if self.injected and self.event_after:
            recs.append({"ts": 2, "id": 18, "level": 2, "seq": 201,
                         "mono": 2, "f": [500000, 1, 50000]})
        return recs

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "logcat":
            return ('X lechao_lciod_event {"rule":"qos_auto","level":"bg"}\n'
                    if self.logcat_hit else "", 0)
        if args[0] != "shell":
            return ("", 0)
        if len(args) >= 2 and args[1] == "setprop":
            return ("", 0)
        if len(args) >= 3 and args[1] == "cat":
            path = args[2]
            if path.endswith("cgroup.controllers"):
                return ("cpu io memory\n", 0)
            if path.endswith("io.max") or "blkio.throttle" in path:
                if self.limit_unreadable:
                    return ("", 1)
                self.cats_since_inject += 1
                if (self.limit_bg and self.relax
                        and self.cats_since_inject >= 3):
                    self.limit_bg = False  # 延迟回落 → 放松
                if self.limit_bg:
                    return ("179:0 rbps=8388608 wbps=8388608\n", 0)
                return ("179:0 rbps=max wbps=max\n", 0)
            if "18_qos_auto_adjust" in path:
                return ("\n".join(json.dumps(r) for r in self._recs()), 0)
            return ("", 0)
        if len(args) >= 2 and args[1] == "ls":
            if len(args) >= 3 and args[2] == "-d":
                return (f"{args[3]}\n", 0)  # cgroup 组目录存在
            return ("/data/vendor/lechao_lcview/logs/"
                    "18_qos_auto_adjust_20261013_p0.jsonl\n", 0)
        if len(args) >= 2 and str(args[1]).startswith("echo 1 >"):
            if self.inject_rc:
                return ("", self.inject_rc)
            self.injected = True
            self.limit_bg = self.bg_after_inject
            self.cats_since_inject = 0
            return ("", 0)
        return ("", 0)


def _qos_auto_args(**kw):
    a = argparse.Namespace()
    a.log_since = kw.get("log_since", 123)
    a.qos_auto_lat_ms = kw.get("qos_auto_lat_ms", 50)
    a.qos_auto_rounds = kw.get("qos_auto_rounds", 8)
    a.qos_auto_sleep = kw.get("qos_auto_sleep", 2.0)
    a.qos_auto_relax_rounds = kw.get("qos_auto_relax_rounds", 10)
    a.qos_auto_relax_sleep = kw.get("qos_auto_relax_sleep", 2.0)
    return a


class TestModeQosAuto(unittest.TestCase):
    def _run(self, fake, **kw):
        with mock.patch.object(lc, "adb", fake), \
                mock.patch.object(lc.time, "sleep"):
            return lc.mode_qos_auto(_qos_auto_args(**kw))

    def test_pass_tighten_then_relax(self):
        # 注入抬延迟 → bg 收紧 + 新增 id=18 + logcat 留痕 → 回落放松 → 判绿
        self.assertEqual(self._run(_FakeAdbQosAuto()), 0)

    def test_no_cgroup_red(self):
        # cgroup 既非 v2 也无 blkio → 判红（无反馈面不得假绿）
        fake = _FakeAdbQosAuto()

        def _no_io(args, timeout=60):
            fake.calls.append(args)
            if args[0] == "shell" and len(args) >= 3 and args[1] == "cat" \
                    and args[2].endswith("cgroup.controllers"):
                return ("cpu memory\n", 0)
            if args[0] == "shell" and len(args) >= 2 and args[1] == "ls":
                return ("", 1)
            return ("", 0)

        with mock.patch.object(lc, "adb", _no_io), \
                mock.patch.object(lc.time, "sleep"):
            self.assertEqual(lc.mode_qos_auto(_qos_auto_args()), 1)

    def test_group_missing_red(self):
        # lechao_bg 组目录缺失（daemon 未建组）→ 判红
        fake = _FakeAdbQosAuto()

        def _no_group(args, timeout=60):
            fake.calls.append(args)
            if args[0] == "shell" and len(args) >= 3 and args[1] == "cat" \
                    and args[2].endswith("cgroup.controllers"):
                return ("cpu io memory\n", 0)
            if args[0] == "shell" and len(args) >= 3 and args[1] == "ls" \
                    and args[2] == "-d":
                return ("", 1)
            return ("", 0)

        with mock.patch.object(lc, "adb", _no_group), \
                mock.patch.object(lc.time, "sleep"):
            self.assertEqual(lc.mode_qos_auto(_qos_auto_args()), 1)

    def test_not_tightened_red(self):
        # 注入后限速文件未进入 bg（自动档未收紧）→ 判红
        self.assertEqual(self._run(_FakeAdbQosAuto(bg_after_inject=False)), 1)

    def test_no_new_event_red(self):
        # 无新增 qos_auto_adjust(id=18) 记录 → 判红
        self.assertEqual(self._run(_FakeAdbQosAuto(event_after=False)), 1)

    def test_no_logcat_trace_red(self):
        # logcat 无 "rule":"qos_auto" 行（自动档留痕缺失）→ 判红
        self.assertEqual(self._run(_FakeAdbQosAuto(logcat_hit=False)), 1)

    def test_not_relaxed_red(self):
        # 停止注入后仍为 bg（延迟回落未放松）→ 判红
        self.assertEqual(self._run(_FakeAdbQosAuto(relax=False)), 1)

    def test_inject_write_fail_red(self):
        # inject_latency 写失败（root-only 属性不可写）→ 判红
        self.assertEqual(self._run(_FakeAdbQosAuto(inject_rc=1)), 1)

    def test_limit_file_unreadable_red(self):
        # 限速文件不可读 → 判红（不得当放松假绿）
        self.assertEqual(self._run(_FakeAdbQosAuto(limit_unreadable=True)), 1)

    def test_inject_writes_latency_attr(self):
        # 回归锚点：注入须写 sysfs inject_latency（root-only 触发属性）
        fake = _FakeAdbQosAuto()
        self.assertEqual(self._run(fake), 0)
        self.assertTrue(any("inject_latency" in str(c) for c in fake.calls))

    def test_sets_auto_prop_and_teardown_clears(self):
        # 回归锚点：须 setprop ioqos_auto=1 且 teardown 清空自动档属性
        fake = _FakeAdbQosAuto()
        self.assertEqual(self._run(fake), 0)
        auto_calls = [c for c in fake.calls
                      if len(c) >= 2 and c[1] == "setprop"
                      and c[2] == "sys.lechao.lciod.ioqos_auto"]
        self.assertTrue(any(c[3] == "1" for c in auto_calls))
        self.assertTrue(any(c[3] == "" for c in auto_calls))


# ============================================================
# 进程级退出码透传回归（方向 1）：perf/storm/qos/sd/histo 模式内部用 return
# 返回 0/1，入口若只写 main() 会丢弃返回值、进程 rc 恒 0（判红模式假绿）。
# 本用例以子进程真实执行脚本 __main__ 入口（runpy run_name=__main__，不调
# main() 自身——避免函数级调用充数），用假 ws_adb_connect 让 perf 模式走到
# "probe 无设备"陈旧失败分支返回 1，断言进程 rc 非零。
# ============================================================
class TestProcessExitCodePassthrough(unittest.TestCase):
    def test_main_entry_propagates_mode_failure_rc(self):
        import subprocess
        import textwrap
        entry = Path(__file__).resolve().parents[1] / "cases" / "lciod_check.py"
        # 假 adb 层注入：ws_connected 返回端点、run_adb 对 root 返 already
        # running（ensure_connected 快路径）、其余返空成功——perf 的 probe
        # 输出为空 → validate_devices 判红 → mode_perf return 1。
        code = textwrap.dedent(
            "import sys, types, runpy\n"
            "fake = types.ModuleType('ws_adb_connect')\n"
            "fake.ensure_connected = lambda *a, **k: '127.0.0.1:5555'\n"
            "fake.host_port = lambda: '127.0.0.1:5555'\n"
            "def _run_adb(args, timeout=60):\n"
            "    if 'root' in args:\n"
            "        return ('already running', 0)\n"
            "    return ('', 0)\n"
            "fake.run_adb = _run_adb\n"
            "sys.modules['ws_adb_connect'] = fake\n"
            "sys.argv = ['lciod_check.py', '--mode', 'perf']\n"
            f"runpy.run_path({str(entry)!r}, run_name='__main__')\n"
        )
        proc = subprocess.run([sys.executable, "-c", code],
                              capture_output=True, text=True,
                              encoding="utf-8", timeout=60)
        # mode_perf 干净返回 1（非异常）：stdout 含其拒绝采集的 ERROR 行
        # （脚本用 print 输出，非 stderr）
        self.assertIn("probe 快照校验未通过", proc.stdout)
        self.assertEqual(proc.returncode, 1,
                         "perf 模式失败须透传进程 rc=1，入口丢弃 main 返回值即假绿")


# ============================================================
# zram 启用与换入压力观测闭环：--mode zram 三段断言
# （TestModeZram）覆盖：swap 生效 / "rule":"zram" 采集留痕 /
# "rule":"zram_swap_pressure" 降阈+合成换入触发 的判绿与各破坏场景判红。
# CDP-DOD-001：每个判红逻辑配套破坏场景红灯用例。
# ============================================================

class _FakeAdbZram:
    """伪 adb（zram 模式）：模拟 /proc/swaps、zram0 disksize、daemon logcat
    留痕与 setprop 降阈/演练注入。drill 注入后 logcat 出现换入压力事件。"""

    def __init__(self, swap_active=True, swap_readable=True, zram_trace=True,
                 pressure_after=True, setprop_fail=False, timeout=False):
        self.swap_active = swap_active
        self.swap_readable = swap_readable
        self.zram_trace = zram_trace
        self.pressure_after = pressure_after
        self.setprop_fail = setprop_fail
        self.timeout = timeout
        self.drilled = False
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "logcat":
            lines = []
            if self.zram_trace:
                lines.append('X lechao_lciod_event '
                             '{"rule":"zram","mem_used_total":0}')
            if self.drilled and self.pressure_after:
                lines.append('X lechao_lciod_event '
                             '{"rule":"zram_swap_pressure","swapin_pages":0}')
            return ("\n".join(lines) + "\n", 0)
        if args[0] != "shell":
            return ("", 0)
        if len(args) >= 2 and args[1] == "setprop":
            if self.setprop_fail and args[2] == lc._ZRAM_THRESHOLD_PROP:
                return ("", 1)
            # drill 置非空值视为注入（"" 为 teardown 清空）
            if args[2] == lc._ZRAM_DRILL_PROP and args[3] != "":
                self.drilled = True
            return ("", 0)
        if len(args) >= 3 and args[1] == "cat":
            path = args[2]
            if path == lc._ZRAM_SWAP_FILE:
                if self.timeout:
                    return ("", -1)
                if not self.swap_readable:
                    return ("", 1)
                body = ("Filename\t\t\t\tType\t\tSize\tUsed\tPriority\n")
                if self.swap_active:
                    body += ("/dev/block/zram0                          "
                             "partition\t4131389440\t0\t-2\n")
                return (body, 0)
            if path.endswith("disksize"):
                return ("4131389440\n" if self.swap_active else "0\n", 0)
            return ("", 0)
        return ("", 0)


def _zram_args(**kw):
    a = argparse.Namespace()
    a.log_since = kw.get("log_since", 123)
    a.zram_drill_pages = kw.get("zram_drill_pages", 64)
    a.zram_threshold_pages = kw.get("zram_threshold_pages", 1)
    a.zram_rounds = kw.get("zram_rounds", 3)
    a.zram_sleep = kw.get("zram_sleep", 0.0)
    return a


class TestModeZram(unittest.TestCase):
    def _run(self, fake, **kw):
        with mock.patch.object(lc, "adb", fake), \
                mock.patch.object(lc.time, "sleep"):
            return lc.mode_zram(_zram_args(**kw))

    def test_pass(self):
        # swap 生效 + 采集留痕 + 降阈注入触发换入压力事件 → 判绿
        self.assertEqual(self._run(_FakeAdbZram()), 0)

    def test_swap_inactive_red(self):
        # /proc/swaps 无 zram0（init 未生效）→ 判红
        self.assertEqual(self._run(_FakeAdbZram(swap_active=False)), 1)

    def test_swap_unreadable_red(self):
        # /proc/swaps 不可读 → 判红（不得当未启用假绿）
        self.assertEqual(self._run(_FakeAdbZram(swap_readable=False)), 1)

    def test_no_zram_trace_red(self):
        # logcat 无 "rule":"zram"（采集未留痕）→ 判红
        self.assertEqual(self._run(_FakeAdbZram(zram_trace=False)), 1)

    def test_no_pressure_event_red(self):
        # 降阈+合成换入后仍无 "rule":"zram_swap_pressure" → 判红
        self.assertEqual(self._run(_FakeAdbZram(pressure_after=False)), 1)

    def test_setprop_fail_red(self):
        # 降阈 setprop 失败 → 判红
        self.assertEqual(self._run(_FakeAdbZram(setprop_fail=True)), 1)

    def test_adb_timeout_returns_minus1(self):
        # adb 超时（rc==-1）→ 返回 -1 由 main 透传失败
        self.assertEqual(self._run(_FakeAdbZram(timeout=True)), -1)

    def test_lowered_threshold_and_drill_set(self):
        # 回归锚点：须 setprop 降阈 + 合成换入页（OOM-safe 确定性触发）
        fake = _FakeAdbZram()
        self.assertEqual(self._run(fake), 0)
        sets = [(c[2], c[3]) for c in fake.calls
                if len(c) >= 4 and c[1] == "setprop"]
        self.assertIn((lc._ZRAM_THRESHOLD_PROP, "1"), sets)
        self.assertIn((lc._ZRAM_DRILL_PROP, "64"), sets)

    def test_teardown_clears_props(self):
        # 回归锚点：teardown 清空降阈/演练 sysprop
        fake = _FakeAdbZram()
        self.assertEqual(self._run(fake), 0)
        clears = [c for c in fake.calls
                  if len(c) >= 4 and c[1] == "setprop" and c[3] == ""]
        cleared = {c[2] for c in clears}
        self.assertIn(lc._ZRAM_THRESHOLD_PROP, cleared)
        self.assertIn(lc._ZRAM_DRILL_PROP, cleared)


class _FakeAdbZramWb:
    """伪 adb（zram-writeback 模式）：模拟 U 盘枚举、backing_dev 绑定/回读、
    bd_stat 前后差分与 writeback 触发。状态机：writeback 触发后 bd_stat 递增。"""

    def __init__(self, usb=True, bind_ok=True, backing_readback=None,
                 increment=True, bd_readable=True, timeout=False):
        self.usb = usb
        self.bind_ok = bind_ok
        # None → 绑定时回读 /dev/block/sdb；否则返回给定值（如 none）
        self.backing_readback = backing_readback
        self.increment = increment
        self.bd_readable = bd_readable
        self.timeout = timeout
        self.written_back = False
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if self.timeout:
            return ("", -1)
        if args[0] != "shell" or len(args) < 2:
            return ("", 0)
        if args[1] == "cat":
            path = args[2]
            if path == lc._ZRAM_WB_DISKSIZE:
                return ("67108864\n", 0)
            if path == lc._ZRAM_WB_BACKING:
                if self.backing_readback is not None:
                    return (self.backing_readback + "\n", 0)
                return ("/dev/block/sdb\n", 0)
            if path == lc._ZRAM_WB_BD_STAT:
                if not self.bd_readable:
                    return ("", 1)
                return ("16 0 16\n" if self.written_back else "0 0 0\n", 0)
            return ("", 0)
        cmd = args[1]
        if "for d in /sys/block/sd*" in cmd:
            return ("sdb 1\n" if self.usb else "", 0)
        if "> /sys/block/zram0/backing_dev" in cmd:
            return ("", 0 if self.bind_ok else 1)
        if "> /sys/block/zram0/writeback" in cmd:
            self.written_back = self.increment
            return ("", 0)
        return ("", 0)


def _zram_wb_args(**kw):
    a = argparse.Namespace()
    a.zram_wb_pages = kw.get("zram_wb_pages", 16)
    a.dd_timeout = kw.get("dd_timeout", 300)
    return a


class TestModeZramWriteback(unittest.TestCase):
    def _run(self, fake, **kw):
        with mock.patch.object(lc, "adb", fake):
            return lc.mode_zram_writeback(_zram_wb_args(**kw))

    def test_pass(self):
        # U 盘在位 + backing_dev 绑定 + bd_stat 递增 → 判绿
        self.assertEqual(self._run(_FakeAdbZramWb()), 0)

    def test_no_usb_skips_exit_zero(self):
        # 无 removable USB 块设备 → SKIP exit 0（验收层转 skip 不判红）
        self.assertEqual(self._run(_FakeAdbZramWb(usb=False)), 0)

    def test_bind_fail_red(self):
        # backing_dev 写失败 → 判红
        self.assertEqual(self._run(_FakeAdbZramWb(bind_ok=False)), 1)

    def test_bind_mismatch_red(self):
        # backing_dev 回读未指向 U 盘 → 判红
        self.assertEqual(self._run(_FakeAdbZramWb(backing_readback="none")), 1)

    def test_no_increment_red(self):
        # writeback 后 bd_stat 未递增 → 判红（防假绿）
        self.assertEqual(self._run(_FakeAdbZramWb(increment=False)), 1)

    def test_bd_stat_unreadable_red(self):
        # bd_stat 不可读 → 判红（不得当无写回假绿）
        self.assertEqual(self._run(_FakeAdbZramWb(bd_readable=False)), 1)

    def test_adb_timeout_returns_minus1(self):
        # adb 超时（rc==-1）→ 返回 -1 由 main 透传失败
        self.assertEqual(self._run(_FakeAdbZramWb(timeout=True)), -1)

    def test_binds_before_disksize(self):
        # 回归锚点：backing_dev 写须在 disksize 写之前（内核 init_done 约束）
        fake = _FakeAdbZramWb()
        self.assertEqual(self._run(fake), 0)
        cmds = [c[1] for c in fake.calls if len(c) >= 2 and c[0] == "shell"]
        bind_idx = next(i for i, c in enumerate(cmds)
                        if c.startswith("echo") and "backing_dev" in c)
        ds_idx = next(i for i, c in enumerate(cmds)
                      if c.startswith("echo 67108864 >"))
        self.assertLess(bind_idx, ds_idx)

    def test_restores_swap(self):
        # 回归锚点：收尾 mkswap + swapon 复原 swap
        fake = _FakeAdbZramWb()
        self.assertEqual(self._run(fake), 0)
        joined = " ".join(c[1] for c in fake.calls
                          if len(c) >= 2 and c[0] == "shell")
        self.assertIn("mkswap", joined)
        self.assertIn("swapon", joined)


class _FakeAdbReadahead:
    """伪 adb（readahead 模式）：模拟 sysfs 读取与 drop_caches/am start。"""

    def __init__(self, sysfs="512", cat_rc=0, am_rc=0,
                 am_out="Status: ok\nLaunchState: WARM\nTotalTime: 700\n"
                        "WaitTime: 710\n", timeout=False):
        self.sysfs = sysfs
        self.cat_rc = cat_rc
        self.am_rc = am_rc
        self.am_out = am_out
        self.timeout = timeout
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if self.timeout:
            return ("", -1)
        if args[0] == "shell" and len(args) >= 2:
            if args[1] == "cat":
                return (f"{self.sysfs}\n", self.cat_rc)
            if "drop_caches" in args[1]:
                return ("", 0)
            if args[1] == "am":
                return (self.am_out, self.am_rc)
        return ("", 0)


def _readahead_args():
    return argparse.Namespace()


class TestModeReadahead(unittest.TestCase):
    def _run(self, fake):
        with mock.patch.object(lc, "adb", fake):
            return lc.mode_readahead(_readahead_args())

    def test_pass(self):
        # sysfs=512 + am start 可解析 → 判绿
        self.assertEqual(self._run(_FakeAdbReadahead()), 0)

    def test_non_target_red(self):
        # sysfs 未落到目标态 512 → 判红
        self.assertEqual(self._run(_FakeAdbReadahead(sysfs="128")), 1)

    def test_unreadable_red(self):
        # sysfs 不可读 → 判红（不得当默认态假绿）
        self.assertEqual(self._run(_FakeAdbReadahead(cat_rc=1)), 1)

    def test_am_fail_red(self):
        # 冷启动测量失败 → 判红（不得静默缺证据）
        self.assertEqual(self._run(_FakeAdbReadahead(am_rc=1)), 1)

    def test_am_no_time_red(self):
        # am 输出缺 TotalTime/WaitTime → 判红（不得当 0ms 假绿）
        self.assertEqual(
            self._run(_FakeAdbReadahead(am_out="Status: ok\n")), 1)

    def test_adb_timeout_returns_minus1(self):
        # adb 超时（rc==-1）→ 返回 -1 由 main 透传失败
        self.assertEqual(self._run(_FakeAdbReadahead(timeout=True)), -1)

    def test_no_restore_write(self):
        # 回归锚点：默认即目标态，不得回写 sysfs（回退由 setprop trigger 驱动）
        fake = _FakeAdbReadahead()
        self.assertEqual(self._run(fake), 0)
        writes = [c for c in fake.calls
                  if len(c) >= 2 and c[0] == "shell"
                  and "read_ahead_kb" in c[1] and "cat" not in c[1]]
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()