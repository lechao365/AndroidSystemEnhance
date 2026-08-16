#!/usr/bin/env python3
"""apply_preset_bugs - 向 workspace 注入预设 bug，验证 AI 闭环能力"""

from __future__ import annotations

import sys
import os
import re
import shutil
import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())
sys.path.insert(0, str(_REPO_ROOT))

from harness.lib.harness_lib import (
    step_begin, step_end, log_info, log_error,
    harness_init, harness_exit,
)
from harness.lib.paths import env_path

# 本 profile 的 vendor 名（硬编码因属 profile 自身语义，不回流到 pack SSOT）
_VENDOR_NAME = "lechao"


def _resolve_aosp_ws() -> Path:
    """解析 AOSP workspace 路径，失败时抛出明确的错误。"""
    raw = os.environ.get("AOSP_WS", "") or env_path("AOSP_WS")
    if not raw:
        raise RuntimeError(
            "无法定位 AOSP workspace：环境变量 AOSP_WS 未设置，"
            "且 harness/config/paths.conf 中 AOSP_WS 也为空。"
            "请设置环境变量 AOSP_WS 或配置 harness/config/paths.conf。"
        )
    return Path(raw)


def _workspace_paths(aosp_ws: Path) -> dict[str, Path]:
    return {
        "LCIOD_HAL": aosp_ws / "vendor" / _VENDOR_NAME / "services" / "lechao_lciod" / "hal" / "hal_service.cpp",
        "LCIOD_DAEMON": aosp_ws / "vendor" / _VENDOR_NAME / "services" / "lechao_lciod" / "daemon" / "service.cpp",
        "LCIOD_DEVIO": aosp_ws / "vendor" / _VENDOR_NAME / "services" / "lechao_lciod" / "hal" / "device_io.cpp",
    }


_WP: dict[str, Path] | None = None


def _get_wp() -> dict[str, Path]:
    global _WP
    if _WP is None:
        aosp_ws = _resolve_aosp_ws()
        _WP = _workspace_paths(aosp_ws)
    return _WP


def LCIOD_HAL() -> Path:
    return _get_wp()["LCIOD_HAL"]


def LCIOD_DAEMON() -> Path:
    return _get_wp()["LCIOD_DAEMON"]


def LCIOD_DEVIO() -> Path:
    return _get_wp()["LCIOD_DEVIO"]


def _backup_dir(aosp_ws: Path) -> Path:
    return aosp_ws / f".lciod_bug_backup_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}"


def _transform_bug_1(text: str) -> str:
    """Bug 1 纯文本变换: readBytes/writeBytes 字段反转（三步占位法避免冲突）。"""
    marker = "_aidl_return->readBytes = raw.TEMP_placeholder;"
    text = text.replace(
        "_aidl_return->readBytes = raw.read_bytes;",
        marker,
    )
    text = text.replace(
        "_aidl_return->writeBytes = raw.write_bytes;",
        "_aidl_return->writeBytes = raw.read_bytes;",
    )
    text = text.replace(
        marker,
        "_aidl_return->readBytes = raw.write_bytes;",
    )
    return text


def apply_bug_1(backup_dir: Path) -> None:
    """Bug 1: HAL getStats read_bytes/write_bytes 字段反转。"""
    step_begin("apply_bug_1")
    log_info("Bug 1: HAL getStats read_bytes/write_bytes 字段反转")
    shutil.copy2(LCIOD_HAL(), backup_dir / "hal_service.cpp.bak")
    old_text = LCIOD_HAL().read_text(encoding="utf-8")
    new_text = _transform_bug_1(old_text)
    if new_text == old_text:
        log_error("Bug 1 替换未生效: hal_service.cpp 中未找到预期模式")
        step_end(False)
        harness_exit(3)
    LCIOD_HAL().write_text(new_text, encoding="utf-8")
    log_info("Bug 1 applied: hal_service.cpp readBytes/writeBytes reversed")
    step_end(True)


def _transform_bug_2(text: str) -> str:
    """Bug 2 纯文本变换: getAverageRate 公式分子分母颠倒。"""
    return text.replace(
        "_aidl_return = static_cast<int64_t>(total * 1000000000ULL / totalNs);",
        "_aidl_return = static_cast<int64_t>(totalNs * 1000000000ULL / total);",
    )


def apply_bug_2(backup_dir: Path) -> None:
    """Bug 2: Daemon getAverageRate 公式分子分母颠倒。"""
    step_begin("apply_bug_2")
    log_info("Bug 2: Daemon getAverageRate 公式分子分母颠倒")
    shutil.copy2(str(LCIOD_DAEMON()), str(backup_dir / "service.cpp.bak"))
    old_text = LCIOD_DAEMON().read_text(encoding="utf-8")
    new_text = _transform_bug_2(old_text)
    if new_text == old_text:
        log_error("Bug 2 替换未生效: service.cpp 中未找到预期模式")
        step_end(False)
        harness_exit(3)
    LCIOD_DAEMON().write_text(new_text, encoding="utf-8")
    log_info("Bug 2 applied: service.cpp getAverageRate formula reversed")
    step_end(True)


def _transform_bug_3(text: str) -> str:
    """Bug 3 纯文本变换: readEvent 排空循环替换为单次读取。"""
    old_pattern = re.compile(
        r'while \(\(n = read\(fd, &tmp, sizeof\(tmp\)\)\) == \(ssize_t\)sizeof\(tmp\)\) \{\n'
        r'        \*event = tmp;\n'
        r'        count\+\+;\n'
        r'        ret = poll\(&pfd, 1, 0\);\n'
        r'        if \(ret <= 0\)\n'
        r'            break;\n'
        r'    \}',
    )
    replacement = (
        'n = read(fd, &tmp, sizeof(tmp));\n'
        '    if (n == (ssize_t)sizeof(tmp)) {\n'
        '        *event = tmp;\n'
        '        count = 1;\n'
        '    }'
    )
    return old_pattern.sub(replacement, text)


def apply_bug_3(backup_dir: Path) -> None:
    """Bug 3: HAL readEvent 排空循环移除--只读一次。"""
    step_begin("apply_bug_3")
    log_info("Bug 3: HAL readEvent 排空循环移除--只读一次")
    shutil.copy2(str(LCIOD_DEVIO()), str(backup_dir / "device_io.cpp.bak"))
    old_text = LCIOD_DEVIO().read_text(encoding="utf-8")
    new_text = _transform_bug_3(old_text)
    if new_text == old_text:
        log_error("Bug 3 替换未生效: device_io.cpp 中未找到预期模式")
        step_end(False)
        harness_exit(3)
    LCIOD_DEVIO().write_text(new_text, encoding="utf-8")
    log_info("Bug 3 applied: device_io.cpp read_event drain loop removed")
    step_end(True)


def revert_bugs(backup_dir: Path) -> bool:
    """从备份目录恢复所有文件。返回 True 表示成功。"""
    step_begin("revert_bugs")
    if not backup_dir.is_dir():
        log_error("No backup directory found. Cannot revert.")
        step_end(False)
        return False
    ok = True
    for src_name, dst in [
        ("hal_service.cpp.bak", LCIOD_HAL()),
        ("service.cpp.bak", LCIOD_DAEMON()),
        ("device_io.cpp.bak", LCIOD_DEVIO()),
    ]:
        src = backup_dir / src_name
        if src.is_file():
            try:
                shutil.copy2(str(src), str(dst))
            except OSError as e:
                log_error(f"恢复失败: {dst} ({e})")
                ok = False
    if ok:
        log_info(f"Reverted all bugs from {backup_dir}")
    else:
        log_error("部分文件恢复失败")
    step_end(ok)
    return ok


def main() -> None:
    harness_init("apply_preset_bugs")

    bugs: list[str] = []
    do_revert = False
    i = 1
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--bug":
            i += 1
            if i >= len(sys.argv):
                log_error("--bug 需要参数，例如 --bug 1 或 --bug 1,2,3")
                harness_exit(3)
            bugs = [b.strip() for b in sys.argv[i].split(",")]
        elif arg == "--revert":
            do_revert = True
        elif arg in ("-h", "--help"):
            print("Usage: python apply_preset_bugs.py --bug <list>  |  --revert")
            print("  --bug 1,2,3    注入指定 bug 编号")
            print("  --revert       从最近备份恢复所有 bug")
            harness_exit(0)
        else:
            log_error(f"Unknown arg: {arg}")
            harness_exit(3)
        i += 1

    aosp_ws = _resolve_aosp_ws()

    if do_revert:
        backups = sorted(aosp_ws.glob(".lciod_bug_backup_*"))
        if not backups:
            log_error("No backup directory found. Cannot revert.")
            harness_exit(3)
        if not revert_bugs(backups[-1]):
            harness_exit(1)
        harness_exit(0)

    if not bugs:
        log_error("--bug is required (e.g. --bug 1 or --bug 1,2,3)")
        harness_exit(3)

    backup_dir = _backup_dir(aosp_ws)
    backup_dir.mkdir(parents=True, exist_ok=True)
    log_info(f"Backup directory: {backup_dir}")

    for b in bugs:
        if b == "1":
            apply_bug_1(backup_dir)
        elif b == "2":
            apply_bug_2(backup_dir)
        elif b == "3":
            apply_bug_3(backup_dir)
        else:
            log_error(f"Unknown bug number: {b} (valid: 1,2,3)")
            harness_exit(3)

    log_info(f"Bugs applied: {','.join(bugs)}. To revert: python {sys.argv[0]} --revert")
    harness_exit(0)


if __name__ == "__main__":
    main()
