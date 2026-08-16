"""harness_lib — 项目内精简运行时库（初始化/退出/日志/步骤）

设计说明：迁移自 LcHarness 同源模块（harness_bootstrap + harness_observability）
的精简版。仅保留 UTF-8、退出码汇总、未捕获异常兜底、stderr 日志与步骤追踪；
去掉了 packs 扫描、catalog 发现、制品归档轮转、环境探测等 LcHarness 通用机制。
"""

from __future__ import annotations

import sys
import time
import atexit

_EXIT_CODE = 0
_SCRIPT_NAME = ""
_INIT_TS = 0.0
_EXCEPTHOOK_INSTALLED = False
_EXIT_WRAPPED = False


def _install_excepthook() -> None:
    """未捕获异常时置退出码 1，再透传原 hook（幂等）。"""
    global _EXCEPTHOOK_INSTALLED
    if _EXCEPTHOOK_INSTALLED:
        return
    _EXCEPTHOOK_INSTALLED = True
    original = sys.excepthook

    def _excepthook(exc_type, exc_value, exc_tb) -> None:
        global _EXIT_CODE
        _EXIT_CODE = 1
        original(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook


def _install_exit_wrapper() -> None:
    """幂等包装 sys.exit：把退出码记入 _EXIT_CODE 再透传。"""
    global _EXIT_WRAPPED
    if _EXIT_WRAPPED:
        return
    _EXIT_WRAPPED = True
    original_exit = sys.exit

    def _exit(code=None) -> None:
        global _EXIT_CODE
        if code is None:
            _EXIT_CODE = 0
        elif isinstance(code, int):
            _EXIT_CODE = code
        else:
            _EXIT_CODE = 1
        original_exit(code)

    sys.exit = _exit


def harness_init(name: str) -> None:
    """脚本入口：设置脚本名、UTF-8 编码、异常/退出兜底。"""
    global _SCRIPT_NAME, _INIT_TS
    _SCRIPT_NAME = name
    _INIT_TS = time.time()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    _install_excepthook()
    _install_exit_wrapper()
    atexit.register(_on_exit)


def harness_exit(code: int = 0) -> None:
    """脚本退出：记录退出码并退出。"""
    global _EXIT_CODE
    _EXIT_CODE = code
    sys.exit(code)


def _on_exit() -> None:
    """退出时汇总：非 0 必打 FAIL；0 且耗时 >0.1s 时汇总。"""
    elapsed = time.time() - _INIT_TS
    code = _EXIT_CODE
    if code != 0 or elapsed > 0.1:
        status = "  OK" if code == 0 else "FAIL"
        print(f"\n[{status}] {_SCRIPT_NAME} 退出码={code} 耗时={elapsed:.1f}s")


def log_info(msg: str) -> None:
    print(f"[INFO] {msg}", file=sys.stderr, flush=True)


def log_warn(msg: str) -> None:
    print(f"[WARN] {msg}", file=sys.stderr, flush=True)


def log_error(msg: str) -> None:
    print(f"[ERROR] {msg}", file=sys.stderr, flush=True)


_STEP_STACK: list[tuple[str, float]] = []
_STEP_IDX = 0


def step_begin(msg: str) -> None:
    """步骤开始：输出分隔行并记录起始时间。"""
    global _STEP_IDX
    _STEP_IDX += 1
    _STEP_STACK.append((msg, time.time()))
    bar = "=" * 10
    print(f"\n{bar} STEP {_STEP_IDX}: {msg} {bar}", file=sys.stderr, flush=True)


def step_end(ok: bool, detail: str = "") -> bool:
    """步骤结束：输出耗时与成败标记，返回 ok。"""
    if not _STEP_STACK:
        return ok
    _title, _start = _STEP_STACK.pop()
    elapsed = time.time() - _start
    mark = "  OK" if ok else "FAIL"
    suffix = f" ({elapsed:.1f}s)" if elapsed > 0.05 else ""
    if detail:
        suffix += f" - {detail}"
    print(f"[{mark}]{suffix}", file=sys.stderr, flush=True)
    return ok
