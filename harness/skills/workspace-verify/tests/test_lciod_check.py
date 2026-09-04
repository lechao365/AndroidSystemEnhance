# ============================================================
# test_lciod_check.py — lciod_check 纯函数级测试
# 所属模块：workspace-verify — 验证用例测试
# 覆盖：probe 输出解析（引号值/残缺行）、stats 校验判红项全集、
#       基线读写、delta 增量对比（未增/缺基线/缺字段均判红）。
#       不依赖设备（adb 主流程由板上用例实测兜底）。
# ============================================================

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

# 与 lciod_probe.c 输出同构的合法单行样本（vendor 含空格验证引号解析）
VALID_LINE = (
    'device minor=0 path=/dev/vendor_lechao_usbd0 vid=0x04e8 pid=0x6344 '
    'vendor="SanDisk Corp" product="Ultra USB 3.0" '
    'read_bytes=4194304 write_bytes=1048576 read_ns=500000000 write_ns=200000000 '
    'read_cmds=64 write_cmds=16 error_count=0 reset_count=0 '
    'probe_count=1 disconnect_count=0 degrade_count=0 '
    'current_rate=0 peak_rate=8388608 last_transport_latency_ns=1200000 '
    'last_event_ts_ns=987654321 last_update=111222333 stall_count=0 '
    'corrupt_count=0 timeout_count=0 last_event_type=5 '
    'enabled=1 flags=0 event_drop_count=0 abi_version=2'
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
        self.assertEqual(dev["abi_version"], "2")
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
        line = VALID_LINE.replace("abi_version=2", "abi_version=3")
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


if __name__ == "__main__":
    unittest.main()
