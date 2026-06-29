import pytest

from loop_core.host_exec import HostCommandError, run_host_command


def test_run_host_command_returns_stdout_and_exit_code():
    result = run_host_command("python3 -c 'print(\"ok\")'", timeout_sec=5.0)
    assert result.exit_code == 0
    assert result.output.strip() == "ok"
    assert result.error == ""


def test_run_host_command_merges_stderr_into_output():
    result = run_host_command(
        "python3 -c 'import sys; sys.stderr.write(\"err\\n\")'",
        timeout_sec=5.0,
    )
    assert result.exit_code == 0
    assert "err" in result.output


def test_run_host_command_preserves_nonzero_exit_code():
    result = run_host_command(
        "python3 -c 'import sys; print(\"bad\"); sys.exit(7)'",
        timeout_sec=5.0,
    )
    assert result.exit_code == 7
    assert "bad" in result.output


def test_run_host_command_timeout_raises_host_command_error():
    with pytest.raises(HostCommandError, match="timed out"):
        run_host_command("python3 -c 'import time; time.sleep(2)'", timeout_sec=0.2)


def test_run_host_command_uses_cwd(tmp_path):
    """P2-9：cwd 参数生效，命令在指定目录下执行。

    回归 P2-9：原 run_host_command 无 cwd 参数，命令依赖调用进程工作目录，
    复现性差（如 git 操作依赖仓库根）。
    """
    # 用 pwd 验证 cwd 生效（返回的 output 含 tmp_path）
    result = run_host_command("pwd", timeout_sec=5.0, cwd=str(tmp_path))
    assert result.exit_code == 0
    assert str(tmp_path) in result.output


def test_run_host_command_without_cwd_uses_process_dir():
    """不传 cwd 时保持原行为（用调用进程工作目录）。"""
    import os
    result = run_host_command("pwd", timeout_sec=5.0)
    assert result.exit_code == 0
    assert os.getcwd() in result.output
