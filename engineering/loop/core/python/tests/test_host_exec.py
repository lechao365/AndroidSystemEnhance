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
