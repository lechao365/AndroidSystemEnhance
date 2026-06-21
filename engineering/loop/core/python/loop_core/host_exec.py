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


def run_host_command(command: str, timeout_sec: float) -> HostCommandResult:
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
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
