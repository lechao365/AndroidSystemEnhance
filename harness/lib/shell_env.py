"""shell 环境探测：bash 定位与 python3 转发 shim。

背景：emit 侧为 Windows，PATH 通常无 bash，依赖 bash 的测试被 pytest.skip
跳过成盲区（KI-20260831-003）。find_bash 先查 PATH，未得且环境变量
LC_HARNESS_WIN_BASH=1 时由 git 可执行文件路径推 Git for Windows 的
bin/bash.exe（git 在 cmd/ 或 bin/ 下两种布局均覆盖），否则返 None。
write_python3_shim 写 python3 转发 shim：Windows 无 python3 命令（仅
python.exe），bash 脚本内 python3 调用会失败，shim 把调用转发到当前解释器。
bash_argv 组合 bash -c 在 shell 内前置 PATH 再 exec：bin/bash.exe 启动时
强插 /mingw64/bin 与 /usr/bin 到 PATH 最前，subprocess 直接传 [bash, 脚本]
时外部 PATH 前置（shim/mock 目录）被顶到后面失效。
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
    # root = git 路径的 parent.parent，bash.exe 恒在 <root>/bin/ 下。
    # 不用 resolve()：异盘路径会被锚到 cwd 盘（如 D:\\ 的 git 锚成 C:\\）
    git_root = Path(git).parent.parent
    cand = git_root / "bin" / "bash.exe"
    return str(cand) if cand.is_file() else None


def to_posix_path(p):
    """Windows 路径转 POSIX 式：盘符转小写斜杠式（C:\\x → /c/x），
    反斜杠转正斜杠；非 Windows 或非盘符路径原样返回。"""
    s = str(p).replace("\\", "/")
    if sys.platform == "win32" and len(s) >= 2 and s[1] == ":":
        return f"/{s[0].lower()}{s[2:]}"
    return s


def bash_argv(script, args=(), prepend_dirs=()):
    """返回 bash 执行 argv：find_bash + -c + shell 内前置 PATH 再 exec。

    绕开 bin/bash.exe 启动期强插 mingw64/usr 到 PATH 最前的失效：bash -c
    'PATH=<前置目录>: $PATH; exec "$0" "$@"' 在 shell 字符串内显式前置目录
    （优先级高于强插段）后 exec 目标脚本，shim/mock 目录可靠命中。
    前置目录经 to_posix_path 转 POSIX 式（shell 内 PATH 以 : 分隔）。
    无 bash 时返 None（调用方 skip）。
    """
    bash = find_bash()
    if not bash:
        return None
    prefix = ":".join(to_posix_path(d) for d in prepend_dirs)
    code = (f'PATH="{prefix}:$PATH"; exec "$0" "$@"' if prefix
            else 'exec "$0" "$@"')
    return [bash, "-c", code, to_posix_path(script), *[str(a) for a in args]]


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