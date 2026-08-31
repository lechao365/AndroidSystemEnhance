"""baseline-status.yaml candidate/promoted 登记辅助。

新流程登记从 candidate 起步（archive 仅旧流程历史）。
sync_manifest 字段复用为 data/verify-results 收据路径（spec §7）。
save() 手工保留 yaml 头部注释块（PyYAML 往返不保留注释）。
"""
import argparse
import datetime
import subprocess
import sys
from pathlib import Path

import yaml

# 本文件位于 harness/skills/publish-main-base/，parents[2] = harness
CONFIG = Path(__file__).resolve().parents[2] / "config" / "baseline-status.yaml"

# 仿 ws_report.py：引入 cross-device 共享收据模块，candidate 实读真实 verify 收据
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_receipt import read_receipt  # noqa: E402
from cdp_issue import issue_files, read_issue, validate_issue  # noqa: E402
from cdp_paths import data_baselines_dir  # noqa: E402


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


def next_id(data, today):
    existing = [b.get("baseline_id") for b in data.get("baselines", [])]
    n = 1
    while f"BL-{today}-{n:02d}" in existing:
        n += 1
    return f"BL-{today}-{n:02d}"


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
    ap.add_argument("--evidence-scope", help="证据范围标签（如 lcview-liveness）；add-candidate 必填")
    args = ap.parse_args(argv)

    # check-issues：known-issues 门禁（publish_main_base.sh 委托；不读写登记 yaml）
    if args.action == "check-issues":
        if not args.task:
            print("error: check-issues 必须传 --task（门禁按任务过滤）", file=sys.stderr)
            return 1
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
        excludes = ("harness/config/baseline-status.yaml", "docs/")
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
        # evidence_scope：证据范围标签必填（缺则退 1）——登记必须声明证据覆盖范围
        evidence_scope = (args.evidence_scope or "").strip()
        if not evidence_scope:
            print("error: add-candidate 必须传 --evidence-scope（证据范围标签，缺则拒绝登记）",
                  file=sys.stderr)
            return 1
        try:
            r = read_receipt(args.receipt_path)
        except (OSError, UnicodeDecodeError) as e:
            print(f"error: 读取收据失败 {args.receipt_path}: {e}", file=sys.stderr)
            return 1
        # build/package 共用收据 build 阶段，board_verify 取 push_board，均大写（不再伪造 PASS）
        # 空值（""/None/纯空白）记 FAIL 不记 SKIP——空值不是合法 skip 证据，证据链从严
        build_result = ((r.build or "").strip() or "FAIL").upper()
        board_verify = ((r.push_board or "").strip() or "FAIL").upper()
        # ki_gate：known-issues 门禁结论（拒批已在脚本层 exit，缺参视为 not-run）
        ki_gate = (args.ki_gate or "").strip() or "not-run"
        # 去重复用：同 source_commit 且仍为 candidate 的记录不新增（防重复 prepare 冗余登记；
        # 收据路径不同则对齐最新证据，保持 promote 证据链一致）
        for b in baselines:
            if (b.get("source_commit") == args.source_commit
                    and b.get("status") == "candidate"):
                if b.get("sync_manifest") != args.receipt_path:
                    b["sync_manifest"] = args.receipt_path
                    b["build_result"] = build_result
                    b["package_result"] = build_result
                    b["board_verify"] = board_verify
                    b["evidence_scope"] = evidence_scope
                    b["evidence"] = {
                        "build_result": build_result,
                        "package_result": build_result,
                        "board_verify": board_verify,
                        "sync_manifest": args.receipt_path,
                        "ki_gate": ki_gate,
                        "evidence_scope": evidence_scope,
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
            "package_result": build_result,
            "board_verify": board_verify,
            "evidence_scope": evidence_scope,
            "evidence": {
                "build_result": build_result,
                "package_result": build_result,
                "board_verify": board_verify,
                "sync_manifest": args.receipt_path,
                "ki_gate": ki_gate,
                "evidence_scope": evidence_scope,
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
                # promote 允许透传/改写 evidence_scope（如零改动豁免时改写 no-code-change）
                scope = (args.evidence_scope or "").strip()
                if scope:
                    b["evidence_scope"] = scope
                    if isinstance(b.get("evidence"), dict):
                        b["evidence"]["evidence_scope"] = scope
                b["status"] = "promoted"
                b["approved_by"] = args.approved_by or "lechao"
                b["approved_at"] = datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
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