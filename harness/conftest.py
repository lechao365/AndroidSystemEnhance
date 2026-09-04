"""harness 级 pytest 配置：慢用例墙钟守卫（方向 2）+ CDP_PROJECT_ROOT 隔离。

- slow_guard：守卫逻辑在 harness/lib/slow_guard.py（纯函数，单测直调）；
  本 conftest 只做 hook 转发——pytest_runtest_setup 记时、
  pytest_runtest_makereport 在 call 落判（超 3s 且未豁免 → 用例改判 failed）。
- CDP_PROJECT_ROOT 隔离（默认化）：autouse fixture 给未显式设置且未声明
  real_repo marker 的用例默认把 CDP_PROJECT_ROOT 指向 pytest 临时目录，
  防新守卫单测漏隔离读到真实仓状态而假失败；存量测试文件的手工隔离样板
  （setUp 已设 CDP_PROJECT_ROOT）天然豁免，无需改动。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))

from slow_guard import enforce_on_call, setup_started, SLOW_OK_MARKER  # noqa: E402

# 需真实仓路径的用例显式标记（conftest 注册防未知标记告警，方向 1）
REAL_REPO_MARKER = "real_repo"


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        f"{SLOW_OK_MARKER}(reason=None): 慢用例豁免 slow guard（确需真实"
        "子进程/脚本语义的用例显式标记）")
    config.addinivalue_line(
        "markers",
        f"{REAL_REPO_MARKER}(reason=None): 需真实仓库路径的用例放行 "
        "CDP_PROJECT_ROOT 隔离（确需读到真实仓 data/harness 状态的用例显式标记）")


@pytest.fixture(autouse=True)
def _isolate_cdp_project_root(request, tmp_path):
    """默认隔离：未显式设置 CDP_PROJECT_ROOT 且无 real_repo marker 的用例
    指向 pytest 临时目录，防漏隔离读到真实仓状态假失败。

    豁免两径（均尊重显式意图）：
    - 用例带 real_repo marker → 放行真实仓路径；
    - CDP_PROJECT_ROOT 已被显式设置（存量测试 setUp 手工样板/外部环境）
      → 不覆盖。
    显式设置（marker 或 env）不干预，方向 1 存量 19 个测试文件样板不动。
    """
    if request.node.get_closest_marker(REAL_REPO_MARKER) or \
            os.environ.get("CDP_PROJECT_ROOT"):
        yield
        return
    os.environ["CDP_PROJECT_ROOT"] = str(tmp_path)
    try:
        yield
    finally:
        os.environ.pop("CDP_PROJECT_ROOT", None)


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
