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
    def test_rc_nonzero_passed_through(self):
        # 方向 6：桩令 pytest 与 refs 均非零，rc 必须如实透出（不经管道）
        fake = _fake_run([
            _FakeProc(1, "1 failed, 119 passed in 5.0s\n"),
            _FakeProc(2, "==== 共 3 处悬空引用（exit 1）====\n"),
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
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=2", out)
        self.assertNotIn("skipped=0", out)


if __name__ == "__main__":
    unittest.main()