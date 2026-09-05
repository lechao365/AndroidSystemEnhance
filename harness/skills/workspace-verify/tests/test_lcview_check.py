import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cases"))
import lcview_check as lc

LOGS_DIR = lc.LOGS_DIR


class TestDefaultBaseline(unittest.TestCase):
    """方向 4：基线文件按轮次隔离——LCVIEW_BASELINE_FILE 环境变量覆盖默认路径。"""

    def test_env_overrides_default(self):
        with mock.patch.dict(os.environ,
                             {"LCVIEW_BASELINE_FILE": "/tmp/lcview_baseline_r1.json"}):
            self.assertEqual(lc._default_baseline(),
                             "/tmp/lcview_baseline_r1.json")

    def test_unset_falls_back(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(lc._default_baseline(), "/tmp/lcview_baseline.json")


class TestEnsureConnected(unittest.TestCase):
    """方向 3：ensure_connected 的 root already-running 快路径（对齐
    ws_upload_tests ensure_user）——adbd 已在 root 时跳过 sleep+重连。"""

    def _ep(self):
        ep = {"v": "10.0.0.5:5555"}
        return ep["v"]

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


class TestWrapperThin(unittest.TestCase):
    """方向 2：wrapper 瘦身为纯 exec python3——connect/root/sleep 已由 py 内
    ensure_connected 承担（含 root already-running 快路径），wrapper 不再
    重复（重复会多耗一次 root 重连）。"""

    def _sh(self, py_path):
        return Path(py_path).with_suffix(".sh").read_text(encoding="utf-8")

    def test_lcview_wrapper_is_pure_exec(self):
        sh = self._sh(lc.__file__)
        self.assertIn("exec python3", sh)
        self.assertNotIn("adb -s", sh)
        self.assertNotIn("sleep 2", sh)

    def test_lciod_wrapper_is_pure_exec(self):
        import importlib
        lci = importlib.import_module("lciod_check")
        sh = self._sh(lci.__file__)
        self.assertIn("exec python3", sh)
        self.assertNotIn("adb -s", sh)
        self.assertNotIn("sleep 2", sh)


def _args(**kw):
    a = argparse.Namespace()
    a.window = kw.get("window", 600)
    a.skew = kw.get("skew", 600)
    a.event = kw.get("event", None)
    a.vid = kw.get("vid", None)
    a.pid = kw.get("pid", None)
    a.baseline = kw.get("baseline", str(Path(tempfile.gettempdir()) / "lcview_baseline_test.json"))
    a.in_flight = kw.get("in_flight", 16)
    a.conserve_sample_s = kw.get("conserve_sample_s", 5)
    a.conserve_load_mb = kw.get("conserve_load_mb", 0)
    a.load_mb = kw.get("load_mb", 64)
    a.block_dev = kw.get("block_dev", "/dev/block/sda")
    a.perf_timeout = kw.get("perf_timeout", 60)
    a.perf_sample_ms = kw.get("perf_sample_ms", 100)
    a.dd_timeout = kw.get("dd_timeout", 300)
    return a


class FakeAdb:
    """伪 adb：files 为 {remote: content}，各子命令可注入 rc 模拟失败/超时。

    shell ls 输出文件基名；pull 按 remote 取内容写本地文件；stat 返回注入
    stdout 与 rc；date 返回注入 stdout 与 rc；logcat/dd/pidof/cat 同款注入。
    """

    def __init__(self, files=None, ls_rc=0, pull_rc=0, stat_rc=0, stat_out="",
                 date_rc=0, date_out="", logcat_rc=0, logcat_out="",
                 dd_rc=0, dd_out="", pidof_rc=0, pidof_out="",
                 proc_rc=0, proc_out="", stats_rc=0, stats_out="",
                 sysfs_rc=0, sysfs_out="", sysfs_seq=None,
                 wc_rc=0, wc_out="", wc_seq=None):
        self.files = dict(files or {})
        self.ls_rc = ls_rc
        self.pull_rc = pull_rc
        self.stat_rc = stat_rc
        self.stat_out = stat_out
        self.date_rc = date_rc
        self.date_out = date_out
        self.logcat_rc = logcat_rc
        self.logcat_out = logcat_out
        self.dd_rc = dd_rc
        self.dd_out = dd_out
        self.pidof_rc = pidof_rc
        self.pidof_out = pidof_out
        self.proc_rc = proc_rc
        self.proc_out = proc_out
        self.stats_rc = stats_rc
        self.stats_out = stats_out
        self.sysfs_rc = sysfs_rc
        self.sysfs_out = sysfs_out
        self.sysfs_seq = list(sysfs_seq or [])
        self.wc_rc = wc_rc
        self.wc_out = wc_out
        self.wc_seq = list(wc_seq or [])
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "logcat":
            return (self.logcat_out, self.logcat_rc)
        if args[0] == "shell":
            cmd = args[1]
            if cmd.startswith("ls "):
                return (" ".join(Path(r).name for r in self.files), self.ls_rc)
            if cmd.startswith("stat "):
                return (self.stat_out, self.stat_rc)
            if cmd.startswith("date "):
                return (self.date_out, self.date_rc)
            if cmd.startswith("dd if="):
                return (self.dd_out, self.dd_rc)
            if cmd.startswith("pidof "):
                return (self.pidof_out, self.pidof_rc)
            if cmd.startswith("cat /proc/"):
                return (self.proc_out, self.proc_rc)
            if cmd.startswith("lcview_stats"):
                return (self.stats_out, self.stats_rc)
            if cmd.startswith("cat /sys/class/"):
                if self.sysfs_seq:
                    return self.sysfs_seq.pop(0)
                return (self.sysfs_out, self.sysfs_rc)
            if cmd.startswith("wc -l "):
                if self.wc_seq:
                    return self.wc_seq.pop(0)
                return (self.wc_out, self.wc_rc)
            return ("", 0)
        if args[0] == "pull":
            remote, local = args[1], args[2]
            if self.pull_rc != 0:
                return ("", self.pull_rc)
            if remote == LOGS_DIR:
                # 目录 pull（方向 1）：一次拉 logs 下全部文件到 local/ 下
                Path(local).mkdir(parents=True, exist_ok=True)
                for rpath, content in self.files.items():
                    if rpath.startswith(LOGS_DIR + "/"):
                        Path(local, Path(rpath).name).write_text(
                            content, encoding="utf-8")
                return ("", 0)
            Path(local).write_text(self.files.get(remote, ""), encoding="utf-8")
            return ("", self.pull_rc)
        return ("", 0)


def _jsonl(*lines):
    return "\n".join(lines) + "\n"


class TestPullLogs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_ls_timeout_propagates_minus1(self):
        # ls adb 超时（rc=-1）→ 透传 -1，不得当"无日志文件"
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"}, ls_rc=-1)
        with mock.patch.object(lc, "adb", fake):
            self.assertEqual(lc.pull_logs(self._tmp.name), -1)

    def test_pull_failure_propagates_minus1(self):
        # pull 失败不得静默跳过（拉不全的"全部"不可信）→ -1
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}",
                              f"{LOGS_DIR}/b.jsonl": "{}"},
                       pull_rc=1)
        with mock.patch.object(lc, "adb", fake):
            self.assertEqual(lc.pull_logs(self._tmp.name), -1)

    def test_pull_all_success(self):
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "x",
                              f"{LOGS_DIR}/b.jsonl": "y"})
        with mock.patch.object(lc, "adb", fake):
            pulled = lc.pull_logs(self._tmp.name)
        self.assertEqual(len(pulled), 2)
        for p in pulled:
            self.assertTrue(Path(p).is_file())

    def test_single_dir_pull(self):
        # 方向 1：逐文件 pull 改单进程目录 pull——只发一次 pull 且目标为
        # logs 目录（files/valid_json/schema/baseline/ts 五项各约 9.5s 皆因
        # 逐文件全量拉取）；ls 预检与 -1 透传保留（由本类前两用例覆盖）
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "x",
                              f"{LOGS_DIR}/b.jsonl": "y"})
        with mock.patch.object(lc, "adb", fake):
            pulled = lc.pull_logs(self._tmp.name)
        pulls = [c for c in fake.calls if c[0] == "pull"]
        self.assertEqual(len(pulls), 1, "目录 pull 应只发一次 pull")
        self.assertEqual(pulls[0][1], LOGS_DIR)
        self.assertEqual(len(pulled), 2)


class TestModeFiles(unittest.TestCase):
    def test_has_nonempty_jsonl_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_files(tmp, _args())
        self.assertEqual(rc, 0)

    def test_no_jsonl_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_files(tmp, _args())
        self.assertEqual(rc, 1)

    def test_timeout_propagates_minus1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"}, ls_rc=-1)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_files(tmp, _args())
        self.assertEqual(rc, -1)


class TestModeValidJson(unittest.TestCase):
    def test_all_valid_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl":
                                  _jsonl('{"ts": 1, "id": 8, "f": []}')})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_valid_json(tmp, _args())
        self.assertEqual(rc, 0)

    def test_bad_line_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "not-json\n"})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_valid_json(tmp, _args())
        self.assertEqual(rc, 1)

    def test_zero_records_fails_not_fake_pass(self):
        # 零记录 ≠ 合法零坏行：无数据可校验须判红
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": ""})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_valid_json(tmp, _args())
        self.assertEqual(rc, 1)

    def test_timeout_propagates_minus1(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"}, ls_rc=-1)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_valid_json(tmp, _args())
        self.assertEqual(rc, -1)


class TestModeSchema(unittest.TestCase):
    SCHEMA = json.dumps({"events": [{"id": 8, "fields": ["a", "b", "c"]}]})

    def test_match_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={
                f"{LOGS_DIR}/a.jsonl":
                    _jsonl('{"ts": 1, "id": 8, "f": [0, 1, 2]}'),
                lc.SCHEMA_REMOTE: self.SCHEMA,
            })
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_schema(tmp, _args())
        self.assertEqual(rc, 0)

    def test_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={
                f"{LOGS_DIR}/a.jsonl":
                    _jsonl('{"ts": 1, "id": 9, "f": [0]}'),
                lc.SCHEMA_REMOTE: self.SCHEMA,
            })
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_schema(tmp, _args())
        self.assertEqual(rc, 1)

    def test_zero_records_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "",
                                  lc.SCHEMA_REMOTE: self.SCHEMA})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_schema(tmp, _args())
        self.assertEqual(rc, 1)

    def test_schema_pull_failure_fails(self):
        # schema pull 失败 → 1；若 logs pull 先失败则透传 -1（不得假绿）
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"},
                           pull_rc=1)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_schema(tmp, _args())
        self.assertEqual(rc, -1)


class TestModeInvalid(unittest.TestCase):
    def test_empty_log_passes(self):
        fake = FakeAdb(files={}, stat_out="0\n", stat_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_invalid(tmp, _args())
        self.assertEqual(rc, 0)

    def test_nonempty_log_fails(self):
        fake = FakeAdb(files={}, stat_out="42\n", stat_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_invalid(tmp, _args())
        self.assertEqual(rc, 1)

    def test_stat_failure_fails_not_fake_pass(self):
        # 方向 3：stat rc 非 0（目录不存在等）→ 判红，不得"视为空通过"
        fake = FakeAdb(files={}, stat_out="", stat_rc=2)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_invalid(tmp, _args())
        self.assertEqual(rc, 1)

    def test_size_not_numeric_fails(self):
        # 方向 3：size 非数字 → 判红（无法确认坏记录状态）
        fake = FakeAdb(files={}, stat_out="unknown\n", stat_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_invalid(tmp, _args())
        self.assertEqual(rc, 1)

    def test_timeout_propagates_minus1(self):
        fake = FakeAdb(files={}, stat_rc=-1)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_invalid(tmp, _args())
        self.assertEqual(rc, -1)


class TestModeFresh(unittest.TestCase):
    def test_fresh_mtime_passes(self):
        import time
        now = int(time.time())
        fake = FakeAdb(files={}, stat_out=f"{now} a.jsonl\n", stat_rc=0,
                       date_out=f"{now}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_fresh(tmp, _args())
        self.assertEqual(rc, 0)

    def test_stale_mtime_fails(self):
        import time
        now = int(time.time())
        fake = FakeAdb(files={}, stat_out=f"{now - 3600} a.jsonl\n",
                       stat_rc=0, date_out=f"{now}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_fresh(tmp, _args())
        self.assertEqual(rc, 1)

    def test_stat_timeout_propagates_minus1(self):
        fake = FakeAdb(files={}, stat_rc=-1, date_out="0\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_fresh(tmp, _args())
        self.assertEqual(rc, -1)


class TestModeTs(unittest.TestCase):
    def test_skew_within_window_passes(self):
        import time
        now_ns = int(time.time()) * 10**9
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl":
                              _jsonl(f'{{"ts": {now_ns}, "id": 8, "f": []}}')},
                       date_out=f"{int(time.time())}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, _args())
        self.assertEqual(rc, 0)

    def test_zero_records_fails(self):
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": ""},
                       date_out="0\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, _args())
        self.assertEqual(rc, 1)

    def test_future_records_filtered_fails(self):
        # 方向 1：未来记录被滤条数非 0 即判红（滤阈=判红阈时余下恒过的架空）
        import time
        now = int(time.time())
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
            f'{{"ts": {(now + 28800) * 10**9}, "id": 8, "f": []}}')},
            date_out=f"{now}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, _args(skew=600))
        self.assertEqual(rc, 1)

    def test_baseline_limits_to_new_records(self):
        # 方向 2：显式 --baseline 时只判基线后新记录（历史未来记录不判红）；
        # 501 条校准前记录场景下 trigger 的 ts 可过
        import time
        now = int(time.time())
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            base.write_text(json.dumps({"max_ts": now * 10**9}),
                            encoding="utf-8")
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
                f'{{"ts": {(now + 28800) * 10**9}, "id": 8, "f": []}}\n'
                f'{{"ts": {(now + 5) * 10**9}, "id": 8, "f": []}}')},
                date_out=f"{now}\n", date_rc=0)
            a = _args(skew=600)
            a.baseline = str(base)
            a.baseline_explicit = True
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, a)
        self.assertEqual(rc, 0)

    def test_baseline_explicit_required(self):
        # 未显式传 --baseline（warn 卫生检查）→ 判全历史，未来记录判红
        import time
        now = int(time.time())
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
            f'{{"ts": {(now + 28800) * 10**9}, "id": 8, "f": []}}')},
            date_out=f"{now}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            a = _args(skew=600)
            a.baseline_explicit = False
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, a)
        self.assertEqual(rc, 1)

    def test_baseline_missing_fails(self):
        # 显式 baseline 但文件缺失 → 判红（不降级全历史）
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"},
                       date_out="0\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            a = _args(skew=600)
            a.baseline = str(Path(tmp) / "nope.json")
            a.baseline_explicit = True
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, a)
        self.assertEqual(rc, 1)

    def test_timeout_propagates_minus1(self):
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"}, ls_rc=-1,
                       date_out="0\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_ts(tmp, _args())
        self.assertEqual(rc, -1)


class TestModeBaseline(unittest.TestCase):
    def test_writes_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl":
                                  _jsonl('{"ts": 1000, "id": 8, "f": []}')},
                           date_out=f"{int(__import__('time').time())}\n",
                           date_rc=0)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_baseline(tmp, _args(baseline=str(base)))
            self.assertEqual(rc, 0)
            data = json.loads(base.read_text(encoding="utf-8"))
        self.assertEqual(data["max_ts"], 1000)
        self.assertEqual(data["line_count"], {"a.jsonl": 1})

    def test_future_ts_excluded_after_clock_rollback(self):
        # 时钟校准回拨（PIT-5）后：校准前写入的未来 ts 不得污染基线 max_ts
        import time
        now = int(time.time())
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
            f'{{"ts": {(now + 25000) * 10**9}, "id": 8, "f": []}}\n'
            f'{{"ts": {now * 10**9}, "id": 8, "f": []}}')},
            date_out=f"{now}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_baseline(tmp, _args(baseline=str(base)))
            self.assertEqual(rc, 0)
            data = json.loads(base.read_text(encoding="utf-8"))
        self.assertEqual(data["max_ts"], now * 10**9)

    def test_all_future_ts_refuses_baseline(self):
        # 方向 1：全部记录被滤空（仅剩时钟回拨前旧记录）→ 拒写基线返 1，
        # max_ts 不得落 0（否则 delta 把历史全当新增假绿，绕过零文件护栏）
        import time
        now = int(time.time())
        fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
            f'{{"ts": {(now + 25000) * 10**9}, "id": 8, "f": []}}')},
            date_out=f"{now}\n", date_rc=0)
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_baseline(tmp, _args(baseline=str(base)))
            self.assertEqual(rc, 1)
            self.assertFalse(base.exists())

    def test_zero_files_refuses_baseline(self):
        # 零文件不得写基线：max_ts 落 0 会让 delta 把历史全当新增
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            fake = FakeAdb(files={})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_baseline(tmp, _args(baseline=str(base)))
            self.assertEqual(rc, 1)
            self.assertFalse(base.exists())

    def test_timeout_propagates_minus1(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"}, ls_rc=-1)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_baseline(tmp, _args(baseline=str(base)))
        self.assertEqual(rc, -1)


class TestModeDelta(unittest.TestCase):
    def _base(self, path, max_ts=1000):
        Path(path).write_text(json.dumps({"max_ts": max_ts, "line_count": {}}),
                              encoding="utf-8")

    def _now_out(self):
        import time
        return f"{int(time.time())}\n"

    def test_new_records_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            self._base(base, max_ts=1000)
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl":
                                  _jsonl('{"ts": 2000, "id": 9, "f": []}')},
                           date_out=self._now_out(), date_rc=0)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(baseline=str(base)))
        self.assertEqual(rc, 0)

    def test_no_new_records_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            self._base(base, max_ts=5000)
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl":
                                  _jsonl('{"ts": 2000, "id": 9, "f": []}')},
                           date_out=self._now_out(), date_rc=0)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(baseline=str(base)))
        self.assertEqual(rc, 1)

    def test_missing_baseline_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"})
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(baseline=str(Path(tmp) / "nope.json")))
        self.assertEqual(rc, 1)

    def test_device_clock_unreadable_fails(self):
        # 方向 3：device_now 读不到（date 异常）→ 判红，静默不过滤
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            self._base(base, max_ts=1000)
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl":
                                  _jsonl('{"ts": 2000, "id": 9, "f": []}')},
                           date_out="bad", date_rc=1)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(baseline=str(base)))
        self.assertEqual(rc, 1)

    def test_event_vid_pid_match_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            self._base(base, max_ts=1000)
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
                '{"ts": 2000, "id": 8, "f": [0, 1256, 25344, "Samsung", "Flash"]}')},
                date_out=self._now_out(), date_rc=0)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(
                    baseline=str(base), event=8, vid=1256, pid=25344))
        self.assertEqual(rc, 0)

    def test_event_vid_pid_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            self._base(base, max_ts=1000)
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": _jsonl(
                '{"ts": 2000, "id": 8, "f": [0, 9999, 9999, "X", "Y"]}')},
                date_out=self._now_out(), date_rc=0)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(
                    baseline=str(base), event=8, vid=1256, pid=25344))
        self.assertEqual(rc, 1)

    def test_timeout_propagates_minus1(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            self._base(base, max_ts=1000)
            fake = FakeAdb(files={f"{LOGS_DIR}/a.jsonl": "{}"}, ls_rc=-1)
            with mock.patch.object(lc, "adb", fake):
                rc = lc.mode_delta(tmp, _args(baseline=str(base)))
        self.assertEqual(rc, -1)


def _sysfs(total=0, overrun=0, ring=0):
    return (f"total_records={total} overrun={overrun} ring_usage_bytes={ring} "
            f"ring_size_bytes=262144")


def _wc(lines):
    return f"{lines} total\n"


class TestModeConserve(unittest.TestCase):
    # conserve v4：两段式采样（静止确认段 2 拍 + 负载采样段 2 拍，共 3 拍）。
    # 静止确认段增量归零 → 起点无积压；负载段比较内核产生增量
    # （Δtotal-Δoverrun）vs 磁盘 JSONL 落盘增量，负向（落盘>产生）在
    # 起点无积压时为真异常判红，仅追赶期（起点有积压）放行。
    def _run(self, sysfs_seq, wc_seq, **kw):
        fake = FakeAdb(sysfs_seq=sysfs_seq, wc_seq=wc_seq,
                       dd_rc=kw.get("dd_rc", 0))
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                with mock.patch.object(lc.time, "sleep"):
                    return lc.mode_conserve(tmp, _args(**kw))

    def test_conserve_static_no_data_red(self):
        # 静止且无自造负载：静止确认通过，但负载窗口 produced==0 →
        # 无数据可校验守恒须判红（防假绿，零记录守卫）
        self.assertEqual(
            self._run([(_sysfs(total=90), 0), (_sysfs(total=90), 0),
                       (_sysfs(total=90), 0)],
                      [(_wc(90), 0), (_wc(90), 0), (_wc(90), 0)]), 1)

    def test_conserve_produced_equals_landed_ok(self):
        # 静止确认通过（前两拍无增量）+ 负载窗口产生 10（total 100→110，
        # overrun 不变）且落盘 10（90→100）→ 在途 0，守恒
        self.assertEqual(
            self._run([(_sysfs(total=100), 0), (_sysfs(total=100), 0),
                       (_sysfs(total=110), 0)],
                      [(_wc(90), 0), (_wc(90), 0), (_wc(100), 0)]), 0)

    def test_conserve_produced_less_overrun_ok(self):
        # 产生含被驱逐（overrun 增 5）：产生增量 = Δtotal - Δoverrun
        self.assertEqual(
            self._run([(_sysfs(total=100, overrun=2), 0),
                       (_sysfs(total=100, overrun=2), 0),
                       (_sysfs(total=115, overrun=7), 0)],
                      [(_wc(90), 0), (_wc(90), 0), (_wc(98), 0)]), 0)

    def test_conserve_backlog_fails(self):
        # 产生 30 但落盘 0 → 积压判红（真丢记录/消费停滞场景）
        self.assertEqual(
            self._run([(_sysfs(total=100), 0), (_sysfs(total=100), 0),
                       (_sysfs(total=130), 0)],
                      [(_wc(90), 0), (_wc(90), 0), (_wc(90), 0)]), 1)

    def test_conserve_duplicate_landing_fails(self):
        # 静止确认通过（起点无积压）但负载窗口落盘 30 > 产生 10 →
        # 重复落盘/计数异常判红（恢复负向判红，dropped 不能替代）
        self.assertEqual(
            self._run([(_sysfs(total=100), 0), (_sysfs(total=100), 0),
                       (_sysfs(total=110), 0)],
                      [(_wc(90), 0), (_wc(90), 0), (_wc(120), 0)]), 1)

    def test_conserve_negative_release_with_backlog_ok(self):
        # 静止确认段有增量（起点有积压，追赶期）→ 负载窗口落盘 40 > 产生 5
        # 为补落盘，负向放行（不判红）
        self.assertEqual(
            self._run([(_sysfs(total=100), 0), (_sysfs(total=110), 0),
                       (_sysfs(total=115), 0)],
                      [(_wc(90), 0), (_wc(90), 0), (_wc(130), 0)]), 0)

    def test_conserve_daemon_restart_immune(self):
        # daemon 重启免疫：磁盘 JSONL 行数持久（wc 不归零），静止确认 +
        # 负载窗口产生 10 落盘 10 即守恒——不再依赖 daemon 进程内
        # jsonl_records（重启归零曾致误判）
        self.assertEqual(
            self._run([(_sysfs(total=100), 0), (_sysfs(total=100), 0),
                       (_sysfs(total=110), 0)],
                      [(_wc(1535), 0), (_wc(1535), 0), (_wc(1545), 0)]), 0)

    def test_conserve_load_dd_fail_red(self):
        # --conserve-load-mb 时自造负载 dd 执行失败 → 判红（触发手段不可用
        # 不得蒙混，与 delta 的 dd 失败判红同源）
        self.assertEqual(
            self._run([(_sysfs(total=90), 0), (_sysfs(total=90), 0)],
                      [(_wc(90), 0), (_wc(90), 0)],
                      conserve_load_mb=4, dd_rc=1), 1)

    def test_conserve_sysfs_fail_red(self):
        # 首拍 sysfs 读失败（cat 超时）→ 判红（无可判数据不蒙混）
        self.assertEqual(self._run([("", -1)], [(_wc(0), 0)]), 1)

    def test_conserve_wc_fail_red(self):
        # 次拍 wc 失败 → 判红
        self.assertEqual(
            self._run([(_sysfs(total=90), 0), (_sysfs(total=90), 0),
                       (_sysfs(total=90), 0)],
                      [(_wc(0), 0), (_wc(0), 0), ("", -1)]), 1)


class TestModePerf(unittest.TestCase):
    def _run(self, fake, totals, jsonls, monotonic=None, **kw):
        if monotonic is None:
            # 缺省 mock 递增值：粗粒度钟（Windows/mingw 等）下两次调用可能
            # 同值 → dd_s=0 被 C4 守卫恒判红；递增值保证 dd_s>0 走正常流程
            monotonic = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5]
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                with mock.patch.object(lc, "kernel_total",
                                       side_effect=totals):
                    with mock.patch.object(lc, "jsonl_line_count",
                                           side_effect=jsonls):
                        with mock.patch.object(lc.time, "sleep"):
                            with mock.patch.object(lc.time, "monotonic",
                                                   side_effect=monotonic):
                                return lc.mode_perf(tmp, _args(**kw))

    def test_perf_full_pipeline_ok(self):
        # dd 前直读内核 total=100/jsonl=90；dd 后直读 total=1321/jsonl=1311
        # （jsonl 增量 1221 >= total 增量 1221，drain 首轮即达标）→ rc 0
        fake = FakeAdb(dd_rc=0,
                       pidof_out="1234\n", pidof_rc=0,
                       proc_out="Name:\tlechao_lcview\nVmHWM:\t    5516 kB\n",
                       proc_rc=0)
        rc = self._run(fake, totals=[100, 1321, 1321], jsonls=[90, 1311, 1311],
                       monotonic=[100.0, 103.7, 103.7, 104.1])
        self.assertEqual(rc, 0)
        # 内部经 adb 执行：dd 一次 + pidof/cat 各一次（直读走 mock，不经 adb）
        self.assertTrue(any(c[1].startswith("dd if=") for c in fake.calls))
        self.assertTrue(any(c[1].startswith("pidof ") for c in fake.calls))
        self.assertTrue(any(c[1].startswith("cat /proc/") for c in fake.calls))

    def test_perf_metrics_json_emitted(self):
        # 走 TestModePerf._run（缺省 mock monotonic 递增值，与 C4 守卫同源）；
        # 上批漏转的独立实现仍无 monotonic mock，粗粒度钟下 dd_s=0 恒判红
        fake = FakeAdb(dd_rc=0,
                       pidof_out="1234\n", pidof_rc=0,
                       proc_out="VmHWM:\t    5516 kB\n", proc_rc=0)
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                with mock.patch.object(lc, "kernel_total",
                                       side_effect=[100, 1321, 1321]):
                    with mock.patch.object(lc, "jsonl_line_count",
                                           side_effect=[90, 1311, 1311]):
                        with mock.patch.object(lc.time, "sleep"):
                            with mock.patch.object(lc.time, "monotonic",
                                                   side_effect=[100.0, 103.7,
                                                                103.7, 104.1]):
                                with contextlib.redirect_stdout(out):
                                    rc = lc.mode_perf(tmp, _args())
        self.assertEqual(rc, 0)
        line = [ln for ln in out.getvalue().splitlines()
                if ln.startswith("METRICS ")]
        self.assertEqual(len(line), 1)
        metrics = json.loads(line[0][len("METRICS "):])
        self.assertEqual(metrics["load_mb"], 64)
        self.assertIn("throughput_evs", metrics)
        self.assertIn("drain_ms_per_event", metrics)
        self.assertEqual(metrics["daemon_rss_kb"], 5516)
        self.assertEqual(metrics["total_delta"], 1221)
        self.assertEqual(metrics["jsonl_delta"], 1221)

    def test_perf_kernel_unreadable_fails(self):
        # 内核计数直读失败（lcview_stats 未部署/失败）→ 判红（指标不全）
        fake = FakeAdb(dd_rc=0)
        rc = self._run(fake, totals=[None], jsonls=[90])
        self.assertEqual(rc, 1)

    def test_perf_jsonl_unreadable_fails(self):
        # JSONL 行数直读失败（wc 超时）→ 判红
        fake = FakeAdb(dd_rc=0)
        rc = self._run(fake, totals=[100], jsonls=[None])
        self.assertEqual(rc, 1)

    def test_perf_dd_failure_fails(self):
        fake = FakeAdb(dd_rc=1)
        rc = self._run(fake, totals=[100], jsonls=[90])
        self.assertEqual(rc, 1)

    def test_perf_dd_s_zero_fails(self):
        # dd_s<=0（负载未执行或计时异常）→ 判红并提示，不得出 throughput=inf 假基线
        fake = FakeAdb(dd_rc=0)
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(lc, "adb", fake):
                with mock.patch.object(lc, "kernel_total",
                                       side_effect=[100]):
                    with mock.patch.object(lc, "jsonl_line_count",
                                           side_effect=[90]):
                        with mock.patch.object(lc.time, "sleep"):
                            with mock.patch.object(lc.time, "monotonic",
                                                   side_effect=[100.0, 100.0]):
                                with contextlib.redirect_stdout(out):
                                    rc = lc.mode_perf(tmp, _args())
        self.assertEqual(rc, 1)
        self.assertIn("负载未执行", out.getvalue())

    def test_perf_kernel_lag_waits_for_update(self):
        # dd 后首轮直读仍为 dd 前旧值（内核计数尚未反映）→ 不得把 0>=0
        # 误判达标，须等内核计数出现增量后再判 jsonl 达标
        fake = FakeAdb(dd_rc=0,
                       pidof_out="1234\n", pidof_rc=0,
                       proc_out="VmHWM:\t    5516 kB\n", proc_rc=0)
        rc = self._run(fake,
                       totals=[100, 100, 1321, 1321],
                       jsonls=[90, 90, 1311, 1311],
                       monotonic=[100.0, 103.7, 103.7, 103.8, 104.1])
        self.assertEqual(rc, 0)

    def test_perf_drain_timeout_fails(self):
        # 直读 jsonl 增量始终 < total 增量 → drain 轮询超时判红
        fake = FakeAdb(dd_rc=0)
        rc = self._run(fake,
                       totals=[200, 300, 300],   # total 增量 100
                       jsonls=[95, 100, 100],    # jsonl 增量 5 < 100，永不达标
                       monotonic=[0.0, 1.0, 1.0, 2.0, 3.0],
                       perf_timeout=1)
        self.assertEqual(rc, 1)

    def test_perf_rss_unavailable_fails(self):
        # RSS 指标不全不能当完整基线 → 判红
        fake = FakeAdb(dd_rc=0, pidof_rc=1, pidof_out="")
        rc = self._run(fake, totals=[100, 1321, 1321], jsonls=[90, 1311, 1311])
        self.assertEqual(rc, 1)

    def test_perf_timeout_propagates_minus1(self):
        fake = FakeAdb(dd_rc=-1)
        rc = self._run(fake, totals=[100], jsonls=[90])
        self.assertEqual(rc, -1)


if __name__ == "__main__":
    unittest.main()