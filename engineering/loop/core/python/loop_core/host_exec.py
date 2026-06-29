from __future__ import annotations

from dataclasses import dataclass
import subprocess


class HostCommandError(RuntimeError):
    pass


@dataclass
class HostCommandResult:
    command: str
    output: str
    exit_code: int
    error: str = ""


def run_host_command(command: str, timeout_sec: float, cwd: str = "") -> HostCommandResult:
    """执行 host 命令。

    Args:
        command: shell 命令（经 bash -lc 执行）
        timeout_sec: 超时秒数
        cwd: 工作目录（空字符串表示用调用进程工作目录，保持向后兼容）。
            P2-9：新增，使 host 命令（如 git 操作）能定位正确的工作目录。
    """
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            cwd=cwd or None,
        )
    except subprocess.TimeoutExpired as exc:
        raise HostCommandError(f"host command timed out after {timeout_sec}s: {command}") from exc
    except OSError as exc:
        raise HostCommandError(f"failed to execute host command: {exc}") from exc

    output = (completed.stdout or "") + (completed.stderr or "")
    return HostCommandResult(
        command=command,
        output=output,
        exit_code=completed.returncode,
        error="" if completed.returncode == 0 else f"exit code {completed.returncode}",
    )
