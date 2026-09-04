# slow_guard 单测（方向 2）：单用例墙钟守卫纯逻辑。
# 端到端红路径（真超时用例）不入自检套件——判红路径由纯函数直测覆盖；
# 全绿套件本身即"守卫不误伤"的冒烟（conftest hook 在 harness 全量生效）。

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pytest  # noqa: E402

import slow_guard  # noqa: E402


class FakeMark:
    def __init__(self, name):
        self.name = name


class FakeItem:
    def __init__(self, marks=(), t0_offset=None):
        self._marks = list(marks)
        if t0_offset is not None:
            self._slow_guard_t0 = time.monotonic() - t0_offset

    def iter_markers(self):
        return iter(self._marks)


class FakeRep:
    def __init__(self, when="call", passed=True):
        self.when = when
        self.passed = passed
        self.outcome = "passed"
        self.longrepr = None


class TestSlowGuard(unittest.TestCase):
    def test_setup_started_records_t0(self):
        item = FakeItem()
        slow_guard.setup_started(item)
        self.assertTrue(hasattr(item, "_slow_guard_t0"))

    def test_within_threshold_passes(self):
        # 阈值内（1s < 3s）→ 不判红，报告不动
        item = FakeItem(t0_offset=1.0)
        rep = FakeRep()
        violated, elapsed = slow_guard.enforce_on_call(item, rep)
        self.assertFalse(violated)
        self.assertEqual(rep.outcome, "passed")
        self.assertGreater(elapsed, 0.5)

    def test_over_threshold_is_red(self):
        # 方向 2 核心：墙钟超 3s → 用例改判 failed，longrepr 指引豁免方式
        item = FakeItem(t0_offset=3.5)
        rep = FakeRep()
        violated, _ = slow_guard.enforce_on_call(item, rep)
        self.assertTrue(violated)
        self.assertEqual(rep.outcome, "failed")
        self.assertIn("slow guard", str(rep.longrepr))
        self.assertIn("slow_ok", str(rep.longrepr))

    def test_slow_ok_marker_exempt(self):
        # 显式豁免（slow_ok marker）→ 超限也不判红
        item = FakeItem(marks=[FakeMark("slow_ok")], t0_offset=10.0)
        rep = FakeRep()
        violated, _ = slow_guard.enforce_on_call(item, rep)
        self.assertFalse(violated)
        self.assertEqual(rep.outcome, "passed")

    def test_non_call_or_failed_untouched(self):
        # 非 call 报告 / 已失败报告不动（不重复改写失败现场）
        item = FakeItem(t0_offset=10.0)
        rep_setup = FakeRep(when="setup")
        self.assertEqual(slow_guard.enforce_on_call(item, rep_setup), (False, 0.0))
        rep_failed = FakeRep(passed=False)
        self.assertEqual(slow_guard.enforce_on_call(item, rep_failed), (False, 0.0))
        self.assertEqual(rep_failed.outcome, "passed")

    def test_missing_t0_noop(self):
        # setup_started 未跑（如 conftest 未接线）→ no-op 不误伤
        item = FakeItem()
        rep = FakeRep()
        self.assertEqual(slow_guard.enforce_on_call(item, rep), (False, 0.0))

    def test_marker_attr_attached_by_decorator(self):
        # 装饰器生效：mark 落到函数 pytestmark 属性（unittest 方法对象经
        # 属性转发同样可读——slow guard 豁免依赖该机制）
        self.assertTrue(any(getattr(m, "name", None) == "slow_ok"
                            for m in _decorated_with_slow_ok.pytestmark))


@pytest.mark.slow_ok("自验证 marker 在 pytest 原生用例上对 iter_markers 可见")
def test_slow_ok_marker_visible(request):
    # marker 可见性端到端（pytest 原生用例，非 unittest——unittest 方法
    # 不注入 fixture 参数）：iter_markers 收到 slow_ok 即豁免链路可用
    marks = [m.name for m in request.node.iter_markers()]
    assert "slow_ok" in marks


@pytest.mark.slow_ok("attr check")
def _decorated_with_slow_ok():
    pass


if __name__ == "__main__":
    unittest.main()
