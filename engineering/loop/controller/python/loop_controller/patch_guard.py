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
    rejected: list[str] = []
    for fc in changes:
        ok = False
        for prefix in allowed_prefixes:
            if fc.workspace_path.startswith(prefix) or fc.workspace_path == prefix.rstrip("/"):
                ok = True
                break
        if not ok:
            rejected.append(fc.workspace_path)
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
