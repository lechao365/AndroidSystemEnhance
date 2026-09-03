"""-s 批次自检：直取 pytest 与 check_skill_refs 的真实退出码。

动因：shell 内联 `X=$(cmd | tail -1); XRC=${PIPESTATUS[0]}` 中命令替换
赋值会把 PIPESTATUS 重置为单个 0（命令替换整体退出码，即 tail 的），
导致 rc 恒零、按 rc 的门禁失效（2026-08-31 实测确认）。本脚本用
subprocess 不经管道直取 returncode，如实透出两工具结果。

计数行只认 stdout（2026-08-31 硬化）：pytest 任何 stderr 输出都会顶掉
拼接输出里的末行计数，而 py_rc=0 时会补 skipped=0——收据即谎报零跳过
（emit 实测 39 skipped，C10 方向 3 的兜底伪造计数以 Python 形态复发）。
故 pytest 摘要行仅从 stdout 用正则定位（含 passed/failed/skipped 的行），
未定位到计数行即不补 skipped：交 ws_report 缺 skipped 拒写，不自己伪造
也不静默通过。refs 结论行同理只取 stdout 末行，stderr 仅附注不参与判定。

输出单行（| 连接，供 ws_report --selfcheck 落盘与门禁判定）：
    pytest_rc=<n> | <pytest 摘要行> | [slow5: <最慢5用例耗时;...>] | skipped=<n> | refs_rc=<n> | <refs 结论行> | config_rc=<n> | <config 结论行> | contract_rc=<n> | <contract 结论行>
skipped=<n> 仅在 pytest_rc=0 且摘要无 skipped 时补 0。config_rc/contract_rc
为 check_config.py 两模式（配置治理/契约检查，方向 4 接入）；ws_report 按
全部 *_rc 键判红（任一非零拒写收据）。退出码恒 0：拒写与否由 ws_report
按 rc 判定，本脚本只负责如实采集（emit 侧可独立自测）。
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# pytest 摘要计数行：含 passed/failed/skipped 任一计数的行（形如
# "531 passed in 27.9s"、"121 passed, 3 skipped in 6.0s"、"1 failed, ..."）
_COUNT_RE = re.compile(r"\b(\d+\s+(?:passed|failed|skipped))\b")

# --durations 段行："<秒>s <phase> <nodeid>"（nodeid 不含空格）
_DUR_LINE_RE = re.compile(r"^(\d+\.\d+)s\s+(call|setup|teardown)\s+(\S+)$")


def durations_summary(stdout, keep=5):
    """从 pytest 输出提取最慢 N 用例耗时行（--durations 段，方向 3）。

    返回 ["13.03s call <nodeid>", ...]（≤keep 条）；无 durations 段（旧
    桩/崩溃输出）返空列表。段定位按 "slowest ... durations" 标题，段内
    首个非耗时行即结束（durations 表后面还有短摘要区）。
    """
    out = []
    zone = False
    for ln in stdout.splitlines():
        s = ln.strip()
        if not zone:
            if "slowest" in s.lower() and "durations" in s:
                zone = True
            continue
        m = _DUR_LINE_RE.match(s)
        if m:
            out.append(f"{m.group(1)}s {m.group(2)} {m.group(3)}")
            if len(out) >= keep:
                break
        elif out:
            break
    return out


def run_tool(cmd):
    """直取工具 returncode（不经管道），返回 (returncode, stdout, stderr)。"""
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=ROOT)
    return proc.returncode, proc.stdout, proc.stderr


def pytest_summary(stdout):
    """从 stdout 定位 pytest 计数摘要行（含 passed/failed/skipped 计数）；
    未定位返回 ""（不取 stderr，防任何告警顶掉计数行）。"""
    for ln in stdout.splitlines():
        if _COUNT_RE.search(ln):
            return ln.strip()
    return ""


def last_stdout_line(stdout):
    """stdout 末非空行（refs 结论行）；stderr 仅附注不参与判定。"""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _mark_selfcheck():
    """自发 apply_selfcheck 打点：自检完成即 mark，供收据兜底段归因。

    15 笔 -s 收据兜底段在 0.26~361.9s 间乱跳而自检恒 11s 档——收据在
    push 之前落盘，兜底段实为"末个 mark 到算段时刻"，含自检与编排空转，
    不细分无法归因。自发 mark 后该段收窄为"自检完成→算段时刻"。
    batch 识别复用 cdp_timing mark 三级回落（显式 --batch > 环境变量
    CDP_BATCH_ID > log 目录唯一 timings 文件）；发点失败不阻断（打点
    诊断数据，非自检结果本身）。
    """
    timing = ROOT / "harness" / "skills" / "cross-device" / "lib" / "python" / "cdp_timing.py"
    try:
        proc = subprocess.run([sys.executable, str(timing), "mark",
                               "--name", "apply_selfcheck"],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=ROOT, timeout=10)
        if proc.returncode != 0:
            print(f"warn: apply_selfcheck 打点失败（不阻断）: {proc.stderr.strip()}",
                  file=sys.stderr)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"warn: apply_selfcheck 打点失败（不阻断）: {e}", file=sys.stderr)


def main():
    pytest_cmd = [sys.executable, "-m", "pytest", "harness", "-q",
                  "--durations=5"]
    # xdist 可导入时并行跑（-n auto 按 CPU 核数分流，apply 侧 586 项串行 30s
    # → 并行显著提速）；导入不到照旧串行。计数行正则不动（-q + -n auto 摘要
    # 行格式与串行一致，仍含 passed/skipped 计数）
    try:
        import xdist  # noqa: F401
        pytest_cmd += ["-n", "auto"]
    except ImportError:
        pass
    py_rc, py_out, py_err = run_tool(pytest_cmd)
    # refs 用 ROOT 拼绝对路径调用，不依赖 cwd（任意工作目录下自检结果一致）
    refs_rc, refs_out, refs_err = run_tool([sys.executable,
                                            str(ROOT / "harness" / "lib"
                                                / "check_skill_refs.py")])
    # 配置治理与契约检查（方向 4）：check_config 两模式各透出一个 rc
    cfg_rc, cfg_out, _ = run_tool([sys.executable,
                                   str(ROOT / "harness" / "lib"
                                       / "check_config.py")])
    ctr_rc, ctr_out, _ = run_tool([sys.executable,
                                   str(ROOT / "harness" / "lib"
                                       / "check_config.py"), "--contract"])
    summary = pytest_summary(py_out)
    parts = [f"pytest_rc={py_rc}"]
    if summary:
        parts.append(summary)
    # 最慢 5 用例耗时（方向 3）：回归定位慢点（xdist 分发波动时慢点即
    # 实时等待混入或真实子进程语义未豁免）
    durs = durations_summary(py_out)
    if durs:
        parts.append("slow5: " + "; ".join(durs))
    if py_rc == 0 and summary and "skipped" not in summary:
        # 仅定位到计数行且全绿无跳过时才补 skipped=0（平台跳过数显式可见）；
        # 未定位到计数行（stderr 顶掉/崩溃）即不补——交 ws_report 缺 skipped
        # 拒写，不自己伪造计数
        parts.append("skipped=0")
    parts.append(f"refs_rc={refs_rc}")
    refs_last = last_stdout_line(refs_out)
    if refs_last:
        parts.append(refs_last)
    # 配置/契约两 rc（方向 4）：结论末行随摘要拼接收据（任一非零由
    # ws_report 全 rc 扫描判红拒写）
    parts.append(f"config_rc={cfg_rc}")
    cfg_last = last_stdout_line(cfg_out)
    if cfg_last:
        parts.append(cfg_last)
    parts.append(f"contract_rc={ctr_rc}")
    ctr_last = last_stdout_line(ctr_out)
    if ctr_last:
        parts.append(ctr_last)
    print(" | ".join(parts))
    _mark_selfcheck()
    return 0


if __name__ == "__main__":
    sys.exit(main())