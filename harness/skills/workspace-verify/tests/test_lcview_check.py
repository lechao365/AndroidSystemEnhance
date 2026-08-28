import argparse
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cases"))
import lcview_check as lc

LOGS_DIR = lc.LOGS_DIR


def _args(**kw):
    a = argparse.Namespace()
    a.window = kw.get("window", 600)
    a.skew = kw.get("skew", 600)
    a.event = kw.get("event", None)
    a.vid = kw.get("vid", None)
    a.pid = kw.get("pid", None)
    a.baseline = kw.get("baseline", str(Path(tempfile.gettempdir()) / "lcview_baseline_test.json"))
    return a


class FakeAdb:
    """伪 adb：files 为 {remote: content}，各子命令可注入 rc 模拟失败/超时。

    shell ls 输出文件基名；pull 按 remote 取内容写本地文件；stat 返回注入
    stdout 与 rc；date 返回注入 stdout 与 rc。
    """

    def __init__(self, files=None, ls_rc=0, pull_rc=0, stat_rc=0, stat_out="",
                 date_rc=0, date_out=""):
        self.files = dict(files or {})
        self.ls_rc = ls_rc
        self.pull_rc = pull_rc
        self.stat_rc = stat_rc
        self.stat_out = stat_out
        self.date_rc = date_rc
        self.date_out = date_out
        self.calls = []

    def __call__(self, args, timeout=60):
        self.calls.append(args)
        if args[0] == "shell":
            cmd = args[1]
            if cmd.startswith("ls "):
                return (" ".join(Path(r).name for r in self.files), self.ls_rc)
            if "stat" in cmd:
                return (self.stat_out, self.stat_rc)
            if "date" in cmd:
                return (self.date_out, self.date_rc)
            return ("", 0)
        if args[0] == "pull":
            remote, local = args[1], args[2]
            if self.pull_rc == 0:
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


if __name__ == "__main__":
    unittest.main()