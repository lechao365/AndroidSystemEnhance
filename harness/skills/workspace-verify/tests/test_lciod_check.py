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
# 与 ioctl.h v4 ABI 对齐：read/write_error_count + abi_version=4）
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
    'abi_version=4'
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
        self.assertEqual(dev["abi_version"], "4")
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
        line = VALID_LINE.replace("abi_version=4", "abi_version=2")
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
    'last_err=0 last_count=1 last_duration_ns=500000 abi_version=4'
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
        self.assertEqual(fields["abi_version"], "4")

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
        # ABI 漂移（3 != 4）判红
        line = VALID_LINK_LINE.replace("abi_version=4", "abi_version=3")
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
                 std_blkio=False, dev_blkio=False):
        self.controllers = controllers
        self.group_rc = group_rc
        self.limit = limit
        self.limit_rc = limit_rc
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
                return (self.limit, self.limit_rc)  # io.max / blkio 限速文件
            if args[1] == "ls":
                if "/lechao_bg" in args[-1]:
                    return (args[-1] + "\n", self.group_rc)
                if "/sys/fs/cgroup/blkio" in args[-1]:
                    return (args[-1] + "\n", 0) if self.std_blkio else ("", 1)
                if "/dev/blkio" in args[-1]:
                    return (args[-1] + "\n", 0) if self.dev_blkio else ("", 1)
                return (args[-1] + "\n", self.group_rc)
            if args[1] == "setprop":
                return ("", 0)
            if args[1] == "rm":
                return ("", 0)
            if args[1] == "sh":
                shell_cmd = args[3]
                return (self.in_dd if "lciod_qos_in_" in shell_cmd
                        else self.out_dd, self.dd_rc)
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

    def test_qos_limit_file_empty_red(self):
        # 限速文件为空/不可读（cgroup 未配置限速）→ 判红
        fake = FakeAdbQos(limit="", limit_rc=1)
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


if __name__ == "__main__":
    unittest.main()
