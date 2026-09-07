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
    pytest_rc=<n> | <pytest 摘要行> | [slow5: <最慢5用例耗时;...>] | skipped=<n> | refs_rc=<n> | <refs 结论行> | config_rc=<n> | <config 结论行> | contract_rc=<n> | <contract 结论行> | pyenv_rc=<n> | <pyenv 汇总行> | ioctl_rc=<n> | <ioctl 结论行> | manifest_rc=<n> | <manifest 结论行> | durs: py=<s> tools=<s> pyenv=<s> ioctl=<s> manifest=<s>
skipped=<n> 仅在 pytest_rc=0 且摘要无 skipped 时补 0。config_rc/contract_rc
为 check_config.py 两模式（配置治理/契约检查，方向 4 接入）；pyenv_rc 为
check_python_env 探测结果（Python 版本 + requirements.txt 依赖，环境破损
时后续工具结论均不可信）；ioctl_rc 为 check_ioctl_headers 内核/AOSP ioctl
头一致性结果（方向 2，双空/漂移判红透出）；manifest_rc 为 gen_manifest
--check-only 的 code/rpi5 manifest 登记完整性结果（方向 2，未登记/有变化
判红透出）；ws_report 按
全部 *_rc 键判红（任一非零拒写收据）。退出码恒 0：拒写与否由 ws_report
按 rc 判定，本脚本只负责如实采集（emit 侧可独立自测）。
"""
import argparse
import importlib
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# ws_report 必查键集合（单点定义，方向 3）：selfcheck 输出的 *_rc 中这些键
# 任一缺失即拒写收据。ws_report.py 必查循环、test_workflow_ci 断言、CI
# workflow 均以本集合为口径基准，新增 rc（pyenv_rc/ioctl_rc/manifest_rc 等）
# 必须同步进本常量，防单侧漏接线致判红静默失效。
REQUIRED_RC_KEYS = ("pytest_rc", "refs_rc", "config_rc", "contract_rc",
                    "pyenv_rc", "ioctl_rc", "manifest_rc",
                    "discipline_rc", "scan_rc")

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


def timed_run(cmd, timeout=None):
    """run_tool + 真实墙钟耗时（方向 6）：返回 (rc, stdout, stderr, dur_s)。
    逐检查器耗时入自检输出行，emit 侧定位耗时瓶颈（慢点归因不再只看
    pytest --durations 与 gap 段）。"""
    _t0 = time.time()
    rc, out, err = run_tool(cmd, timeout=timeout)
    return rc, out, err, time.time() - _t0


# 治理工具超时上限（秒）：refs/config 实测 ~25s（全量扫描 refs 索引 + yaml
# 治理），放宽 4 倍兜住挂死（方向 2 订正：此前注释称 2~4s 与实测差一个数量级）
_TOOL_TIMEOUT_S = 120
# pytest 超时上限（秒）：xdist 全量正常 ~25s（WSL2 drvfs ~60s），兜挂死
_PYTEST_TIMEOUT_S = 900


def _spawn_cmd(cmd):
    """Popen 启动单个工具（非阻塞，方向 1：与 pytest 重叠跑，收口在
    _collect_cmd）。cwd=ROOT 与 run_tool 一致。"""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8", errors="replace", cwd=ROOT)
    # 方向 4：durs 自 Popen 时刻起（此前 _t0 记在收口时刻，重叠进程
    # communicate 立即返回致 durs 恒 0，emit 无法定位真实耗时）
    proc._spawn_t0 = time.time()

    def _wait_exit():
        # 方向 3：wait 线程记进程真实退出时刻（_exit_t0）。收口在 pytest
        # 之后发生，若 dur 取收口时刻减 spawn，六项 durs 恒等 pytest 总时长
        # ——改取退出减 spawn 才反映工具真实运行时长。
        try:
            proc.wait()
        except Exception:
            pass
        finally:
            proc._exit_t0 = time.time()

    threading.Thread(target=_wait_exit, daemon=True).start()
    return proc


def _collect_cmd(proc, name, timeout=_TOOL_TIMEOUT_S):
    """收口单个 Popen：communicate + 墙钟，返回 (rc, stdout, stderr, dur_s)。
    超时 kill 返 rc=124（约定超时标记，B3 兜底挂死）。dur_s = 进程退出时刻
    （wait 线程记）减 Popen 时刻（方向 3：工具真实运行时长；wait 线程未记
    即收口时刻——communicate 返回即退出，近似一致）。"""
    _t0 = getattr(proc, "_spawn_t0", time.time())
    try:
        out, err = proc.communicate(timeout=timeout)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        out, err = proc.communicate()
        print(f"warn: 治理工具超时（>{timeout}s，rc=124）: {name}",
              file=sys.stderr)
        rc, err = 124, f"timeout after {timeout}s"
    exit_t0 = getattr(proc, "_exit_t0", None)
    # mock/异常形态下 _exit_t0 可能非数值（unittest mock 自动属性），回落收口
    if not isinstance(exit_t0, (int, float)):
        exit_t0 = None
    end = exit_t0 if exit_t0 is not None else time.time()
    return rc, out, err, end - _t0


def _spawn_tools():
    """并行启动 refs 与 config/contract（B2，方向 1 拆两阶段）：仅 Popen
    不阻塞，主流程随后跑 pytest 与治理重叠，收口在 _collect_tools。"""
    return {
        "refs": _spawn_cmd(
            [sys.executable, str(ROOT / "harness" / "lib" / "check_skill_refs.py")]),
        "cfg": _spawn_cmd(
            [sys.executable, str(ROOT / "harness" / "lib" / "check_config.py"),
             "--all"]),
        "discipline": _spawn_cmd(
            [sys.executable, str(ROOT / "harness" / "lib"
                                 / "check_test_discipline.py")]),
        "scan": _spawn_cmd(
            [sys.executable, str(ROOT / "harness" / "lib"
                                 / "check_hot_path_scan.py")]),
    }


def _collect_tools(procs):
    """收口 refs/cfg（communicate + 各自墙钟），解析 --all 机器行。

    check_config --all 单进程双模式（消两遍 yaml 导入/全量扫描，B2）：
    末尾 config_rc=/contract_rc= 机器行分别解析，结论行按 label 前缀
    分别提取（与单模式 last_stdout_line 口径兼容）。
    返回 (tools dict, refs_dur, cfg_dur, dis_dur, scan_dur)：tools 形态与
    run_parallel_tools 一致（refs/cfg/ctr/discipline/scan 五元组），四个 dur
    供 durs 拆开自报（方向 2）。
    """
    refs_rc, refs_out, refs_err, refs_dur = _collect_cmd(procs["refs"], "refs")
    cfg_rc_raw, cfg_out, _, cfg_dur = _collect_cmd(procs["cfg"], "cfg")
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
    # 方向 1/2 新增守卫：discipline（测试改动禁新增 xfail/skip/sleep 重试）
    # 与 scan（热路径禁全树 rglob/os.walk）并行收口，各自 rc 与结论行透出
    dis_rc, dis_out, _, dis_dur = _collect_cmd(procs["discipline"], "discipline")
    scan_rc, scan_out, _, scan_dur = _collect_cmd(procs["scan"], "scan")
    tools = {"refs": (refs_rc, refs_out, refs_err),
             "cfg": (cfg_rc, cfg_out, cfg_last),
             "ctr": (ctr_rc, ctr_out, ctr_last),
             "discipline": (dis_rc, dis_out, ""),
             "scan": (scan_rc, scan_out, "")}
    return tools, refs_dur, cfg_dur, dis_dur, scan_dur


def run_parallel_tools():
    """refs 与 config/contract 并行采集（B2 组合接口，测试/兼容用）。

    两阶段 _spawn_tools + _collect_tools 的即时组合（墙钟取 max 而非 sum）；
    main 已拆两阶段与 pytest/ioctl/manifest 全重叠（方向 1），本函数保留
    原接口供 TestParallelTools 与外部调用。
    """
    procs = _spawn_tools()
    tools, _, _, _, _ = _collect_tools(procs)
    return tools


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


# requirements.txt 包名 → import 名归一（pip 名与 import 名不一致的已知项）
_REQ_IMPORT_ALIASES = {"pyyaml": "yaml"}


def _parse_requirement_names(req_path):
    """解析 requirements.txt，产出 (spec, 包名) 列表（# 后视为注释）。

    spec 为去注释后的整行（如 "PyYAML>=6.0"，供汇总行点名）；包名取行首
    合法包名字符（版本约束符号前缀）。无法解析出包名的行跳过。
    """
    reqs = []
    for raw in req_path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)", line)
        if m:
            reqs.append((line, m.group(1)))
    return reqs


def check_python_env(req_path=None):
    """检查 Python 运行环境：解释器版本（>=3.8）与 requirements.txt 依赖可用性。

    读仓库根 requirements.txt（req_path 可覆盖，供测试注入临时清单）逐项
    import 探测（包名按 _REQ_IMPORT_ALIASES 归一，如 PyYAML→yaml）。
    任何失败不抛异常，返回 (ok, summary)：ok 为总判定（版本达标且全部
    依赖可导入），summary 为汇总行（python 版本结论 + 各依赖 OK/MISSING），
    供 main 拼接收据；环境破损时 pyenv_rc 非零交由 ws_report 判红。
    """
    vi = sys.version_info
    ver = f"{vi.major}.{vi.minor}.{vi.micro}"
    ver_ok = vi >= (3, 8)
    ver_text = f"python={ver} " + ("OK(>=3.8)" if ver_ok else "TOO_OLD(<3.8)")
    path = Path(req_path) if req_path else ROOT / "requirements.txt"
    try:
        reqs = _parse_requirement_names(path)
    except OSError as e:
        return False, f"{ver_text}; requirements.txt 读取失败: {e}"
    if not reqs:
        return False, f"{ver_text}; requirements.txt 无有效依赖项"
    ok = ver_ok
    parts = []
    for spec, pkg in reqs:
        import_name = _REQ_IMPORT_ALIASES.get(
            pkg.lower(), pkg.lower().replace("-", "_"))
        try:
            importlib.import_module(import_name)
            parts.append(f"{spec} OK")
        except Exception as e:  # 依赖探测：任何导入失败均记 MISSING，不抛出
            ok = False
            parts.append(f"{spec} MISSING({type(e).__name__})")
    return ok, f"{ver_text}; deps: {'; '.join(parts)}"


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


# ── 方向 1：偶现失败机械判定（KIR-002 抖动登记 / KIR-001 不得顺延）──────────
_FAILED_RE = re.compile(r"^FAILED (\S+)", re.M)


def _failed_nodeids(py_out):
    """从 pytest -q 输出提取失败用例 nodeid（"FAILED <nodeid> - reason" 行）。"""
    return sorted(set(_FAILED_RE.findall(py_out)))


def _git_changed_files():
    """本批改动文件（git diff --name-only HEAD，相对 ROOT）；无 git/无改动返空集。"""
    try:
        r = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except Exception:
        return set()
    if r.returncode != 0:
        return set()
    return {ln for ln in r.stdout.splitlines() if ln}


def _current_batch_id():
    """当前活跃 batch_id（cdp_timing.resolve_batch_id 回落）；无则空串。"""
    try:
        if _import_cdp_timing():
            return cdp_timing.resolve_batch_id() or ""
    except Exception:
        pass
    return ""


def _load_cdp_issue():
    """延迟导入 cdp_issue（cross-device lib），失败返 None。"""
    try:
        issue_dir = ROOT / "harness" / "skills" / "cross-device" / "lib" / "python"
        if str(issue_dir) not in sys.path:
            sys.path.insert(0, str(issue_dir))
        import cdp_issue
        return cdp_issue
    except Exception as e:
        print(f"warn: cdp_issue 导入失败（抖动登记降级，不阻断）: {e}",
              file=sys.stderr)
        return None


def _flake_history(nodeid):
    """既有 kind=flake 条目中该 nodeid 的 (轮次, 首现批次)；无则 (0, "")。"""
    issues_dir = ROOT / "data" / "known-issues"
    if not issues_dir.is_dir():
        return 0, ""
    max_round, first_batch = 0, ""
    for p in sorted(issues_dir.glob("*.md")):
        if p.name == "index.md":
            continue
        try:
            txt = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if f"nodeid: {nodeid}" not in txt:
            continue
        if not re.search(r"^- kind: flake$", txt, re.M):
            continue
        rm = re.search(r"^- round: (\d+)$", txt, re.M)
        if rm:
            max_round = max(max_round, int(rm.group(1)))
        fm = re.search(r"^- first_seen_batch: (\S+)$", txt, re.M)
        if fm and not first_batch:
            first_batch = fm.group(1)
    return max_round, first_batch


def _register_flake_issue(nodeid):
    """按 KIR-002 登记抖动 known-issue（记用例名/轮次/首现批次/复现命令）。

    返回 (nodeid, round, first_batch)。轮次 = 既有该用例 flake 条目数 + 1，
    首现批次沿用最早条目（同一用例抖动跨批归同一 flake 记录链）。
    """
    round_n, first_batch = _flake_history(nodeid)
    batch_id = _current_batch_id()
    if not first_batch:
        first_batch, round_n = batch_id or "unknown", 1
    else:
        round_n += 1
    issue_id = f"KI-FLAKE-{first_batch}-{abs(hash(nodeid)) & 0xFFF:03x}"
    body = (f"- nodeid: {nodeid}\n"
            f"- round: {round_n}\n"
            f"- first_seen_batch: {first_batch}\n"
            f"- rerun_cmd: python3 -m pytest {nodeid} -q\n"
            f"- rerun_result: 全新进程单独重跑全部通过（KIR-002 抖动，非阻塞，"
            f"放行本轮；未闭环 flake 阻断 promote）")
    cdpi = _load_cdp_issue()
    if cdpi is not None:
        try:
            issue = cdpi.Issue(
                issue_id=issue_id, title=f"[flake] {nodeid}",
                kind="flake", origin="pre-existing", blocking=False,
                status="open", task="", discovered_in=first_batch,
                batch_id=batch_id or "0" * 12)
            cdpi.write_issue(issue, body)
        except Exception as e:
            print(f"warn: flake 抖动登记失败（不阻断）: {e}", file=sys.stderr)
    return nodeid, round_n, first_batch


def _rerun_failures(py_out):
    """方向 1：全量红时在全新进程单独重跑失败用例的机械判定。

    返回 (flake_notes, ki001_hits)：
      flake_notes: [(nodeid, round, first_batch)]——全部单跑绿（KIR-002 抖动，
        已登记 known-issues 放行本轮）；
      ki001_hits: [nodeid]——失败用例命中本批改动路径（KIR-001 引入嫌疑，
        不得顺延，当批修，pytest_rc 保持非零）；
    任一单跑仍红（真回归阻塞）→ 两项皆空（pytest_rc 保持非零）。
    """
    nodeids = _failed_nodeids(py_out)
    if not nodeids:
        return [], []
    changed = _git_changed_files()
    flake_notes, ki001_hits = [], []
    for nodeid in nodeids:
        test_file = nodeid.split("::", 1)[0]
        # 全新进程单独重跑（禁 slow_guard——慢用例单跑会被守卫误判红；
        # 单进程避免 xdist 分片抖动干扰）
        env = dict(os.environ)
        env["SLOW_GUARD_OFF"] = "1"
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pytest", nodeid, "-q"], cwd=ROOT,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=env, timeout=_PYTEST_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return [], []  # 重跑挂死按真回归阻塞处理
        if r.returncode != 0:
            return [], []  # 单跑仍红 → 真回归阻塞
        if test_file in changed:
            ki001_hits.append(nodeid)  # KIR-001：本批引入嫌疑，不得顺延
        else:
            flake_notes.append(_register_flake_issue(nodeid))
    return flake_notes, ki001_hits


# ── 方向 2：quick 档（git diff 推导受影响测试；推导不出回落全量）────────────
def _quick_test_targets():
    """git diff 推导受影响测试文件；推导不出（无 git/无改动/无法映射）返 None
    → 回落全量（保守，保证覆盖）。规则：
      - 改动文件本身是 tests/test_*.py → 直接跑；
      - harness/lib/<x>.py → harness/lib/tests/test_<x>.py；
      - harness/skills/<skill>/<...>/<x>.py → harness/skills/<skill>/tests/test_<x>.py；
      - 其余（非 .py / 无对应测试）→ 推导不出回落全量。
    """
    changed = _git_changed_files()
    if not changed:
        return None
    targets = set()
    for rel in changed:
        p = Path(rel)
        if p.suffix != ".py":
            return None
        parts = p.parts
        if "tests" in parts and p.name.startswith("test_"):
            targets.add(rel)
            continue
        if p.name.startswith("test_"):
            return None
        if len(parts) >= 2 and parts[0] == "harness" and parts[1] == "lib":
            cand = f"harness/lib/tests/test_{p.stem}.py"
        elif len(parts) >= 3 and parts[0] == "harness" and parts[1] == "skills":
            skill = parts[2]
            cand = f"harness/skills/{skill}/tests/test_{p.stem}.py"
        else:
            return None
        if not (ROOT / cand).is_file():
            return None
        targets.add(cand)
    return sorted(targets)


def main(argv=None):
    # 方向 4：重配标准输出为 utf-8（对齐 harness_lib.harness_init），防 GBK
    # 终端把摘要中的中文/非 ASCII 替换成 � 致自检结论行打印失败或被误判
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    # 方向 2：quick 档（git diff 推导受影响测试，推导不出回落全量）
    mode = "full"
    if argv:
        parser = argparse.ArgumentParser(description="harness 自检（-s 采集）")
        parser.add_argument("--mode", choices=("full", "quick"), default="full",
                            help="full 全量 harness；quick 由 git diff 推导"
                                 "受影响测试（推导不出回落全量），供 loop 中间轮")
        args = parser.parse_args(argv)
        mode = args.mode
    # 自检整体墙钟实测（方向 3）：pytest 起跑前记 t0，四工具完成后 t1，
    # 差值经 _mark_selfcheck --dur-s 上报（自检段耗时不再被相邻差额吞并）
    _t0 = time.time()
    _ensure_edit_close_mark()
    if mode == "quick":
        quick_targets = _quick_test_targets()
        # 推导不出（无 git/无改动/无法映射）回落全量（保守，保证覆盖）
        scope = quick_targets or ["harness"]
    else:
        scope = ["harness"]
    pytest_cmd = [sys.executable, "-m", "pytest", *scope, "-q",
                  "--durations=5"]
    # xdist 可导入时并行跑（-n auto 按 CPU 核数分流，apply 侧 586 项串行 30s
    # → 并行显著提速）；导入不到照旧串行。计数行正则不动（-q + -n auto 摘要
    # 行格式与串行一致，仍含 passed/skipped 计数）
    try:
        import xdist  # noqa: F401
        pytest_cmd += ["-n", "auto"]
    except ImportError:
        pass
    # 方向 1：先 Popen 全部治理工具（refs/cfg/ioctl/manifest）再跑 pytest，
    # 全重叠后收口——实测 py ~24.8s 与 tools ~27s 可完全重叠，单轮省约 26s
    tools_procs = _spawn_tools()
    ioctl_proc = _spawn_cmd(
        [sys.executable, str(ROOT / "harness" / "lib" / "check_ioctl_headers.py")])
    manifest_proc = _spawn_cmd(
        [sys.executable, str(ROOT / "harness" / "skills" / "cross-device"
                             / "lib" / "python" / "gen_manifest.py"), "--check-only"])
    py_rc, py_out, py_err, py_dur = timed_run(
        pytest_cmd, timeout=_PYTEST_TIMEOUT_S)
    # pytest 跑完收口治理（各进程已与 pytest 重叠，墙钟取 max 而非 sum）
    (tools, refs_dur, cfg_dur, dis_dur, scan_dur) = _collect_tools(tools_procs)
    ioctl_rc, ioctl_out, _, ioctl_dur = _collect_cmd(ioctl_proc, "ioctl")
    manifest_rc, manifest_out, _, manifest_dur = _collect_cmd(
        manifest_proc, "manifest")
    refs_rc, refs_out, refs_err = tools["refs"]
    cfg_rc, cfg_out, cfg_last = tools["cfg"]
    ctr_rc, ctr_out, ctr_last = tools["ctr"]
    dis_rc, dis_out, _ = tools["discipline"]
    scan_rc, scan_out, _ = tools["scan"]
    summary = pytest_summary(py_out)
    # 方向 1：全量红时机械判定——全新进程单独重跑失败用例，单跑绿即
    # KIR-002 抖动（自动登记 known-issues 放行本轮），单跑红判真回归阻塞，
    # KIR-001 命中者不得顺延（pytest_rc 保持非零）
    flake_notes, ki001_hits = [], []
    if py_rc != 0:
        flake_notes, ki001_hits = _rerun_failures(py_out)
        if flake_notes and not ki001_hits:
            py_rc = 0  # 全部单跑绿且无 KIR-001 嫌疑 → 抖动放行本轮
    parts = [f"pytest_rc={py_rc}"]
    if flake_notes and not ki001_hits:
        # 抖动放行：原始 failed 计数不拼（ws_report 文本防线见 failed 即拒写），
        # 改拼 flake 标注（用例名/轮次/首现批次/复现命令，收据可见可追踪）
        for nodeid, round_n, first_batch in flake_notes:
            parts.append(f"flake: {nodeid} round={round_n} first={first_batch} "
                         f'cmd="python3 -m pytest {nodeid} -q"')
        m = re.search(r"\b(\d+)\s*skipped\b", summary or "")
        parts.append(f"skipped={m.group(1) if m else 0}")
    else:
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
    # Python 运行环境探测（版本 + requirements.txt 依赖）：环境破损（缺
    # yaml 等）时后续工具结论均不可信，pyenv_rc 非零交 ws_report 全
    # *_rc 扫描判红拒写
    _env_t0 = time.time()
    env_ok, env_summary = check_python_env()
    env_dur = time.time() - _env_t0
    parts.append(f"pyenv_rc={0 if env_ok else 1}")
    if env_summary:
        parts.append(env_summary)
    # 内核/AOSP ioctl 头一致性（方向 2）：此前 check_ioctl_headers 无调用方，
    # 头文件单侧漂移/双空解析异常静默无感；接入自检后 ioctl_rc 透出，双空
    # 判红在 check_ioctl_headers 内部完成，非零由 ws_report 全 *_rc 判红拒写
    parts.append(f"ioctl_rc={ioctl_rc}")
    ioctl_last = last_stdout_line(ioctl_out)
    if ioctl_last:
        parts.append(ioctl_last)
    # manifest 登记完整性（方向 2）：gen_manifest --check-only 未登记文件或有
    # 变化均判红（--check-only 有变化返非零），manifest_rc 透出交 ws_report
    # 全 *_rc 判红拒写（此前 --check-only 无调用方，manifest 漂移静默无感）
    parts.append(f"manifest_rc={manifest_rc}")
    manifest_last = last_stdout_line(manifest_out)
    if manifest_last:
        parts.append(manifest_last)
    # 测试改动纪律（方向 1，IDLE-006 机械化）：discipline_rc 透出——测试改动
    # 新增 xfail/skip/sleep 重试即判红，交 ws_report 全 *_rc 判红拒写
    parts.append(f"discipline_rc={dis_rc}")
    dis_last = last_stdout_line(dis_out)
    if dis_last:
        parts.append(dis_last)
    # 热路径遍历约束（方向 2）：scan_rc 透出——治理检查器热路径全树
    # rglob/os.walk 即判红，防 refs 39s 回归重现
    parts.append(f"scan_rc={scan_rc}")
    scan_last = last_stdout_line(scan_out)
    if scan_last:
        parts.append(scan_last)
    # 方向 2 + 方向 6：逐检查器耗时（秒，一位小数）入输出行，refs/cfg 拆开
    # 各自自报（合并 tools 无法定位慢点）。重叠模型（方向 1）下语义：
    # py 为 pytest 进程总耗时；refs/cfg/ioctl/manifest 为其收口阻塞墙钟
    # （≈进程对主流程耗时的贡献：≈0 即进程在 pytest 期间已完成不拖慢，
    #  显著非零即收口仍在等待/管道排空——emit 据此定位拖慢主流程的检查器）。
    # 前缀 *_dur 不匹配 ws_report 的 *_rc 判红正则，不干扰 rc 判定
    parts.append(f"durs: py={py_dur:.1f} refs={refs_dur:.1f} "
                 f"cfg={cfg_dur:.1f} pyenv={env_dur:.1f} "
                 f"ioctl={ioctl_dur:.1f} manifest={manifest_dur:.1f} "
                 f"discipline={dis_dur:.1f} scan={scan_dur:.1f}")
    print(" | ".join(parts))
    _mark_selfcheck(dur_s=time.time() - _t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())