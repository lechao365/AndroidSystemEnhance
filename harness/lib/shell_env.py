"""shell 环境探测：bash 定位与 python3 转发 shim。

背景：emit 侧为 Windows，PATH 通常无 bash，依赖 bash 的测试被 pytest.skip
跳过成盲区（KI-20260831-003）。find_bash 先查 PATH，未得且环境变量
LC_HARNESS_WIN_BASH=1 时由 git 可执行文件路径推 Git for Windows 的
bin/bash.exe（git 在 cmd/ 或 bin/ 下两种布局均覆盖），否则返 None。
write_python3_shim 写 python3 转发 shim：Windows 无 python3 命令（仅
python.exe），bash 脚本内 python3 调用会失败，shim 把调用转发到当前解释器。
"""
import os
import shutil
import stat
import sys
from pathlib import Path


def find_bash():
    """返回可用 bash 绝对路径。

    先查 PATH（shutil.which）；未得且 env LC_HARNESS_WIN_BASH 为 1 时由 git
    路径推 Git for Windows 的 bin/bash.exe；仍无返 None（调用方 skip）。
    """
    bash = shutil.which("bash")
    if bash:
        return bash
    if os.environ.get("LC_HARNESS_WIN_BASH") != "1":
        return None
    git = shutil.which("git")
    if not git:
        return None
    # Git for Windows 布局：git.exe 在 <root>/cmd/ 或 <root>/bin/，
    # root = git 路径的 parent.parent，bash.exe 恒在 <root>/bin/ 下
    git_root = Path(git).resolve().parent.parent
    cand = git_root / "bin" / "bash.exe"
    return str(cand) if cand.is_file() else None


def write_python3_shim(directory):
    """在给定目录写 python3 转发 shim 并置可执行位，返回该目录。

    shim 内容：#!/bin/sh + exec <sys.executable 正斜杠> "$@"（正斜杠防
    Windows 反斜杠在 bash 中被转义）；PATH 前置该目录后，bash 脚本内
    python3 调用经 shim 转发到当前解释器。
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    exe = sys.executable.replace("\\", "/")
    shim = d / ("python3.exe" if sys.platform == "win32" else "python3")
    shim.write_text(f"#!/bin/sh\nexec \"{exe}\" \"$@\"\n", encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    return d