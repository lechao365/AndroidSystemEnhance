import contextlib
import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selfcheck


class _FakeProc:
    def __init__(self, returncode, out="", err=""):
        self.returncode = returncode
        self.stdout = out
        self.stderr = err


def _fake_run(seq):
    """桩 subprocess.run：按调用顺序返回假进程。"""
    def _run(cmd, **kw):
        return seq.pop(0)
    return _run


class TestSelfcheck(unittest.TestCase):
    def setUp(self):
        # 屏蔽自发 apply_selfcheck 打点（打点不影响 selfcheck 结果断言，
        # 其行为由 TestMarkSelfcheck 单独覆盖；不屏蔽会让 mock 的
        # subprocess.run 固定序列被额外调用耗尽报 IndexError）
        self._mark = mock.patch.object(selfcheck, "_mark_selfcheck")
        self._mark.start()

    def tearDown(self):
        self._mark.stop()

    def test_rc_nonzero_passed_through(self):
        # 方向 6：桩令 pytest 与 refs 均非零，rc 必须如实透出（不经管道）
        fake = _fake_run([
            _FakeProc(1, "1 failed, 119 passed in 5.0s\n"),
            _FakeProc(2, "==== 共 3 处悬空引用（exit 1）====\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                self.assertEqual(selfcheck.main(), 0)
        out = buf.getvalue()
        self.assertIn("pytest_rc=1", out)
        self.assertIn("refs_rc=2", out)
        self.assertIn("1 failed, 119 passed", out)
        self.assertIn("悬空引用", out)

    def test_rc_zero_with_skip_in_summary_no_fake_skipped(self):
        # pytest 通过且摘要含 skipped → 不补 skipped=0（已有计数）
        fake = _fake_run([
            _FakeProc(0, "121 passed, 3 skipped in 6.0s\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=0", out)
        self.assertIn("3 skipped", out)
        self.assertNotIn("skipped=0", out)

    def test_rc_zero_no_skip_appends_zero(self):
        # 全绿无跳过 → 补 skipped=0（平台跳过数显式可见）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        self.assertIn("skipped=0", buf.getvalue())

    def test_pytest_crash_no_fake_skipped(self):
        # 方向 3：pytest 崩溃（rc 非零且末行无 skipped）→ 不补 skipped=0
        # （兜底会为崩溃的运行伪造计数，使 skipped 门禁永不生效）
        fake = _fake_run([
            _FakeProc(2, "INTERNALERROR> Killed\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=2", out)
        self.assertNotIn("skipped=0", out)

    def test_stderr_warning_does_not_replace_count_line(self):
        # 方向 1/5：stderr 有告警而 stdout 有计数行 → 计数行仍正确提取
        # （只认 stdout；拼接 stderr 会顶掉计数行，使兜底补 skipped=0 谎报）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n",
                      "warn: 某插件加载失败\nwarn: 忽略\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("531 passed in 27.9s", out)
        self.assertIn("skipped=0", out)
        self.assertNotIn("某插件加载失败", out)

    def test_stderr_only_no_count_no_fake_skipped(self):
        # 方向 1/2/5：stdout 无计数行（计数被 stderr 顶掉/异常）→ 不补 skipped=0，
        # 交 ws_report 缺 skipped 拒写（不伪造也不静默通过）
        fake = _fake_run([
            _FakeProc(0, "\n", "warn: 某插件加载失败\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=0", out)
        self.assertNotIn("skipped=0", out)
        self.assertNotIn("某插件加载失败", out)

    def test_refs_conclusion_only_from_stdout(self):
        # 方向 3：refs 结论行只取 stdout 末行，stderr 仅附注不参与判定
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
            _FakeProc(0, "OK: 引用完整\n", "warn: 非判定信息\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("OK: 引用完整", out)
        self.assertNotIn("非判定信息", out)

    # ── 方向 1：xdist 可导入时 -n auto，导入不到回落串行 ────────────────
    def _run_capture_cmd(self, fake):
        """桩 subprocess.run 并捕获 pytest 命令；返回捕获列表。"""
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return fake(cmd, **kw)

        with mock.patch.object(selfcheck.subprocess, "run", side_effect=_run):
            with redirect_stdout(io.StringIO()):
                selfcheck.main()
        return seen

    def test_xdist_importable_uses_parallel(self):
        # xdist 可导入 → pytest 命令加 -n auto（并行提速，计数行正则不动）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        with mock.patch.dict(sys.modules, {"xdist": mock.Mock()}):
            seen = self._run_capture_cmd(fake)
        pytest_cmd = seen[0]
        self.assertIn("-n", pytest_cmd)
        self.assertIn("auto", pytest_cmd)

    def test_xdist_missing_falls_back_serial(self):
        # xdist 不可导入（sys.modules 置 None → import 抛 ImportError）
        # → pytest 命令照旧串行（无 -n auto）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
            _FakeProc(0, "OK: 引用完整\n"),
        _FakeProc(0, "OK: config 检查通过，无违规。\n"),
        _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
        ])
        with mock.patch.dict(sys.modules, {"xdist": None}):
            seen = self._run_capture_cmd(fake)
        pytest_cmd = seen[0]
        self.assertNotIn("-n", pytest_cmd)


class TestMarkSelfcheck(unittest.TestCase):
    """方向 1：自检跑完自发 mark apply_selfcheck（batch 三级回落，失败不阻断）。"""

    def test_main_marks_after_selfcheck(self):
        # main() 完成自检后调用 _mark_selfcheck（打点入 cdp_timing mark 链）
        with mock.patch.object(selfcheck, "_mark_selfcheck") as m:
            fake = _fake_run([
                _FakeProc(0, "531 passed in 27.9s\n"),
                _FakeProc(0, "OK: 引用完整\n"),
            _FakeProc(0, "OK: config 检查通过，无违规。\n"),
            _FakeProc(0, "OK: contract 检查通过，无违规。\n"),
            ])
            with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
                with redirect_stdout(io.StringIO()):
                    selfcheck.main()
        m.assert_called_once()

    def test_mark_calls_cdp_timing_with_name(self):
        # _mark_selfcheck 以 cdp_timing.py mark --name apply_selfcheck 调起，
        # 不显式 --batch（交 cdp_timing 三级回落：env CDP_BATCH_ID > 唯一文件）
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return _FakeProc(0, "mark: apply_selfcheck @ 123.456\n")

        with mock.patch.object(selfcheck.subprocess, "run", side_effect=_run):
            with contextlib.redirect_stderr(io.StringIO()):
                selfcheck._mark_selfcheck()
        self.assertEqual(len(seen), 1)
        cmd = seen[0]
        self.assertTrue(any("cdp_timing" in c for c in cmd))
        self.assertIn("mark", cmd)
        self.assertIn("apply_selfcheck", cmd)
        self.assertNotIn("--batch", cmd)

    def test_mark_failure_not_blocking(self):
        # 发点失败（returncode 非零）仅 warn 不阻断（自检结果与打点解耦）
        fake = _FakeProc(3, "", "error: 未 start\n")
        err = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", return_value=fake):
            with contextlib.redirect_stderr(err):
                selfcheck._mark_selfcheck()  # 不抛异常即通过
        self.assertIn("打点失败（不阻断）", err.getvalue())


if __name__ == "__main__":
    unittest.main()