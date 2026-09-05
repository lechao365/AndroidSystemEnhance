"""baseline-status.yaml candidate/promoted 登记辅助。

新流程登记从 candidate 起步（archive 仅旧流程历史）。
sync_manifest 字段复用为 data/verify-results 收据路径（spec §7）。
save() 手工保留 yaml 头部注释块（PyYAML 往返不保留注释）。
"""
import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path

import yaml

# 本文件位于 harness/skills/publish-main-base/，parents[2] = harness
CONFIG = Path(__file__).resolve().parents[2] / "config" / "baseline-status.yaml"

# 仿 ws_report.py：引入 cross-device 共享收据模块，candidate 实读真实 verify 收据
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_receipt import read_receipt  # noqa: E402
from cdp_issue import (closed_issue_details, delete_closed,
                       issue_files, read_index, read_issue, validate_issue)  # noqa: E402
from cdp_paths import data_baselines_dir, project_root  # noqa: E402


def load():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


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


def main(argv=None):
    ap = argparse.ArgumentParser(description="baseline candidate/promoted 登记")
    ap.add_argument("action",
                    choices=["add-candidate", "promote", "revert-candidate",
                             "check-issues", "verify-tree"])
    ap.add_argument("--baseline-id")
    ap.add_argument("--source-commit")
    ap.add_argument("--receipt-path")
    ap.add_argument("--approved-by")
    ap.add_argument("--task")
    ap.add_argument("--ki-gate", help="known-issues 门禁结论 pass/not-run，写入 evidence")
    ap.add_argument("--evidence-scope", help="证据范围标签（如 lcview-liveness）；"
                        "缺省从收据 cases 推导，人工传值须为其子集（防过度声称）")
    ap.add_argument("--package-evidence",
                    help="ws_package 打包证据 JSON 路径（缺省按收据 batch_id 探测 "
                         "harness/log/workspace-verify/package-<batch_id>.json）")
    ap.add_argument("--known-issues-carried",
                    help="带病登记 issue_id 列表（逗号分隔，写入 evidence 的 "
                         "known_issues_carried；缺参记空，只记录不阻断）")
    args = ap.parse_args(argv)

    # check-issues：known-issues 门禁（publish_main_base.sh 委托；不读写登记 yaml）
    if args.action == "check-issues":
        # 先判畸形登记：validate_issue 有红即拒（文件名/头字段/枚举/index 一致性全局把关，
        # 防 index 按空格切分错位等畸形记录污染门禁判定）
        for p in issue_files():
            errs = validate_issue(p)
            if errs:
                for e in errs:
                    print(f"{p.name}: {e}", file=sys.stderr)
                print("error: known-issues 畸形登记，拒绝（先修复登记再发布基线）",
                      file=sys.stderr)
                return 1
        # task 推断：缺省从 status 非 fixed 条目的 task 集合推断（自动，无需人工申报）
        active_tasks = {i.task for p in issue_files()
                        if (i := read_issue(p)).status != "fixed" and i.task}
        if args.task:
            # 白名单：显式传 --task 不在活跃集合内即 exit 3（防拼错静默通过；
            # 空集合时放行——无活跃任务则无冲突对象）
            if active_tasks and args.task not in active_tasks:
                print(f"error: --task {args.task!r} 不在活跃任务集合 "
                      f"{sorted(active_tasks)} 内（防拼错静默通过）",
                      file=sys.stderr)
                return 3
        else:
            if len(active_tasks) == 1:
                args.task = next(iter(active_tasks))
            elif len(active_tasks) > 1:
                print(f"error: 活跃任务集合多值 {sorted(active_tasks)}，"
                      f"须显式传 --task 之一", file=sys.stderr)
                return 1
            else:
                args.task = "empty-registry"
        # 再判目标任务未解决阻塞：origin=introduced 或 blocking 且 status!=fixed 即拒
        bad = []
        for p in issue_files():
            i = read_issue(p)
            if i.task != args.task:
                continue
            if (i.origin == "introduced" or i.blocking) and i.status != "fixed":
                bad.append(f"{p.name}: origin={i.origin} blocking={i.blocking} "
                           f"status={i.status}")
        if bad:
            print("\n".join(bad), file=sys.stderr)
            print(f"error: task={args.task} 存在未解决阻塞问题", file=sys.stderr)
            return 1
        print(f"known-issues 门禁通过（task={args.task} 无未解决阻塞问题）")
        return 0

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
        # 与 data/known-issues/（promote 清算删除目录，随晋升提交入库——
        # 不排除则清算删除让树等价断言必红回滚）
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
        receipt_cases = {c.strip() for c in (r.cases or "").split(",") if c.strip()}
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
        # package（方向 2，批次 ff33f92060ac）：由 ws_package 打包证据机械推导——
        # 证据 script_rc==0 记 PASS；evidence_scope=no-code-change 记 SKIP（无
        # 代码改动打包豁免）；其余（无证据/证据 rc 非 0/不可读）留 UNKNOWN 不声称。
        # 不再把 build_result 复制给 package_result，杜绝伪造打包证据
        pkg_evidence_path = ((args.package_evidence or "").strip()
                             or _package_evidence_path(r.batch_id))
        pkg_evidence = _load_package_evidence(pkg_evidence_path)
        pkg_rc = pkg_evidence.get("script_rc") if pkg_evidence else None
        if pkg_rc == 0:
            package_result = "PASS"
        elif evidence_scope == "no-code-change":
            package_result = "SKIP"
        else:
            package_result = "UNKNOWN"
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
        # 拒 FAIL：build/board_verify 为 FAIL 不可登记为基线（证据须 pass/skip）
        if build_result == "FAIL" or board_verify == "FAIL":
            print("error: 收据 build/board_verify 为 FAIL，拒绝登记"
                  "（基线证据须 pass/skip）", file=sys.stderr)
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
                    }
                    save(data)
                    print(f"candidate 复用并更新收据: {b['baseline_id']}（source_commit={args.source_commit}）")
                else:
                    print(f"candidate 复用: {b['baseline_id']}（source_commit={args.source_commit}）")
                return 0
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
                # 方向 6：审批凭据外部化——promote 空审批人即拒（在写快照前校验，
                # 防快照污染），不再回落默认常量（防审批可自证）
                if not args.approved_by:
                    print("error: promote 必须传 --approved-by"
                          "（审批凭据外部化，不再回落默认常量）", file=sys.stderr)
                    return 1
                snapshot_name = f"{args.baseline_id}-{receipt.name}"
                if not snapshot_name.endswith(".md"):
                    snapshot_name += ".md"
                snapshot_path = data_baselines_dir() / snapshot_name
                if snapshot_path.exists():
                    print(f"error: 证据快照已存在，拒绝覆盖: {snapshot_path}",
                          file=sys.stderr)
                    return 1
                snapshot_path.write_text(receipt.read_text(encoding="utf-8"),
                                         encoding="utf-8")
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
                if pkg_now != "PASS" and scope_now != "no-code-change":
                    print(f"error: promote 硬门禁：baseline {args.baseline_id} "
                          f"package_result={pkg_now} 非 PASS（动过 code 须 ws_package "
                          f"打包证据；evidence_scope=no-code-change 不受限）",
                          file=sys.stderr)
                    return 1
                b["status"] = "promoted"
                b["approved_by"] = args.approved_by
                b["approved_at"] = datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
                # promote 清算（KIR-006）：晋升前先把终态条目清单（status 属
                # fixed 或 wontfix，不看 blocking）记入 evidence.known_issues_closed
                # 再 save——快照与清单先入档，随后 delete_closed 删文件（终态
                # 记录随清单入档，删除不销毁证据链）；删失败仅 warn 不回滚快照。
                # 清单存明细列表（issue_id/resolved_in/title），只存 id 在删文件
                # 后无从辨认；evidence 非字典写不成清单时跳过清算删除并告警
                #（无清单入档即删 = 无快照删证据）
                closed_details = closed_issue_details()
                evidence = b.get("evidence")
                if isinstance(evidence, dict):
                    evidence["known_issues_closed"] = closed_details
                    save(data)
                    try:
                        delete_closed([d["issue_id"] for d in closed_details])
                    except OSError as e:
                        print(f"warn: 终态条目清算删除失败（快照与清单已入档不回滚）: "
                              f"{e}", file=sys.stderr)
                else:
                    print(f"warn: evidence 非字典（{type(evidence).__name__}），"
                          "写不成 known_issues_closed 清单，跳过清算删除"
                          "（无清单入档即删 = 无快照删证据）", file=sys.stderr)
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