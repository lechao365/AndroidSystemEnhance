"""loop-engineering 会话状态机：只管状态记账，不管执行。

职责边界（spec docs/superpowers/specs/2026-08-30-ws-loop-design.md）：
- 脚本（本文件）：session 记账 / 指纹归一化比对 / 双层计数 / 退出判定 / 报告骨架
- AI：失败分析 / 修复编辑 / 归因复核 / 诊断语义撰写
- attempt 推进唯一锚 = 收据落盘成功后调用 done（param_error 补参不产收据不耗轮次）

CLI:
  start    --goal <文本> (--batch-file <cdp> | --target <12hex|dev|main> --case <标签>)
           [--max-patience 3] [--max-total 10]
  run      --session <json>                          # 输出本轮 verify 执行指引
  done     --session <json> --receipt <路径> [--stage <sync|build|unit_test|push|acceptance>]
           [--error-line <首错误行>] [--attribution env_fail|framework_error]
  status   --session <json>
  diagnose --session <json>
退出码: 0 正常 / 1 会话状态错误 / 2 参数错误 / 3 session 文件非法
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path

# 复用仓内共享库（同 ws_report.py 路径注入方式；依赖全部内聚于本仓 harness/，
# 禁止引用 LcSkills/loop_core 等外部仓文件）：
# cross-device/lib/python: cdp_receipt（收据读取）/ cdp_parse（批次验收解析）/ cdp_paths（项目根）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_paths import project_root  # noqa: E402
from cdp_receipt import read_receipt  # noqa: E402

# 会话老化配额（spec §4.7）：目录级，仅删已终结会话
_SESSION_KEEP = 20

# 归一化规则：剥时间戳（三种格式）/ 家目录与 workspace 路径 / 十六进制地址。
# 数字不再归一化（_NUM_RE 已删）：错误行中的数值（端口/行号/计数）是语义稳定
# 部分，剥掉会把不同问题折叠成同一指纹（过激归一化），保留 TS/HOME/HEX 三类
# 纯易变字段即可满足误判防护（spec §4.3）。
# 三种格式：ISO 日期时间（YYYY-MM-DD[ T]HH:MM:SS(.ms)）、MM/DD HH:MM:SS、logcat MM-DD HH:MM:SS.mmm
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"|\d{2}/\d{2} \d{2}:\d{2}:\d{2}"
    r"|\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?"
)
_HOME_RE = re.compile(r"/home/[A-Za-z0-9_.-]+")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")


def normalize_error_line(text):
    """首错误行归一化：剥易变字段（时间戳/路径/地址），保留语义稳定部分。

    误判防护（spec §4.3）：同一问题但错误行含时间戳/地址微变时，
    不得被误判为指纹演化而无限清零 patience。
    """
    t = (text or "").strip()
    t = _TS_RE.sub("<TS>", t)
    t = _HOME_RE.sub("~", t)
    t = _HEX_RE.sub("<HEX>", t)
    return t[:200]


def compute_fingerprint(stage, verify_exit, first_error_line):
    """失败指纹三元组哈希（失败阶段|退出码|归一化首错误行），返回 12 位 hex。"""
    key = f"{stage or '-'}|{verify_exit}|{normalize_error_line(first_error_line)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


class SessionError(ValueError):
    """会话加载/保存失败。"""


def sessions_root():
    """session 根目录：<项目根>/harness/log/loop-engineering（CDP_PROJECT_ROOT 可覆盖）。"""
    return project_root() / "harness" / "log" / "loop-engineering"


def _now():
    return datetime.now().isoformat(timespec="seconds")


def create_session(goal, batch_file=None, target=None, case=None,
                   max_patience=3, max_total=10):
    """创建会话（模式 A: --batch-file；模式 B: --target + --case）。"""
    mode = "A" if batch_file else "B"
    return {
        "id": uuid.uuid4().hex[:12],
        "mode": mode,
        "goal": goal,
        "batch_file": batch_file or "",
        "target": target or "",
        "case": case or "",
        "created_at": _now(),
        "updated_at": _now(),
        "patience": 0,
        "max_patience": max_patience,
        "total_attempts": 0,
        "max_total": max_total,
        "exit_attribution": None,
        "runs": [],
    }


def save_session(session, path=None):
    """原子写 session.json（tmp + replace），返回路径。"""
    if path is None:
        path = sessions_root() / f"session-{session['id']}" / "session.json"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    session["updated_at"] = _now()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(session, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)
    return path


def _validate_session(data):
    """校验会话 dict 完整性与字段类型；不合法返回 (false, 原因)。"""
    if not isinstance(data, dict):
        return False, "顶层非对象"
    # 后续 apply_done/status 直接索引以下字段（patience/total_attempts/max_patience）
    # 并对 runs 做 append，缺失或类型错误会抛裸 KeyError/TypeError 破坏退出码契约
    checks = {
        "id": (str, "非字符串"),
        "runs": (list, "非列表"),
        "patience": (int, "非整数"),
        "total_attempts": (int, "非整数"),
        "max_patience": (int, "非整数"),
        "max_total": (int, "非整数"),
        "goal": (str, "非字符串"),
        "mode": (str, "非字符串"),
    }
    for field, (typ, why) in checks.items():
        if field not in data:
            return False, f"缺字段 {field}"
        if not isinstance(data[field], typ):
            return False, f"字段 {field} {why}"
    if "exit_attribution" not in data:
        return False, "缺字段 exit_attribution"
    if not isinstance(data["exit_attribution"], (str, type(None))):
        return False, "字段 exit_attribution 非字符串或 null"
    if not data["id"]:
        return False, "字段 id 为空"
    return True, None


def load_session(path):
    """加载会话；缺失/非法抛 SessionError（CLI 层转退出码 3）。"""
    p = Path(path)
    if not p.is_file():
        raise SessionError(f"会话文件不存在: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        ok, reason = _validate_session(data)
        if not ok:
            raise ValueError(reason)
        return data
    except (json.JSONDecodeError, ValueError, OSError, UnicodeDecodeError) as exc:
        raise SessionError(f"会话文件非法: {p}: {exc}") from exc


def find_active_session(goal, batch_file=None, target=None):
    """幂等 start：同 goal+target 且未终结的会话复用。返回 (session, reused)。"""
    for d in sorted(sessions_root().glob("session-*")):
        sj = d / "session.json"
        try:
            s = load_session(sj)
        except SessionError:
            continue  # 损坏目录跳过（可能正被写入），保守不复用
        if (s.get("exit_attribution") is None and s.get("goal") == goal
                and (s.get("batch_file") or None) == (batch_file or None)
                and (s.get("target") or None) == (target or None)):
            return s, True
    return create_session(goal=goal, batch_file=batch_file,
                          target=target), False


_TERMINAL_ATTRS = ("pass", "env_fail", "framework_error",
                   "task_unsolvable", "cost_cap_exceeded")


def extract_first_fail_line(acceptance):
    """从收据 acceptance 字段提取首个 fail 项的 detail 首行（指纹源）。

    三态行为（spec §4.3 首错误行）：
    - 空（空串/空白）-> 返回空串（指纹退化为 阶段|退出码，仍可用）
    - JSON -> 返回首个 status=fail 项的 detail 首行（截断 200 字符），无 fail 项返回空串
    - 非 JSON 且非空 -> 返回原文首行（截断 200 字符），保留首行为指纹源
    """
    if not acceptance:
        return ""
    try:
        data = json.loads(acceptance)
    except (ValueError, json.JSONDecodeError):
        return acceptance.splitlines()[0][:200] if acceptance.strip() else ""
    items = data.get("items", []) if isinstance(data, dict) else []
    for it in items:
        if isinstance(it, dict) and it.get("status") == "fail":
            detail = str(it.get("detail", ""))
            return detail.splitlines()[0][:200] if detail else ""
    return ""


def _acceptance_passed(acceptance):
    """收据 result=pass 时验收一致性校验：acceptance 须为合法 JSON 且 overall
    为 pass 且无 fail 项（与 ws_report 拒写同语义，堵手填假绿推进会话）。

    兼容 dict（overall+items）与数组（历史格式）两种结构。
    返回 (ok, reason)。
    """
    if not (acceptance or "").strip():
        return False, "acceptance 为空"
    try:
        data = json.loads(acceptance)
    except (ValueError, json.JSONDecodeError) as e:
        return False, f"acceptance 非合法 JSON（{e}）"
    if isinstance(data, dict):
        if data.get("overall") != "pass":
            return False, f"overall 非 pass（实际 {data.get('overall')!r}）"
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        return False, "acceptance 非 JSON 对象或数组"
    for it in items:
        if isinstance(it, dict) and it.get("status") == "fail":
            return False, "acceptance 含 fail 项"
    return True, ""


def apply_done(session, receipt_path, stage=None, error_line=None,
               attribution=None):
    """记账一轮：读收据快照 -> 指纹比对 -> 双层计数 -> 归因/退出判定。

    返回 (session, guidance)。attempt 推进唯一锚（spec §4.2/§6）。
    attribution: 显式覆盖（仅 env_fail / framework_error 合法），
    缺省按收据 result 推导（pass -> pass / fail -> task_fail）。
    """
    if session.get("exit_attribution"):
        raise RuntimeError(
            f"会话已终结（{session['exit_attribution']}），拒绝重复记账")
    try:
        r, _receipt_errs = read_receipt(receipt_path)
    except (OSError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"收据读取失败 {receipt_path}: {exc}") from exc

    if r.result == "pass":
        # 验收证据门禁：收据 result=pass 时 acceptance overall 须也为 pass
        # 且无 fail 项，否则拒记账（防手填假绿收据推进会话/终态 pass）
        ok, why = _acceptance_passed(r.acceptance)
        if not ok:
            raise RuntimeError(f"收据 result=pass 但验收未通过（{why}），拒绝记账")

    prev_fail = next((run for run in reversed(session["runs"])
                      if run["result"] == "fail"), None)
    first_err = error_line or extract_first_fail_line(r.acceptance)
    # verify_exit 是 pass/fail 代理（0/1），非真实子进程退出码（spec §4.1/§4.3）
    verify_exit = 0 if r.result == "pass" else 1
    fp = compute_fingerprint(stage, verify_exit, first_err)
    frozen = bool(prev_fail and prev_fail["fingerprint"] == fp)

    # 每轮归因：显式覆盖优先（env_fail/framework_error），
    # 否则 pass -> pass / fail -> task_fail（机械可判，AI 仅边界复核）
    if attribution and attribution not in ("env_fail", "framework_error"):
        raise RuntimeError(f"attribution 仅允许 env_fail/framework_error: {attribution!r}")
    run_attr = attribution or ("pass" if r.result == "pass" else "task_fail")

    session["total_attempts"] += 1
    if run_attr == "task_fail":
        # 同一问题（指纹冻结）连续失败才累计；新问题/新阶段（演化）清零
        session["patience"] = session["patience"] + 1 if frozen else 0

    run = {
        "attempt": session["total_attempts"],
        "ran_at": _now(),
        "receipt_path": str(receipt_path),
        "result": r.result,
        "stage": stage or "",
        "verify_exit": verify_exit,
        "fingerprint": fp,
        "fingerprint_frozen": frozen,
        "attribution": run_attr,
        "fix_action": "",   # AI 每轮修复动作摘要（AI 编辑 session.json 填写）
        "log": "",
        # 收据头快照冗余：收据老化（50 份配额）淘汰后 session 仍自洽（spec §4.1）
        "snapshot": {
            "build": r.build,
            "push_board": r.push_board,
            "acceptance_first_line": (r.acceptance or "").splitlines()[0][:120]
            if r.acceptance else "",
            "summary": (r.summary or "")[:120],
        },
    }
    session["runs"].append(run)

    # 退出判定（优先级：pass > env/framework > patience > total 护栏）
    if run_attr == "pass":
        session["exit_attribution"] = "pass"
    elif run_attr in _TERMINAL_ATTRS:  # env_fail/framework_error 即刻终结
        session["exit_attribution"] = run_attr
    elif session["patience"] >= session["max_patience"]:
        session["exit_attribution"] = "task_unsolvable"
    elif session["total_attempts"] >= session["max_total"]:
        session["exit_attribution"] = "cost_cap_exceeded"

    guidance = _next_guidance(session)
    return session, guidance


def _next_guidance(session):
    """输出下一步指令（status/run/done 同款复用）。"""
    if session["exit_attribution"]:
        return (f"会话终结: {session['exit_attribution']}（{session['total_attempts']} 轮）。"
                "下一步: diagnose 生成诊断报告; task_unsolvable/cost_cap_exceeded 时"
                "末轮收据须并入诊断（ws_report --body <诊断文件> 重写终态收据）后"
                "交 apply 侧推送（模式 A）或直接汇报（模式 B）")
    return (f"继续第 {session['total_attempts'] + 1} 轮: AI 分析失败现场 -> 修复编辑 code/"
            "-> 重跑 verify 工作流 -> 收据落盘后 done。"
            f"patience={session['patience']}/{session['max_patience']} "
            f"total={session['total_attempts']}/{session['max_total']}")


def prune_sessions():
    """写时老化（start 时调用）：超配额删最旧已终结会话目录，活跃会话保护。

    安全前提（spec §4.7）：诊断已并入末轮收据随批入库，session 老化不丢跨批
    证据；损坏目录保守跳过（可能正被原子写的 tmp 阶段覆盖）。
    返回被删目录路径列表。
    """
    root = sessions_root()
    if _SESSION_KEEP <= 0:
        return []
    # 排序键 (mtime, created_at, name)：mtime 升序最旧优先；粗粒度文件系统
    # （如 /mnt/d drvfs）mtime 秒级并列时退化到 readdir 顺序，非确定性，
    # 用 created_at（session.json）再 id hex 兜底保证确定（spec §4.7 硬化）
    def _sort_key(p):
        try:
            created = load_session(p / "session.json").get("created_at", "")
        except SessionError:
            created = ""
        return (p.stat().st_mtime, created, p.name)
    dirs = sorted((d for d in root.glob("session-*") if d.is_dir()),
                  key=_sort_key)
    total = len(dirs)
    if total <= _SESSION_KEEP:
        return []
    finished = []
    for d in dirs:
        try:
            s = load_session(d / "session.json")
        except SessionError:
            continue  # 损坏目录保守跳过（活跃判定不可靠时不冒险删除）
        if s.get("exit_attribution"):
            finished.append(d)
    removed = []
    for d in finished:  # 排序键升序（mtime -> created_at -> id），最旧优先
        if total <= _SESSION_KEEP:
            break
        shutil.rmtree(d)
        removed.append(str(d))
        total -= 1
    if total > _SESSION_KEEP:
        print(f"WARN: 会话数 {total} 超配额 {_SESSION_KEEP}，剩余均活跃/损坏不删除",
              file=sys.stderr)
    return removed


def _cell(text):
    """markdown 表格单元格转义（防 AI 填写的 fix_action 等含 | 破坏列）。"""
    return str(text or "").replace("|", "\\|").replace("\n", " ").strip()


def build_diagnosis(session):
    """诊断报告 markdown（spec §4.6 七要素的机械骨架；语义段由 AI 补写）。

    首行结论五分类（task_unsolvable / cost_cap_exceeded / env_fail /
    framework_error / pass）；该文本由 AI 经 ws_report --body 并入末轮收据。
    """
    attr = session.get("exit_attribution") or "未终结"
    lines = [f"# 诊断报告 session-{session['id']}", "",
             f"归因: {attr}",
             f"目标: {session.get('goal', '')}（模式 {session.get('mode', '')}）",
             f"轮次: {session['total_attempts']}/{session['max_total']}"
             f" patience {session['patience']}/{session['max_patience']}", ""]
    lines += ["## 各轮明细", "",
              "| attempt | 阶段 | result | 指纹(冻结) | 归因 | 修复动作 | build | board | 收据 |",
              "|---|---|---|---|---|---|---|---|---|"]
    for run in session["runs"]:
        snap = run.get("snapshot", {})
        fp = f"{run['fingerprint']}{'*' if run['fingerprint_frozen'] else ''}"
        lines.append(f"| {run['attempt']} | {_cell(run.get('stage', ''))} | "
                     f"{_cell(run['result'])} | {fp} | {_cell(run['attribution'])} | "
                     f"{_cell(run.get('fix_action', ''))} | "
                     f"{_cell(snap.get('build', ''))} | "
                     f"{_cell(snap.get('push_board', ''))} | "
                     f"{_cell(run['receipt_path'])} |")
    fp_track = " -> ".join(f"{r['fingerprint']}{'(冻结)' if r['fingerprint_frozen'] else ''}"
                           for r in session["runs"]) or "（无）"
    lines += ["", "## 指纹演化轨迹", "", fp_track, "",
              "## 已证伪修复方向", "",
              "（AI 补写：各轮 fix_action 中未生效的方向，升级 emit 时随批携带，"
              "强模型不重复撞死胡同）", "",
              "## 建议新增调整 case", "",
              "（AI 补写：失败暴露的用例资产缺口，如新增 verify-cases.yaml 判据）", "",
              "## 循环终止建议", "",
              _next_guidance(session), ""]
    return "\n".join(lines)


def _resolve_acceptance_for_run(session):
    """解析本轮验收源（run 指引用）：模式 A 批次文本 / 模式 B verify-cases 标签。"""
    if session.get("batch_file"):
        from cdp_parse import parse_batch
        try:
            text = Path(session["batch_file"]).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RuntimeError(f"批次文件不可读或非 UTF-8: {exc}") from exc
        b = parse_batch(text)
        if not b.acceptance or b.acceptance == "无":
            raise RuntimeError("批次验收为空或「无」（-sv 批次须有验收）")
        return "--batch-file", session["batch_file"], b.acceptance, b.base
    if session.get("case"):
        import yaml
        cfg = project_root() / "harness" / "config" / "verify-cases.yaml"
        try:
            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise RuntimeError(f"verify-cases.yaml 读取失败: {exc}") from exc
        cases = data.get("cases") or {}
        # 与 ws_acceptance.py resolve_acceptance 对齐：--case 支持逗号分隔
        # 多用例，逐个查表拼接，任一缺失即拒（不部分拼接）
        labels = [c.strip() for c in session["case"].split(",") if c.strip()]
        missing = [c for c in labels if c not in cases]
        if missing:
            raise RuntimeError(
                f"用例标签 {', '.join(missing)} 不存在于 verify-cases.yaml"
                f"（可选: {', '.join(sorted(cases)) or '无'}）")
        def _case_text(v):
            # 与 ws_acceptance._case_text 同款：cases 值 dict 形态
            # （生命周期资产）取 acceptance 键，str 旧形态原样
            if isinstance(v, dict):
                return (v.get("acceptance") or "").strip()
            return v

        return ("--case", session["case"],
                " ".join(_case_text(cases[c]) for c in labels), "")
    raise RuntimeError("会话缺验收源（模式 A 须 --batch-file；模式 B 须 --case）")


def run_guidance(session):
    """输出本轮 verify 执行指引（AI 按序执行，done 记账收尾）。"""
    if session.get("exit_attribution"):
        raise RuntimeError(
            f"会话已终结（{session['exit_attribution']}），不得再生成新轮指引")
    flag, val, acc, base = _resolve_acceptance_for_run(session)
    vdir = "harness/skills/workspace-verify"
    # 方向 4：先产自描述产物再传给报告——单测/验收/推送产物落在本会话日志目录，
    # ws_report PASS 路径（--acceptance-file/--unit-test-file/--push-file）
    # 按产物核验
    logdir = sessions_root() / f"session-{session['id']}"
    ut_file = logdir / "unit-tests.json"
    acc_file = logdir / "acceptance.json"
    push_file = logdir / "push.json"
    return "\n".join([
        f"[第 {session['total_attempts'] + 1} 轮] 按 workspace-verify SKILL 工作流执行:",
        f"  1. code->workspace 同步 + 影响面判定 + 编译 + adb 推送（SKILL 步骤 1-4，"
        f"ws_push.py --result-file {push_file}）",
        f"  2. python3 {vdir}/ws_upload_tests.py --result-file {ut_file}"
        "（上板真跑 C++ 单测：lcview/lciod unit_test+hal_test 先推后跑，"
        "有失败即本轮失败）",
        f"  3. python3 {vdir}/ws_acceptance.py run {flag} {val}"
        f" --result-file {acc_file}"
        " [--ensure-boot 无 boot 标签时自动追加] [--wait-ready --log-since ... 有 reboot 时]",
        f"  4. python3 {vdir}/ws_report.py --result <pass|fail> --build ... --board ..."
        f" --acceptance-file {acc_file} --unit-test-file {ut_file}"
        f" --push-file {push_file}（pass 必需按产物核验；"
        "fail 可 --acceptance 直传现场）"
        f" [--batch-file {session.get('batch_file') or '<批次>'}]"
        f" --target {session.get('target') or base or '<12hex>'} --body <正文文件>"
        + (f" --case {session.get('case')}" if session.get("case") else ""),
        f"  5. python3 {ws_session_cli_path()} done --session <session.json>"
        " --receipt <步骤 4 输出的收据路径> --stage <sync|build|unit_test|push|acceptance>",
        f"验收文本: {acc}",
        "失败时: 读收据失败现场分析 -> 修复编辑 code/ -> 复跑本轮（rescue/补参不耗轮次）",
    ])


def ws_session_cli_path():
    """返回 CLI 相对项目根路径；CDP_PROJECT_ROOT 覆盖（测试/异地）时回退绝对路径。"""
    p = Path(__file__).resolve()
    root = project_root().resolve()
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


def status_text(session):
    """一站式状态（30 秒定位问题层）：计数器 / 各轮归因指纹 / 下一步 / 日志路径。"""
    attr = session.get("exit_attribution") or "运行中"
    lines = [f"session: {session['id']}（模式 {session['mode']}）",
             f"goal: {session['goal']}",
             f"状态: {attr}",
             f"计数: patience {session['patience']}/{session['max_patience']}"
             f"  total {session['total_attempts']}/{session['max_total']}",
             "各轮:"]
    for run in session["runs"]:
        fp = f"{run['fingerprint']}{'(冻结)' if run['fingerprint_frozen'] else ''}"
        lines.append(f"  #{run['attempt']} [{run['attribution']}] {run['result']}"
                     f"@{run.get('stage', '')} fp={fp} 收据={run['receipt_path']}"
                     f" 修复={run.get('fix_action', '') or '-'}")
    sid = session["id"]
    lines.append(f"日志目录: harness/log/loop-engineering/session-{sid}/"
                 f"（attempt-N.log / diagnosis.md）")
    if session.get("exit_attribution"):
        # 终结指引自带「下一步:」前缀，不再叠加（防双重前缀）
        lines.append(_next_guidance(session))
    else:
        lines.append("下一步: " + _next_guidance(session).replace("继续第", "下一轮为第"))
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="loop-engineering 会话状态机")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="创建/复用会话")
    p_start.add_argument("--goal", required=True)
    p_start.add_argument("--batch-file")
    p_start.add_argument("--target")
    p_start.add_argument("--case")
    p_start.add_argument("--max-patience", type=int, default=3)
    p_start.add_argument("--max-total", type=int, default=10)

    for name, help_text in (("run", "输出本轮 verify 执行指引"),
                            ("status", "一站式状态"),
                            ("diagnose", "生成诊断报告")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--session", required=True)

    p_done = sub.add_parser("done", help="记账一轮（收据落盘后调用）")
    p_done.add_argument("--session", required=True)
    p_done.add_argument("--receipt", required=True)
    p_done.add_argument("--stage", choices=["sync", "build", "unit_test", "push",
                                            "acceptance"])
    p_done.add_argument("--error-line", default=None,
                        help="首错误行（缺省从收据 acceptance 提取首个 fail detail）")
    p_done.add_argument("--attribution", choices=["env_fail", "framework_error"])

    args = ap.parse_args(argv)
    try:
        if args.cmd == "start":
            if not args.batch_file and not (args.target and args.case):
                print("error: 须传 --batch-file（模式 A）或 --target+--case（模式 B）",
                      file=sys.stderr)
                return 2
            if args.batch_file and (args.target or args.case):
                print("error: 模式 A/B 互斥", file=sys.stderr)
                return 2
            s, reused = find_active_session(
                args.goal, batch_file=args.batch_file, target=args.target)
            if not reused:
                # 仅新建会话设定 case/上限；复用会话不得覆盖既有验收标准，
                # 防中途切换 case 混两种验收标准于同一 run 历史（spec §7.5）
                s["case"] = args.case or s.get("case", "")
                s["max_patience"] = args.max_patience
                s["max_total"] = args.max_total
            elif args.case and s.get("case") and args.case != s.get("case"):
                print(f"WARN: 复用活跃会话 {s['id']} 的 case={s.get('case')!r}"
                      f" 与本次 --case {args.case!r} 不同，沿用原 case",
                      file=sys.stderr)
            path = save_session(s)
            prune_sessions()  # 写时老化（仅删已终结，活跃保护）
            print(f"session: {path}")
            print(f"{'复用活跃会话' if reused else '新会话'}: {s['id']}"
                  f" goal={s['goal']} 模式={s['mode']}")
            print(f"下一步: run --session {path}")
            return 0
        try:
            s = load_session(args.session)
        except SessionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 3
        if args.cmd == "run":
            if s.get("exit_attribution"):
                print(f"error: 会话已终结（{s['exit_attribution']}），"
                      "不得再跑新轮", file=sys.stderr)
                return 1
            try:
                print(run_guidance(s))
            except RuntimeError as exc:
                # 真实路径错误（坏 case 标签/批次不可读/验收缺源等）须转退出码 1，
                # 不得让裸 traceback 逃逸破坏 framework_error 检测（spec §9）
                print(f"error: {exc}", file=sys.stderr)
                return 1
            return 0
        if args.cmd == "status":
            print(status_text(s))
            return 0
        if args.cmd == "diagnose":
            md = build_diagnosis(s)
            # 落位会话目录（确定性），而非 --session 裸文件名时的 CWD
            out = sessions_root() / f"session-{s['id']}" / "diagnosis.md"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
            print(f"diagnosis: {out}")
            print(md)
            return 0
        # done
        if s.get("exit_attribution"):
            print(f"error: 会话已终结（{s['exit_attribution']}），拒绝重复记账",
                  file=sys.stderr)
            return 1
        try:
            s, guidance = apply_done(s, args.receipt, stage=args.stage,
                                     error_line=args.error_line,
                                     attribution=args.attribution)
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        save_session(s, args.session)
        print(guidance)
        print(f"session: {args.session}")
        return 0
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
