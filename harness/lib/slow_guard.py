"""单用例墙钟守卫（方向 2）：call 阶段（自 setup 起）超阈值判红。

动因：实时等待类实时性缺陷（如 reboot_and_wait 真睡 8s）混进单测后
自检整体多花 15~28s 且随 xdist 分发波动，无守卫时悄然累积。守卫把
"单用例墙钟"制度化：>SLOW_GUARD_SECONDS 即把该用例改判 failed（判红），
确需慢用例（真实子进程/脚本语义）以 @pytest.mark.slow_ok 显式豁免。

钩子接线在 harness/conftest.py（pytest 收集链对 harness 全量生效）；
本模块只含纯逻辑，供 conftest 转发与单测直调（test_slow_guard.py）。
"""
import time

# 判红阈值（秒）：正常单测毫秒级，3s 已是异常信号（xdist 分发不改变
# 单用例自身墙钟，该阈值跨分发模式稳定）
SLOW_GUARD_SECONDS = 3.0

# 豁免 marker 名（pytest_configure 注册，未注册会触发 UnknownMarkWarning）
SLOW_OK_MARKER = "slow_ok"


def setup_started(item):
    """setup 起点记时（挂在 item 上，跨 hook 传递）。"""
    item._slow_guard_t0 = time.monotonic()


def is_exempt(item):
    """显式豁免：item 带 slow_ok marker（可带 reason kwarg）。"""
    return any(m.name == SLOW_OK_MARKER for m in item.iter_markers())


def enforce_on_call(item, rep):
    """call 报告落判（方向 2 核心）：通过且墙钟超限且未豁免 → 改 failed。

    返回 (violated, elapsed)：violated=True 表示本次判红由本守卫做出。
    非_passed 报告不重复动（已有失败现场，墙钟信息无意义）。
    """
    if rep.when != "call" or not rep.passed:
        return False, 0.0
    t0 = getattr(item, "_slow_guard_t0", None)
    if t0 is None:
        return False, 0.0
    elapsed = time.monotonic() - t0
    if elapsed <= SLOW_GUARD_SECONDS:
        return False, elapsed
    if is_exempt(item):
        return False, elapsed
    rep.outcome = "failed"
    rep.longrepr = (f"slow guard: 单用例墙钟 {elapsed:.2f}s 超 "
                    f"{SLOW_GUARD_SECONDS}s 判红（实时等待混入单测）"
                    "；确需慢用例请加 @pytest.mark.slow_ok 豁免")
    return True, elapsed
