"""baseline-status.yaml candidate/promoted 登记辅助。

新流程登记从 candidate 起步（archive 仅旧流程历史）。
sync_manifest 字段复用为 data/verify-results 收据路径（spec §7）。
save() 手工保留 yaml 头部注释块（PyYAML 往返不保留注释）。
"""
import argparse
import datetime
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

# 本文件位于 harness/skills/publish-main-base/，parents[2] = harness
CONFIG = Path(__file__).resolve().parents[2] / "config" / "baseline-status.yaml"

# 发布全量组门禁基准：verify-cases.yaml cases 段全部 case（发布前须全量验收）
VERIFY_CASES_PATH = (Path(__file__).resolve().parents[2] / "config"
                     / "verify-cases.yaml")

# 仿 ws_report.py：引入 cross-device 共享收据模块，candidate 实读真实 verify 收据
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_receipt import read_receipt  # noqa: E402
from cdp_issue import (closed_issue_details, closed_issue_paths,
                       issue_files, read_index, read_issue, set_archived_in,
                       validate_issue)  # noqa: E402
from cdp_paths import data_baselines_dir, project_root  # noqa: E402


def load():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def verify_case_ids(cases_path=None):
    """verify-cases.yaml cases 段全部 case id（发布全量组门禁基准）。"""
    path = Path(cases_path) if cases_path else VERIFY_CASES_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"verify-cases.yaml 读取失败: {e}") from e
    return list((data.get("cases") or {}).keys())


def cases_coverage(receipt_cases, cases_path=None):
    """发布全量组覆盖核对：返回 (result, missing, run_count)。

    result: 'full'（覆盖 verify-cases.yaml 全部 case）/ 'partial'（有缺项）/
    'missing'（收据无 cases = 无上板验收证据）。两级策略由此从注释契约升级为
    机器核对：少跑不能背书基线。
    """
    all_ids = verify_case_ids(cases_path)
    got = {c.strip() for c in (receipt_cases or "").split(",") if c.strip()}
    missing = [c for c in all_ids if c not in got]
    if not got:
        result = "missing"
    elif missing:
        result = "partial"
    else:
        result = "full"
    return result, missing, len(got)


def save(data):
    """整文件重写但保留头部 '#' 注释行（语义说明不丢失）。

    注释收集遇首个非 '#' 非空行即停，避免把 yaml 条目内的注释行反复上提。
    """
    text = CONFIG.read_text(encoding="utf-8")
    header = []
    for ln in text.splitlines(keepends=True):
        if ln.startswith("#"):
            header.append(ln)
        elif ln.strip() == "":
            header.append(ln)  # 头部注释块间的空行一并保留
        else:
            break
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    CONFIG.write_text("".join(header) + body, encoding="utf-8")


def _package_evidence_path(batch_id):
    """按 batch_id 探测 ws_package 打包证据（harness/log/workspace-verify/）。"""
    if not batch_id:
        return None
    p = (project_root() / "harness" / "log" / "workspace-verify"
         / f"package-{batch_id}.json")
    return p if p.is_file() else None


def _load_package_evidence(path):
    """读打包证据 JSON dict；缺失/不可读/非对象均返 None（如实不声称）。"""
    if not path:
        return None
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _receipt_package(r):
    """解析收据 package 字段内嵌的 ws_package 打包证据 dict。

    收据 package 字段由 ws_report 内嵌 ws_package 自描述证据单行 JSON 串
    （随收据入库可追溯，本批意图 1）；空/非法/非对象返 None（如实不声称）。
    """
    text = (getattr(r, "package", "") or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _package_result_from_evidence(pkg_evidence, evidence_scope):
    """由打包证据机械推导 package_result（方向 2 同源口径）：
    证据 script_rc==0 记 PASS；evidence_scope=no-code-change 记 SKIP（无代码
    改动打包豁免）；其余（无证据/证据 rc 非 0/不可读）留 UNKNOWN 不声称。
    """
    pkg_rc = pkg_evidence.get("script_rc") if pkg_evidence else None
    if pkg_rc == 0:
        return "PASS"
    if (evidence_scope or "").strip() == "no-code-change":
        return "SKIP"
    return "UNKNOWN"


def _derive_package_result(receipt, evidence_scope):
    """promote 一致性校验：由收据内嵌打包证据推导期望 package_result。

    与 add-candidate 同源口径（_package_result_from_evidence），仅证据源固定
    为收据 package 字段（入库证据）——基线记 PASS 而收据无内嵌打包证据
    （gitignore 域不可追溯）即推导 UNKNOWN，与基线不一致即阻断（方向 3）。
    """
    return _package_result_from_evidence(_receipt_package(receipt),
                                         evidence_scope)


def _code_changes_since_main():
    """dev 相对 origin/main 的 code/ 改动提交列表（no-code-change 机器核对用）。

    no-code-change 豁免声称"无代码改动"，须机器核对而非采信参数：shell 层
    （publish_main_base.sh）已由 git log 推导，Python 层做对称校验堵直调
    baseline_register promote 伪造豁免。复用本文件既有的 subprocess git
    能力（verify-tree 已用 git diff/rev-parse），不新引依赖。
    git log 失败（缺 origin/main 引用等）返回 None = 无法核对，调用方须
    fail-closed 拒绝豁免（宁可误拒不可放行）。
    """
    r = subprocess.run(
        ["git", "log", "--format=%h %s", "origin/main..HEAD", "--", "code/"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def next_id(data, today):
    existing = [b.get("baseline_id") for b in data.get("baselines", [])]
    n = 1
    while f"BL-{today}-{n:02d}" in existing:
        n += 1
    return f"BL-{today}-{n:02d}"


def carried_issue_ids(task, issues_dir=None):
    """取 status 属 open 或 scheduled 且 task 匹配的条目 id（带病项自动携带）。

    prepare 升基线时把遗留问题记账进 candidate evidence（known_issues_carried），
    只记录不阻断——硬阻断会死锁（遗留问题恰好是升基线要延续跟踪的对象）。
    task 为空（未显式指定）返回空列表，不携带任何条目。
    """
    if not task:
        return []
    return [e["issue_id"] for e in read_index(issues_dir)
            if e["status"] in ("open", "scheduled") and e["task"] == task]


def _real_known_issues_dir():
    """承重门禁数据源：固定仓库真实根（模块位置解析，不随 CDP_PROJECT_ROOT 改道）。

    CDP_PROJECT_ROOT 是收据/打点等运行产物的隔离机制（CI 自检指向 runner
    临时目录），known-issues 是发布门禁证据——若随 env 改道到空目录会被
    环境变量静默关掉（empty-registry 假绿），故门禁一律读真实根。
    测试经 --known-issues-dir 显式指回临时根，与 env 隔离机制并存。"""
    return Path(__file__).resolve().parents[3] / "data" / "known-issues"


def _open_flake_issues(issues_dir=None):
    """未闭环 flake 类 known-issues：kind=flake 且 status 非 fixed/wontfix。

    方向 3：KIR-002 抖动登记（selfcheck 机械放行）的 flake 是"单跑绿非阻塞"
    记录，但存在未闭环 flake 意味着抖动尚未根因定位/闭环——promote 基线
    晋升不得携带未闭环抖动，故 promote 硬拒。闭环 = 标 fixed/wontfix 并填
    resolved_in（KIR-006）。"""
    d = Path(issues_dir) if issues_dir else _real_known_issues_dir()
    out = []
    for p in issue_files(d):
        i = read_issue(p)
        if i.kind == "flake" and i.status not in ("fixed", "wontfix"):
            out.append(f"{p.name}: {i.title}")
    return out


# ── P1-B：promote 审批独立校验（修复 KI-20260907-001）────────────────
# 业界对齐 SLSA 独立审批思想：审批不得自证。两道校验——
#   1) 身份不等式：--approved-by 审批人不得等于执行人 git 身份（收据
#      operator 同源采集）；
#   2) 审批凭据外部化：LC_PROMOTE_APPROVAL_TOKEN 环境变量须与
#      harness/config/promote-approval.env 预设值一致（评审人独立持有，
#      文件 gitignore 不入库，杜绝审批可自证）。


def _collect_operator() -> str:
    """执行人 git 身份（与 cdp_receipt._collect_operator 同源口径）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]
                               / "skills" / "cross-device" / "lib" / "python"))
        import cdp_receipt
        return cdp_receipt._collect_operator()
    except Exception:
        return "unknown"


def _norm_identity(s: str) -> str:
    """身份归一：'Name <email>' → 'name'; 小写去空白（比较用）。"""
    s = (s or "").strip()
    if "<" in s:
        s = s.split("<", 1)[0]
    return s.lower().strip()


def _read_approval_token(token_file: str | None = None) -> str:
    """读 promote-approval.env 预设 token（缺省
    harness/config/promote-approval.env）；不存在返回 ''。"""
    path = Path(token_file) if token_file else (
        Path(__file__).resolve().parents[2] / "config"
        / "promote-approval.env")
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln.startswith("LC_PROMOTE_APPROVAL_TOKEN="):
                return ln.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def _check_approval_independence(approved_by: str,
                                 operator: str,
                                 token: str,
                                 token_file: str | None = None) -> tuple[bool, str]:
    """审批独立校验：返回 (ok, err)。

    token_file（方向 1）：--approval-token-file 透传（测试/异地覆盖），
    缺省读 harness/config/promote-approval.env。
    """
    if not (approved_by or "").strip():
        return False, "promote 必须传 --approved-by（审批凭据外部化）"
    if _norm_identity(approved_by) == _norm_identity(operator) \
            and operator.lower() != "unknown":
        return False, (f"审批人 {approved_by!r} 与执行人 {operator!r} 相同，"
                       "审批缺乏独立隔离（KI-20260907-001），拒绝 promote")
    provided = (token or "").strip()
    if not provided:
        return False, "缺 LC_PROMOTE_APPROVAL_TOKEN（审批凭据外部化失败）"
    # 占位符/尖括号一律拒（真实随机 token 不含 < >）
    if "<" in provided or ">" in provided:
        return False, "LC_PROMOTE_APPROVAL_TOKEN 为占位符，拒绝 promote"
    expected = _read_approval_token(token_file)
    if not expected:
        # 缺 token 判红（方向 1）：预设文件缺失/未预设即凭据外部化失败，
        # 不得静默放行（此前默认路径多拼一层恒读空、空 expected 短路跳过
        # 比对 fail-open）
        return False, ("promote-approval.env 缺失或未预设 "
                       "LC_PROMOTE_APPROVAL_TOKEN（审批凭据外部化失败）")
    if provided != expected:
        return False, "LC_PROMOTE_APPROVAL_TOKEN 与 promote-approval.env 预设值不一致"
    return True, ""


def check_issues_gate(task=None, issues_dir=None):
    """known-issues 门禁主体（check-issues action 与 add-candidate 复用，方向 4）。

    先判畸形登记（validate_issue 有红即拒：文件名/头字段/枚举/index 一致性
    全局把关，防 index 按空格切分错位等畸形记录污染门禁判定）→ task 推断/
    白名单（缺省从 status 非 fixed 条目的 task 集合推断，显式传值须在活跃
    集合内防拼错）→ 判目标任务未解决阻塞（origin=introduced 或 blocking 且
    status!=fixed 即拒）。
    数据源 issues_dir 缺省取仓库真实根（_real_known_issues_dir，不随
    CDP_PROJECT_ROOT 改道——承重门禁不得被环境变量关掉）。
    返回 rc：0 通过 / 1 畸形或未解决阻塞 / 3 task 不在活跃集合。
    """
    d = Path(issues_dir) if issues_dir else _real_known_issues_dir()
    # 单次遍历缓存 (path, Issue) 复用（pub-08）：task 推断与阻塞判定不再逐文件
    # 重复 read_issue（drvfs IO 放大收敛为一次读取）；read 失败记 None——不可读
    # 文件仍由下方 validate_issue 判红拒绝，后续遍历跳过 None（行为与原实现一致）
    issues = []
    for p in issue_files(d):
        try:
            i = read_issue(p)
        except OSError:
            i = None
        issues.append((p, i))
    for p, _i in issues:
        errs = validate_issue(p)
        if errs:
            for e in errs:
                print(f"{p.name}: {e}", file=sys.stderr)
            print("error: known-issues 畸形登记，拒绝（先修复登记再发布基线）",
                  file=sys.stderr)
            return 1
    # task 推断：缺省从 status 非 fixed 条目的 task 集合推断（自动，无需人工申报）
    active_tasks = {i.task for _p, i in issues
                    if i is not None and i.status != "fixed" and i.task}
    if task:
        # 白名单：显式传 --task 不在活跃集合内即 exit 3（防拼错静默通过；
        # 空集合时放行——无活跃任务则无冲突对象）
        if active_tasks and task not in active_tasks:
            print(f"error: --task {task!r} 不在活跃任务集合 "
                  f"{sorted(active_tasks)} 内（防拼错静默通过）",
                  file=sys.stderr)
            return 3
    else:
        if len(active_tasks) == 1:
            task = next(iter(active_tasks))
        elif len(active_tasks) > 1:
            print(f"error: 活跃任务集合多值 {sorted(active_tasks)}，"
                  f"须显式传 --task 之一", file=sys.stderr)
            return 1
        else:
            task = "empty-registry"
    # 再判目标任务未解决阻塞：origin=introduced 或 blocking 且 status!=fixed 即拒
    bad = []
    for p, i in issues:
        if i is None or i.task != task:
            continue
        if (i.origin == "introduced" or i.blocking) and i.status != "fixed":
            bad.append(f"{p.name}: origin={i.origin} blocking={i.blocking} "
                       f"status={i.status}")
    if bad:
        print("\n".join(bad), file=sys.stderr)
        print(f"error: task={task} 存在未解决阻塞问题", file=sys.stderr)
        return 1
    print(f"known-issues 门禁通过（task={task} 无未解决阻塞问题）")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="baseline candidate/promoted 登记")
    ap.add_argument("action",
                    choices=["add-candidate", "promote", "revert-candidate",
                             "check-issues", "verify-tree"])
    ap.add_argument("--baseline-id")
    ap.add_argument("--source-commit")
    ap.add_argument("--receipt-path")
    ap.add_argument("--approved-by")
    ap.add_argument("--approval-token-file", default="",
                    help="promote-approval.env 路径（测试/异地覆盖；缺省 "
                         "harness/config/promote-approval.env）")
    ap.add_argument("--task")
    ap.add_argument("--ki-gate", help="known-issues 门禁结论 pass/not-run，写入 evidence")
    ap.add_argument("--evidence-scope", help="证据范围标签（如 lcview-liveness）；"
                        "缺省从收据 cases 推导，人工传值须为其子集（防过度声称）")
    ap.add_argument("--package-evidence",
                    help="ws_package 打包证据 JSON 路径（兜底：收据 package 字段内嵌"
                         "证据优先，缺省按收据 batch_id 探测 "
                         "harness/log/workspace-verify/package-<batch_id>.json）")
    ap.add_argument("--known-issues-carried",
                    help="带病登记 issue_id 列表（逗号分隔，写入 evidence 的 "
                         "known_issues_carried；缺参记空，只记录不阻断）")
    ap.add_argument("--known-issues-dir",
                    help="known-issues 门禁数据源目录（缺省固定仓库真实根 "
                         "data/known-issues，不随 CDP_PROJECT_ROOT 改道；"
                         "测试/异地经此显式指回，与收据 env 隔离并存）")
    args = ap.parse_args(argv)

    # check-issues：known-issues 门禁（publish_main_base.sh 委托；不读写登记 yaml）
    if args.action == "check-issues":
        return check_issues_gate(task=args.task, issues_dir=args.known_issues_dir)

    # verify-tree：树等价断言（publish_main_base.sh squash 后、push main 前委托）。
    # 比较 verified/<id> tag 与 main 的树，排除登记 yaml 与 docs 后必须无差异，
    # 防未验证内容借 meta/doc 提交夹带进 main（不读写登记 yaml）
    if args.action == "verify-tree":
        if not args.baseline_id:
            print("error: verify-tree 必须传 --baseline-id", file=sys.stderr)
            return 1
        tag = f"verified/{args.baseline_id}"

        def _tree(ref):
            r = subprocess.run(["git", "rev-parse", f"{ref}^{{tree}}"],
                               capture_output=True, text=True, encoding="utf-8", errors="replace")
            return r.stdout.strip() if r.returncode == 0 else ""

        tag_tree, main_tree = _tree(tag), _tree("main")
        if not tag_tree or not main_tree:
            print(f"error: 无法解析 {tag} 或 main 的树对象", file=sys.stderr)
            return 1
        r = subprocess.run(["git", "diff", "--name-only", tag_tree, main_tree],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode != 0:
            print(f"error: 树对比失败: {r.stderr.strip()}", file=sys.stderr)
            return 1
        # 排除项：登记 yaml（promote 元提交必然改动）与 docs/（文档同步提交）
        # 与 data/baselines/（promote 生成的证据快照目录，随晋升提交入库）
        # 与 data/known-issues/（保留目录——promote 归档不删文件，批内新登记
        # 问题随晋升提交入库；不排除则登记变更让树等价断言必红回滚）
        excludes = ("harness/config/baseline-status.yaml", "docs/",
                    "data/baselines/", "data/known-issues/")
        diffs = [ln for ln in r.stdout.splitlines()
                 if ln and not any(ln == e or ln.startswith(e) for e in excludes)]
        if diffs:
            print("\n".join(diffs), file=sys.stderr)
            print(f"error: verified/{args.baseline_id} 与 main 树不等价"
                  f"（排除登记 yaml 与 docs 后仍有差异）", file=sys.stderr)
            return 1
        print(f"树等价断言通过：verified/{args.baseline_id} ≡ main"
              f"（排除登记 yaml 与 docs）")
        return 0

    data = load()
    baselines = data.setdefault("baselines", [])
    today = datetime.date.today().strftime("%Y%m%d")

    if args.action == "add-candidate":
        # 方向 4：known-issues 门禁自执（此前只记 --ki-gate 参数不自执；抽出
        # check_issues_gate 复用 check-issues action 同源逻辑，门禁不过即拒登记，
        # 不把门禁结论留给参数声明；数据源固定真实根不随 CDP_PROJECT_ROOT 改道）
        gate_rc = check_issues_gate(task=args.task, issues_dir=args.known_issues_dir)
        if gate_rc != 0:
            return gate_rc
        if not args.receipt_path:
            print("error: add-candidate 必须传 --receipt-path（证据链要求实读 verify 收据）",
                  file=sys.stderr)
            return 1
        # evidence_scope：证据推导优先——缺省取该收据 cases（上板实测范围），
        # 人工传值须为其子集否则拒（防过度声称：不得声称未实测的用例范围）
        evidence_scope = (args.evidence_scope or "").strip()
        try:
            r, receipt_errs = read_receipt(args.receipt_path)
        except (OSError, UnicodeDecodeError) as e:
            print(f"error: 读取收据失败 {args.receipt_path}: {e}", file=sys.stderr)
            return 1
        if receipt_errs:
            print(f"error: 收据解析错误 {args.receipt_path}: "
                  f"{'; '.join(receipt_errs)}", file=sys.stderr)
            return 1
        # 方向 5：device_dirty 拒收——设备态不可信的验证结果不得登记为基线
        # （ws_report 已源头拒落 pass，此处补登记侧防线防绕过）
        if (r.device_dirty or "").strip().lower() in ("true", "1", "yes"):
            print("error: 收据 device_dirty=true（teardown 恢复失败，设备态不可信），"
                  "拒绝登记 candidate", file=sys.stderr)
            return 1
        receipt_cases = {c.strip() for c in (r.cases or "").split(",") if c.strip()}
        # 方向 2（本批意图 2）：evidence 自描述——记录发布全量组覆盖核对结果
        # 与实跑 case 数（含缺失名单），复核不需要重读 verify-cases.yaml
        cov_result, cov_missing, cov_run_count = cases_coverage(r.cases)
        cov_record = {"result": cov_result, "missing": cov_missing,
                      "run_count": cov_run_count}
        if not evidence_scope:
            if not receipt_cases:
                print("error: add-candidate 缺 --evidence-scope 且收据无 cases 字段"
                      "（证据推导无源），须传 --evidence-scope 或先让 ws_report 落 cases",
                      file=sys.stderr)
                return 1
            evidence_scope = ",".join(sorted(receipt_cases))
        elif evidence_scope == "no-code-change":
            # 豁免标记非 case 标签：不参与"收据 cases 子集"声称校验（批次
            # ff33f92060ac 方向 2——no-code-change 批据此可登记为 candidate，
            # package_result 由同源推导记 SKIP）
            pass
        else:
            manual = {c.strip() for c in evidence_scope.split(",") if c.strip()}
            if not manual.issubset(receipt_cases):
                extra = ", ".join(sorted(manual - receipt_cases))
                print(f"error: --evidence-scope {manual} 超出收据实测 cases"
                      f"（{sorted(receipt_cases) or '无'}），过度声称拒绝登记: {extra}",
                      file=sys.stderr)
                return 1
        # build 取收据 build 阶段，board_verify 取 push_board，均大写（不再伪造 PASS）
        # 空值（""/None/纯空白）记 FAIL 不记 SKIP——空值不是合法 skip 证据，证据链从严
        build_result = ((r.build or "").strip() or "FAIL").upper()
        # package（方向 2，批次 ff33f92060ac；本批意图 2 改源）：由 ws_package
        # 打包证据机械推导。证据源优先级：收据 package 字段内嵌证据（随收据
        # 入库可追溯，主源）> --package-evidence 显式路径（兜底）> 按 batch_id
        # 探测 harness/log（gitignore 域，最末兜底）。script_rc==0 记 PASS；
        # evidence_scope=no-code-change 记 SKIP（无代码改动打包豁免）；
        # 其余（无证据/证据 rc 非 0/不可读）留 UNKNOWN 不声称。
        # 不再把 build_result 复制给 package_result，杜绝伪造打包证据
        receipt_pkg = _receipt_package(r)
        if receipt_pkg is not None:
            pkg_evidence = receipt_pkg
            pkg_evidence_path = args.receipt_path  # 证据内嵌收据，载体即收据
        else:
            pkg_evidence_path = ((args.package_evidence or "").strip()
                                 or _package_evidence_path(r.batch_id))
            pkg_evidence = _load_package_evidence(pkg_evidence_path)
        pkg_rc = pkg_evidence.get("script_rc") if pkg_evidence else None
        package_result = _package_result_from_evidence(pkg_evidence,
                                                       evidence_scope)
        # 方向 5：UNKNOWN 且证据 sudo_n=false → 告警指向人工打包路径（BLD-013：
        # opencode 会话 NoNewPrivileges 使 sudo 恒被内核拒绝，会话内无法打包）
        if package_result == "UNKNOWN" and isinstance(pkg_evidence, dict) \
                and pkg_evidence.get("sudo_n") is False:
            print("warn: package_result=UNKNOWN 且打包证据 sudo_n=false（会话内 "
                  "sudo 被 NoNewPrivileges 拒绝，BLD-013）：须在 opencode 会话外"
                  "普通终端人工执行打包后重登记（见 harness/reference/"
                  "build-reference.md）", file=sys.stderr)
        board_verify = ((r.push_board or "").strip() or "FAIL").upper()
        # 方向 4：Python 层登记防线——防绕过 shell 直调登记（publish_main_base.sh
        # prepare 有门禁，直调 add-candidate 须同样从严）
        # 非法枚举：verify_mode/result 白名单
        if r.verify_mode not in ("board", "skip", "none"):
            print(f"error: 收据 verify_mode 非法（{r.verify_mode!r}），拒绝登记",
                  file=sys.stderr)
            return 1
        if r.result not in ("pass", "fail", "skip"):
            print(f"error: 收据 result 非法（{r.result!r}），拒绝登记",
                  file=sys.stderr)
            return 1
        # 登记门禁（AGENTS.md 原文：「登记门禁：收据 result 属 pass 或 skip 且
        # HEAD^ 等于 verified_commit」）：result=fail 收据（含 build/push_board=
        # PASS 但 cases 失败者）不具备基线证据性，显式拒绝——堵绕过链（fail 收据
        # 经 skip 收据成为 LATEST 后由 prepare 取作 evidence 锚点放行登记）
        if r.result == "fail":
            print("error: 收据 result=fail，拒绝登记（登记门禁：收据 result 属"
                  " pass 或 skip，fail 收据不得作为基线证据，见 AGENTS.md）",
                  file=sys.stderr)
            return 1
        # board 模式收据须 result=pass：board 实测批的 skip 不具备上板证据性
        # （skip 收据仅可用于 harness 自检批，不得充当发布验收证据）
        if r.verify_mode == "board" and r.result != "pass":
            print(f"error: 收据 verify_mode=board 但 result={r.result!r} 非 pass，"
                  "拒绝登记（board 实测收据须 result=pass 才具备基线证据性）",
                  file=sys.stderr)
            return 1
        # 缺必需字段：batch_id/verified_commit/build/push_board 必填
        missing = [k for k, v in (("batch_id", r.batch_id),
                                  ("verified_commit", r.verified_commit),
                                  ("build", r.build),
                                  ("push_board", r.push_board))
                   if not (v or "").strip()]
        if missing:
            print(f"error: 收据缺必需字段 {', '.join(missing)}，拒绝登记",
                  file=sys.stderr)
            return 1
        # 拒非 PASS/SKIP：build/board_verify 非 PASS/SKIP 不可登记为基线
        # （AGENTS.md：UNKNOWN 视同 FAIL 须人工复核；空值缺省 FAIL 逻辑保留，
        # 空值不是合法 skip 证据）
        if build_result not in ("PASS", "SKIP") \
                or board_verify not in ("PASS", "SKIP"):
            print(f"error: 收据 build/board_verify 非 PASS/SKIP"
                  f"（build={build_result} board_verify={board_verify}），"
                  "拒绝登记（UNKNOWN 视同 FAIL，须人工复核）", file=sys.stderr)
            return 1
        # ki_gate：known-issues 门禁结论（拒批已在脚本层 exit，缺参视为 not-run）
        ki_gate = (args.ki_gate or "").strip() or "not-run"
        # known_issues_carried：带病项记账（缺参记空；只记录不阻断，硬阻断会死锁）
        known_issues_carried = (args.known_issues_carried or "").strip()
        # 去重复用：同 source_commit 且仍为 candidate 的记录不新增（防重复 prepare 冗余登记；
        # 收据路径不同则对齐最新证据，保持 promote 证据链一致）
        for b in baselines:
            if (b.get("source_commit") == args.source_commit
                    and b.get("status") == "candidate"):
                if b.get("sync_manifest") != args.receipt_path:
                    b["sync_manifest"] = args.receipt_path
                    b["build_result"] = build_result
                    b["package_result"] = package_result
                    b["board_verify"] = board_verify
                    b["evidence_scope"] = evidence_scope
                    b["evidence"] = {
                        "build_result": build_result,
                        "package_result": package_result,
                        "board_verify": board_verify,
                        "sync_manifest": args.receipt_path,
                        "ki_gate": ki_gate,
                        "evidence_scope": evidence_scope,
                        "known_issues_carried": known_issues_carried,
                        "package_evidence": str(pkg_evidence_path or ""),
                        "package_rc": pkg_rc,
                        "cases_coverage": cov_record,
                    }
                    save(data)
                    print(f"candidate 复用并更新收据: {b['baseline_id']}（source_commit={args.source_commit}）")
                else:
                    print(f"candidate 复用: {b['baseline_id']}（source_commit={args.source_commit}）")
                return 0
        # 显式 --baseline-id 查重（pub-06）：同 id 记录已存在（任意状态）即拒，
        # 防显式指定绕过 next_id 产生重复 id 污染登记
        if args.baseline_id and any(b.get("baseline_id") == args.baseline_id
                                    for b in baselines):
            print(f"error: baseline_id {args.baseline_id} 已存在，拒绝重复登记",
                  file=sys.stderr)
            return 1
        bid = args.baseline_id or next_id(data, today)
        baselines.append({
            "baseline_id": bid,
            "status": "candidate",
            "source_branch": "dev",
            "source_commit": args.source_commit,
            "sync_manifest": args.receipt_path,
            "build_result": build_result,
            "package_result": package_result,
            "board_verify": board_verify,
            "evidence_scope": evidence_scope,
            "evidence": {
                "build_result": build_result,
                "package_result": package_result,
                "board_verify": board_verify,
                "sync_manifest": args.receipt_path,
                "ki_gate": ki_gate,
                "evidence_scope": evidence_scope,
                "known_issues_carried": known_issues_carried,
                "package_evidence": str(pkg_evidence_path or ""),
                "package_rc": pkg_rc,
                "cases_coverage": cov_record,
            },
        })
        save(data)
        print(f"candidate: {bid}")
        return 0

    if args.action == "promote":
        for b in baselines:
            if b.get("baseline_id") == args.baseline_id:
                if b.get("status") != "candidate":
                    print(f"error: baseline {args.baseline_id} 状态为 "
                          f"{b.get('status')!r}，仅 candidate 可 promote", file=sys.stderr)
                    return 1
                # 证据快照：把条目 sync_manifest（verify 收据）复制到 data/baselines/
                # <id>-<收据名>.md，随登记 yaml 一并入库；同名快照已存在即拒（防覆盖历史证据）
                receipt = Path(b.get("sync_manifest") or "")
                if not receipt.is_file():
                    print(f"error: 收据文件不存在，无法生成证据快照: {receipt}",
                          file=sys.stderr)
                    return 1
                # 方向 6 + P1-B：审批凭据外部化——promote 审批人不得为执行人、
                # token 须与 promote-approval.env 预设一致（在写快照前校验，
                # 防快照污染），不再回落默认常量（防审批可自证，闭环
                # KI-20260907-001）
                if not args.approved_by:
                    print("error: promote 必须传 --approved-by"
                          "（审批凭据外部化，不再回落默认常量）", file=sys.stderr)
                    return 1
                ok, aerr = _check_approval_independence(
                    args.approved_by, _collect_operator(),
                    os.environ.get("LC_PROMOTE_APPROVAL_TOKEN", ""),
                    args.approval_token_file or None)
                if not ok:
                    print(f"error: {aerr}", file=sys.stderr)
                    return 1
                # 方向 3：存在未闭环 flake 类 KI（kind=flake 且未标终态）即拒
                # ——KIR-002 抖动登记允许放行本轮自检，但晋升不得携带未闭环
                # 抖动（闭环须标 fixed/wontfix 并填 resolved_in）
                open_flakes = _open_flake_issues(args.known_issues_dir)
                if open_flakes:
                    print(f"error: promote 存在 {len(open_flakes)} 个未闭环 flake "
                          f"known-issues（抖动未闭环不得晋升），拒绝：",
                          file=sys.stderr)
                    for o in open_flakes:
                        print(f"  {o}", file=sys.stderr)
                    return 1
                snapshot_name = f"{args.baseline_id}-{receipt.name}"
                if not snapshot_name.endswith(".md"):
                    snapshot_name += ".md"
                snapshot_path = data_baselines_dir() / snapshot_name
                if snapshot_path.exists():
                    print(f"error: 证据快照已存在，拒绝覆盖: {snapshot_path}",
                          file=sys.stderr)
                    return 1
                # promote 允许透传/改写 evidence_scope（如零改动豁免时改写 no-code-change）；
                # 改写为 no-code-change 时 package_result 由 UNKNOWN 同步改 SKIP
                # （无代码改动打包豁免，方向 2 同源推导）
                scope = (args.evidence_scope or "").strip()
                if scope:
                    b["evidence_scope"] = scope
                    if isinstance(b.get("evidence"), dict):
                        b["evidence"]["evidence_scope"] = scope
                    if (scope == "no-code-change"
                            and b.get("package_result") == "UNKNOWN"):
                        b["package_result"] = "SKIP"
                        if isinstance(b.get("evidence"), dict):
                            b["evidence"]["package_result"] = "SKIP"
                # 方向 3（批次 ff33f92060ac）promote 硬门禁：package_result 非 PASS
                # 即阻断，仅 evidence_scope=no-code-change（无代码改动）豁免不受限。
                # 打包生产者 ws_package 已就位，替换旧"UNKNOWN 仅告警"口径——
                # 动过 code 的基线必须携带真实打包证据（rc=0）才可晋升
                pkg_now = (b.get("package_result") or "UNKNOWN").upper()
                scope_now = (b.get("evidence_scope") or "").strip()
                # 方向 1（本批意图 1）：promote no-code-change 机器核对——Python
                # 层对称校验（shell 层已由 git log 推导而 Python 层不原样采信参数，
                # 防直调 promote 一次同时豁免打包硬门禁与全量组门禁）。最终生效
                # scope 为 no-code-change 时，核对 dev 相对 origin/main 的 code/
                # 改动：有改动即拒并列改动提交；无法核对（缺 origin/main 引用）
                # 按 fail-closed 拒绝豁免
                if scope_now == "no-code-change":
                    code_changes = _code_changes_since_main()
                    if code_changes is None:
                        print("error: promote 机器核对：无法核对 code/ 改动"
                              "（缺 origin/main 引用），拒绝 no-code-change 豁免",
                              file=sys.stderr)
                        return 1
                    if code_changes:
                        print(f"error: promote 机器核对：evidence_scope=no-code-change"
                              f" 与 code/ 实际 diff 不符，存在 "
                              f"{len(code_changes)} 个改动提交，拒绝豁免:",
                              file=sys.stderr)
                        for c in code_changes:
                            print(f"  {c}", file=sys.stderr)
                        return 1
                if pkg_now != "PASS" and scope_now != "no-code-change":
                    print(f"error: promote 硬门禁：baseline {args.baseline_id} "
                          f"package_result={pkg_now} 非 PASS（动过 code 须 ws_package "
                          f"打包证据；evidence_scope=no-code-change 不受限）",
                          file=sys.stderr)
                    return 1
                # 方向 3（本批意图 3）promote 一致性校验：收据 package 字段（内嵌
                # 打包证据，随收据入库可追溯）推导的 package_result 须与基线
                # package_result 一致，不一致即阻断——堵"基线记 PASS 而收据无
                # 内嵌打包证据"（gitignore 域证据不可追溯）或登记/晋升间人为
                # 改写漂移。两 package 门禁均须在写快照前（门禁失败不落污染快照）
                r_pkg, pkg_errs = read_receipt(receipt)
                if pkg_errs:
                    print(f"error: promote 一致性校验：收据解析错误 {receipt}: "
                          f"{'; '.join(pkg_errs)}", file=sys.stderr)
                    return 1
                # 方向 2（本批意图 2）：promote 改写 evidence_scope 同样过 prepare
                # 已有的收据 cases 子集校验——晋升一步不得声称未实测的用例范围
                #（add-candidate 有此防线，promote 分支此前直接赋值不校验）。
                # no-code-change 为豁免标记（非 case 标签），方向 1 已机器核对
                if scope_now and scope_now != "no-code-change":
                    pkg_receipt_cases = {c.strip() for c in
                                         (r_pkg.cases or "").split(",") if c.strip()}
                    manual = {c.strip() for c in
                              scope_now.split(",") if c.strip()}
                    if manual and not manual.issubset(pkg_receipt_cases):
                        extra = ", ".join(sorted(manual - pkg_receipt_cases))
                        print(f"error: promote 改写 evidence_scope 超出收据实测 "
                              f"cases（{sorted(pkg_receipt_cases) or '无'}），"
                              f"过度声称拒绝: {extra}", file=sys.stderr)
                        return 1
                expected_pkg = _derive_package_result(r_pkg, scope_now)
                if expected_pkg != pkg_now:
                    print(f"error: promote 一致性校验：收据 package 证据推导 "
                          f"{expected_pkg}，与基线 package_result={pkg_now} 不一致"
                          f"（收据内嵌打包证据缺失或与登记不符），阻断晋升",
                          file=sys.stderr)
                    return 1
                # 方向 1（本批意图 1）promote 发布全量组门禁：sync_manifest 收据
                # cases 须覆盖 verify-cases.yaml cases 段全部 case，缺项即阻断并
                # 列出缺失名——两级策略从注释契约升级为机器核对（少跑不能背书
                # 基线，如 BL-20260905-01 自称全量 11 实录 10 缺 lcview-trigger）。
                # no-code-change（无代码改动）豁免，与 package 硬门禁同口径；
                # 门禁在写快照前（失败不落污染快照）
                pkg_cov_result, pkg_cov_missing, pkg_cov_run = cases_coverage(r_pkg.cases)
                if pkg_cov_result != "full" and scope_now != "no-code-change":
                    print(f"error: promote 发布全量组门禁：收据 cases 未覆盖 "
                          f"verify-cases.yaml 全部 {len(verify_case_ids())} case"
                          f"（实跑 {pkg_cov_run}），缺失: "
                          f"{', '.join(pkg_cov_missing) or '无'}（发布前须全量验收；"
                          f"evidence_scope=no-code-change 不受限）", file=sys.stderr)
                    return 1
                snapshot_path.write_text(receipt.read_text(encoding="utf-8"),
                                         encoding="utf-8")
                b["status"] = "promoted"
                b["approved_by"] = args.approved_by
                b["approved_at"] = datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
                # 方向 3（本批意图 3）：promote 不再删除 status=fixed 的
                # known-issues 记录（KIR-006 清算删除废止），改归档进基线文档
                # （证据快照）新增段落，逐条记 id/标题/修复提交——终态记录随
                # 文档留存可追溯，registry 不再清零。evidence.known_issues_closed
                # 仍入档供 yaml 复核（明细含 resolved_in/title），文件保留。
                closed_details = closed_issue_details()
                evidence = b.get("evidence")
                if isinstance(evidence, dict):
                    evidence["known_issues_closed"] = closed_details
                else:
                    print(f"warn: evidence 非字典（{type(evidence).__name__}），"
                          "写不成 known_issues_closed 清单（归档仍入基线文档）",
                          file=sys.stderr)
                if closed_details:
                    # 方向 6：归档前回写 archived_in 到终态条目文件头（标记归属
                    # 基线；已归档条目 closed_issue_details 已过滤，不再重复归档）
                    paths_by_id = closed_issue_paths()
                    for d in closed_details:
                        p = paths_by_id.get(d["issue_id"])
                        if p:
                            set_archived_in(p, args.baseline_id)
                    archive_lines = ["", "## 已修复问题归档", ""]
                    archive_lines += [
                        f"- {d['issue_id']} | {d['title']} | "
                        f"修复提交: {d['resolved_in'] or '未记'}"
                        for d in closed_details]
                    with snapshot_path.open("a", encoding="utf-8") as fh:
                        fh.write("\n".join(archive_lines) + "\n")
                save(data)
                print(f"promoted: {args.baseline_id}（快照: {snapshot_path}）")
                return 0
        print(f"error: 未找到 baseline {args.baseline_id}")
        return 1

    if args.action == "revert-candidate":
        for b in baselines:
            if b.get("baseline_id") == args.baseline_id:
                if b.get("status") != "promoted":
                    print(f"error: baseline {args.baseline_id} 状态为 "
                          f"{b.get('status')!r}，仅 promoted 可 revert-candidate",
                          file=sys.stderr)
                    return 1
                b["status"] = "candidate"
                b.pop("approved_by", None)
                b.pop("approved_at", None)
                save(data)
                print(f"reverted-candidate: {args.baseline_id}")
                return 0
        print(f"error: 未找到 baseline {args.baseline_id}")
        return 1


if __name__ == "__main__":
    sys.exit(main())