"""harness 级 pytest 配置：慢用例墙钟守卫（方向 2）+ slow_ok marker 注册。

守卫逻辑在 harness/lib/slow_guard.py（纯函数，单测直调）；本 conftest
只做 hook 转发——pytest_runtest_setup 记时、pytest_runtest_makereport
在 call 落判（超 3s 且未豁免 → 用例改判 failed）。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from slow_guard import enforce_on_call, setup_started, SLOW_OK_MARKER  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{SLOW_OK_MARKER}(reason=None): 慢用例豁免 slow guard（确需真实"
        "子进程/脚本语义的用例显式标记）")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    setup_started(item)
    yield


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    if call.when == "call":
        rep = outcome.get_result()
        enforce_on_call(item, rep)
