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
import os
import re
import subprocess
import sys
import time
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


def run_tool(cmd, timeout=None):
    """直取工具 returncode（不经管道），返回 (returncode, stdout, stderr)。

    timeout（B3）：超时 kill 并返 rc=124（约定超时标记），防 pytest/refs
    挂起无限阻塞自检链与 CI（真子进程类用例逃过 slow_guard setup 阶段时
    的兜底）。
    """
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", cwd=ROOT,
                              timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", errors="replace") \
            if isinstance(e.stdout, bytes) else (e.stdout or "")
        print(f"warn: 工具超时（>{timeout}s，rc=124）: {cmd[:2]}", file=sys.stderr)
        return 124, out, f"timeout after {timeout}s"


# 治理工具超时上限（秒）：refs/config 正常 2~4s，放宽 20 倍仍能兜住挂死
_TOOL_TIMEOUT_S = 120
# pytest 超时上限（秒）：xdist 全量正常 ~25s（WSL2 drvfs ~60s），兜挂死
_PYTEST_TIMEOUT_S = 900


def run_parallel_tools():
    """refs 与 config/contract 并行采集（B2）：两进程同时拉起，墙钟取
    max 而非 sum（治理进程冷启动与 pytest 峰值错峰）。

    check_config --all 单进程双模式（消两遍 yaml 导入/全量扫描，B2）：
    末尾 config_rc=/contract_rc= 机器行分别解析，结论行按 label 前缀
    分别提取（与单模式 last_stdout_line 口径兼容）。
    返回 dict：
      refs: (rc, stdout, stderr)
      cfg:  (rc, stdout, 结论行)
      ctr:  (rc, stdout, 结论行)
    """
    procs = {
        "refs": subprocess.Popen(
            [sys.executable, str(ROOT / "harness" / "lib" / "check_skill_refs.py")],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", cwd=ROOT),
        "cfg": subprocess.Popen(
            [sys.executable, str(ROOT / "harness" / "lib" / "check_config.py"),
             "--all"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", cwd=ROOT),
    }
    results = {}
    for key, proc in procs.items():
        try:
            out, err = proc.communicate(timeout=_TOOL_TIMEOUT_S)
            results[key] = (proc.returncode, out, err)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            print(f"warn: 治理工具超时（>{_TOOL_TIMEOUT_S}s，rc=124）: {key}",
                  file=sys.stderr)
            results[key] = (124, out or "", f"timeout after {_TOOL_TIMEOUT_S}s")
    refs_rc, refs_out, refs_err = results["refs"]
    cfg_rc_raw, cfg_out, _ = results["cfg"]
    # --all 末尾机器 rc 行解析；异常形态（旧版无机器行/输出损坏）按整体
    # rc 兜底双段，结论行回落全文末行保摘要可见性
    cfg_rc, ctr_rc = cfg_rc_raw, cfg_rc_raw
    cfg_last, ctr_last = _extract_all_mode_lines(cfg_out)
    ctr_out = _split_contract_section(cfg_out, ctr_last) if ctr_last else cfg_out
    if cfg_last and ctr_last:
        m_cfg = re.search(r"config_rc=(\d+)", cfg_out)
        m_ctr = re.search(r"contract_rc=(\d+)", cfg_out)
        if m_cfg and m_ctr:
            cfg_rc, ctr_rc = int(m_cfg.group(1)), int(m_ctr.group(1))
    elif not ctr_last:
        ctr_last = last_stdout_line(cfg_out)
    return {"refs": (refs_rc, refs_out, refs_err),
            "cfg": (cfg_rc, cfg_out, cfg_last),
            "ctr": (ctr_rc, ctr_out, ctr_last)}


def _extract_all_mode_lines(out):
    """--all 输出中分别提取 config 与 contract 结论行（OK/违规结论）。"""
    cfg_last = ctr_last = ""
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s.startswith("OK: config ") or s.startswith("==== config:"):
            cfg_last = s
        elif s.startswith("OK: contract ") or s.startswith("==== contract:"):
            ctr_last = s
    return cfg_last, ctr_last


def _split_contract_section(cfg_out, ctr_last):
    """从 --all 全文切出 contract 段（其结论行起至机器 rc 行前）。"""
    lines = cfg_out.splitlines()
    try:
        start = lines.index(ctr_last)
    except ValueError:
        return ctr_last
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("config_rc=") or lines[i].startswith("contract_rc="):
            end = i
            break
    return "\n".join(lines[start:end])


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


# cdp_timing 模块引用（_import_cdp_timing 成功后置位；打点诊断数据，
# 导入失败降级不打点但不阻断自检——留痕防 KI-20260902-001 同类漂移不可见）
cdp_timing = None


def _mark_selfcheck(dur_s=None):
    """自发 apply_selfcheck 打点：自检完成即 mark，供收据兜底段归因。

    15 笔 -s 收据兜底段在 0.26~361.9s 间乱跳而自检恒 11s 档——收据在
    push 之前落盘，兜底段实为"末个 mark 到算段时刻"，含自检与编排空转，
    不细分无法归因。自发 mark 后该段收窄为"自检完成→算段时刻"。
    dur_s（方向 3）：调用方实测自检整体墙钟，经 emit_mark --dur-s 上报，
    compute_segments 归因时 apply_selfcheck 段耗时取实测值（不再被相邻
    差额吞并/夸大），编排空转余量落 gap_before_apply_selfcheck。
    batch 识别复用 emit_mark 四级回落（显式 batch_id > CDP_BATCH_ID >
    current-batch.json）；进程内直调（B8/C-1 打点胶水收敛，消除子进程
    spawn 开销），发点失败不阻断（打点诊断数据，非自检结果本身）。
    """
    if _import_cdp_timing():
        cdp_timing.emit_mark("apply_selfcheck", dur_s=dur_s)


def _ensure_edit_close_mark():
    """编辑收口自动补打（B1，根治 KI-20260902-001）。

    mark edit 此前由 apply AI 自判"编辑完成"——-s 批实测漂移（edit mark
    打在两轮自检之后，真实编辑散落 gap_before_apply_selfcheck），edit 段
    口径跨批不可比。制度化：本函数在自检起跑前判定编辑是否已收口，未收口
    则补打 mark edit（cdp_timing 同名 mark 自动 #N 序号）：
      - marks 无 edit → 补打（AI 漏打）
      - 末个 edit 在末个 apply_selfcheck 之后 → 补打（loop 轮修复编辑 /
        -s 批漂移形态，把后续真实编辑重新归口为 edit#N）
      - 其余（edit 已收口）→ 不动
    batch 定位与 marks 读取走 cdp_timing 公开 API（resolve_batch_id/
    read_marks，B8：不再依赖 _read_current_batch/_load/_timing_path 私有
    三件套，cdp_timing 重构不再静默破坏本兜底）；无活跃批（emit 侧独立
    自测）静默跳过。补打失败仅 warn 不阻断（打点诊断数据）。
    """
    try:
        if not _import_cdp_timing():
            return
        bid = cdp_timing.resolve_batch_id()
        if not bid:
            return
        marks = cdp_timing.read_marks(bid)
        if marks is None:
            return
        edit_idx = [i for i, m in enumerate(marks) if m.get("name") == "edit"]
        selfcheck_idx = [i for i, m in enumerate(marks)
                         if m.get("name") == "apply_selfcheck"]
        # 已收口：已有 edit 且（无自检 或 末个 edit 在末个 apply_selfcheck
        # 之后——正常 AI 手打路径）→ 不补
        if edit_idx and (not selfcheck_idx
                         or edit_idx[-1] > selfcheck_idx[-1]):
            return
        # 未收口：无 edit（AI 漏打）或末个 edit 早于末个自检（loop 轮修复
        # 编辑 / -s 批漂移形态）→ 补打，把后续真实编辑重新归口为 edit#N
        if not cdp_timing.emit_mark("edit", batch_id=bid):
            print("warn: edit 收口补打失败（不阻断）", file=sys.stderr)
    except Exception as e:  # 打点诊断数据，任何异常不得阻断自检
        print(f"warn: edit 收口判定失败（不阻断）: {e}", file=sys.stderr)


def _import_cdp_timing():
    """导入 cdp_timing（cross-device lib），失败仅 warn 返 False。

    打点属诊断数据：导入失败（仓结构异常）不得阻断自检，但必须留痕——
    静默失效即 KI-20260902-001 同类漂移不可见问题复发。
    """
    global cdp_timing
    if cdp_timing is not None:
        return True
    timing_dir = ROOT / "harness" / "skills" / "cross-device" / "lib" / "python"
    if str(timing_dir) not in sys.path:
        sys.path.insert(0, str(timing_dir))
    try:
        import cdp_timing as _ct
        cdp_timing = _ct
        return True
    except Exception as e:
        print(f"warn: cdp_timing 导入失败（打点降级，不阻断）: {e}",
              file=sys.stderr)
        return False


def main():
    # 方向 4：重配标准输出为 utf-8（对齐 harness_lib.harness_init），防 GBK
    # 终端把摘要中的中文/非 ASCII 替换成 � 致自检结论行打印失败或被误判
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    # 自检整体墙钟实测（方向 3）：pytest 起跑前记 t0，四工具完成后 t1，
    # 差值经 _mark_selfcheck --dur-s 上报（自检段耗时不再被相邻差额吞并）
    _t0 = time.time()
    _ensure_edit_close_mark()
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
    py_rc, py_out, py_err = run_tool(pytest_cmd, timeout=_PYTEST_TIMEOUT_S)
    # refs 与 config/contract 并行采集（B2：Popen 同时拉起 + --all 单进程
    # 双模式，治理墙钟由 sum 降为 max，两遍 yaml/全量扫描降为一遍）
    tools = run_parallel_tools()
    refs_rc, refs_out, refs_err = tools["refs"]
    cfg_rc, cfg_out, cfg_last = tools["cfg"]
    ctr_rc, ctr_out, ctr_last = tools["ctr"]
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
    # 配置/契约两 rc（方向 4）：结论行随摘要拼接收据（任一非零由
    # ws_report 全 rc 扫描判红拒写）；结论行由 --all 输出按 label 提取
    parts.append(f"config_rc={cfg_rc}")
    if cfg_last:
        parts.append(cfg_last)
    parts.append(f"contract_rc={ctr_rc}")
    if ctr_last:
        parts.append(ctr_last)
    print(" | ".join(parts))
    _mark_selfcheck(dur_s=time.time() - _t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())