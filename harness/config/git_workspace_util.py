"""git_workspace_util - workspace 扫描共享工具

统一 sync/revert 脚本的排除正则，避免规则发散。

设计说明：迁移自 LcHarness 同源模块（config/git_workspace_util.py），
功能保持不变，仅清理 LcHarness 规则引用。
"""

from __future__ import annotations

import os
import re

# 统一排除正则（sync + revert 并集）
_DEFAULT_EXCLUDE_RE = (
    r'\.o$|\.ko$|\.cmd$|\.d$|\.mod\.c$|\.symvers$|^Image$|'
    r'\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|'
    r'\.prebuilt/|\.prev/|/\.git/'
)
_DEFAULT_EXCLUDE_DIR_RE = r'^(out|prebuilts|\.git|__pycache__)$'

HARNESS_EXCLUDE_RE = re.compile(
    os.environ.get("HARNESS_EXCLUDE_RE", _DEFAULT_EXCLUDE_RE)
)
HARNESS_EXCLUDE_DIR_RE = re.compile(
    os.environ.get("HARNESS_EXCLUDE_DIR_RE", _DEFAULT_EXCLUDE_DIR_RE)
)


def is_excluded(path_str: str) -> bool:
    """判断文件路径是否匹配排除正则。"""
    return bool(HARNESS_EXCLUDE_RE.search(path_str))


def is_excluded_dir(name: str) -> bool:
    """判断目录名是否匹配排除目录正则。"""
    return bool(HARNESS_EXCLUDE_DIR_RE.search(name))


def filter_files(files: list[str]) -> list[str]:
    """过滤掉被排除的文件，返回保留列表。"""
    return [f for f in files if not is_excluded(f)]


def count_excluded(files: list[str]) -> int:
    """统计被排除的文件数量。"""
    return sum(1 for f in files if is_excluded(f))
