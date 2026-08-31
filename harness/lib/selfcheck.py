"""-s 批次自检：直取 pytest 与 check_skill_refs 的真实退出码。

动因：shell 内联 `X=$(cmd | tail -1); XRC=${PIPESTATUS[0]}` 中命令替换
赋值会把 PIPESTATUS 重置为单个 0（命令替换整体退出码，即 tail 的），
导致 rc 恒零、按 rc 的门禁失效（2026-08-31 实测确认）。本脚本用
subprocess 不经管道直取 returncode，如实透出两工具结果。

输出单行（| 连接，供 ws_report --selfcheck 落盘与门禁判定）：
    pytest_rc=<n> | <pytest 摘要行> | skipped=<n> | refs_rc=<n> | <refs 末行>
skipped=<n> 仅在 pytest_rc=0 且摘要无 skipped 时补 0（pytest 崩溃/失败时
不伪造计数）。退出码恒 0：拒写与否由 ws_report 按 rc 判定，本脚本只负责
如实采集（emit 侧可独立自测，不依赖 apply 环境）。
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_tool(cmd):
    """直取工具 returncode（不经管道），返回 (returncode, 末非空行)。"""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT)
    out = proc.stdout + proc.stderr
    lines = [ln for ln in out.splitlines() if ln.strip()]
    last = lines[-1] if lines else ""
    return proc.returncode, last


def main():
    py_rc, py_last = run_tool([sys.executable, "-m", "pytest", "harness", "-q"])
    refs_rc, refs_last = run_tool([sys.executable,
                                   "harness/lib/check_skill_refs.py"])
    parts = [f"pytest_rc={py_rc}"]
    if py_last:
        parts.append(py_last)
    if py_rc == 0 and "skipped" not in py_last:
        # skipped 计数显式可见：摘要无 skipped（全绿无跳过）补 0；
        # pytest 失败/崩溃不补——兜底会为崩溃的运行伪造计数
        parts.append("skipped=0")
    parts.append(f"refs_rc={refs_rc}")
    if refs_last:
        parts.append(refs_last)
    print(" | ".join(parts))
    return 0


if __name__ == "__main__":
    sys.exit(main())