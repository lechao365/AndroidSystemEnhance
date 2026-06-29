"""patch_guard：白名单校验 + 风险标记 + 语法预检。"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from loop_controller.analyzer_protocol import FileChange


@dataclass
class GuardResult:
    allowed: bool
    rejected_files: list[str] = field(default_factory=list)
    risk: str = "NORMAL"


_KERNEL_RISK_MARKERS = {
    ".c", ".h", ".dts", ".dtsi", "Makefile",
    "Kconfig", "defconfig", "Kbuild", ".rc",
}


def check_white_list(changes: list[FileChange], allowed_prefixes: list[str]) -> GuardResult:
    """校验改动是否全部落在白名单前缀内。

    P1-6：防路径穿越——先拒绝显式含 ``..`` 的路径，再用规范化后的绝对路径判前缀，
    避免 ``device/brcm/rpi5/../../../etc/passwd`` 这类穿越攻击绕过 startswith。
    """
    rejected: list[str] = []
    for fc in changes:
        wp = fc.workspace_path
        # 1. 显式拒绝含 .. 的穿越路径（无论是否落在白名单字面前缀内）
        if ".." in Path(wp).parts:
            rejected.append(wp)
            continue
        # 2. 规范化后判前缀：workspace_path 可能是相对路径，
        #    用 posix 风格规范化（去掉多余分隔符，不解析 .. 已被上面拦截）
        normalized = Path(wp).as_posix().rstrip("/") if wp else ""
        ok = False
        for prefix in allowed_prefixes:
            # 空 prefix 视为通配（向后兼容测试/调试场景的 [""] 白名单）。
            # 注意 Path("").as_posix() == "."，须在规范化前判原始字符串。
            if prefix == "" or prefix.strip("/") == "":
                ok = True
                break
            norm_prefix = Path(prefix).as_posix().rstrip("/")
            if (normalized == norm_prefix
                    or normalized.startswith(norm_prefix + "/")):
                ok = True
                break
        if not ok:
            rejected.append(wp)
    return GuardResult(
        allowed=len(rejected) == 0,
        rejected_files=rejected,
    )


def detect_risk(changes: list[FileChange]) -> str:
    for fc in changes:
        p = Path(fc.workspace_path)
        if p.suffix in _KERNEL_RISK_MARKERS or p.name in _KERNEL_RISK_MARKERS:
            return "KERNEL"
    return "NORMAL"


def check_syntax(changes: list[FileChange], workspace_root: str = "") -> list[str]:
    errors: list[str] = []
    for fc in changes:
        ext = Path(fc.workspace_path).suffix
        if ext in (".c", ".cpp"):
            fp = Path(workspace_root) / fc.workspace_path
            if fp.exists():
                lang = "c++" if ext == ".cpp" else "c"
                try:
                    r = subprocess.run(
                        ["gcc", "-fsyntax-only", "-x", lang, str(fp)],
                        capture_output=True, text=True, timeout=30,
                    )
                except (subprocess.SubprocessError, OSError):
                    continue
                if r.returncode != 0:
                    stderr = r.stderr[:500]
                    if "fatal error:" in stderr and "No such file or directory" in stderr:
                        continue
                    errors.append(f"{fc.workspace_path}: syntax error\n{stderr[:200]}")
    return errors
