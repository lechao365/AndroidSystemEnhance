"""baseline-status.yaml candidate/promoted 登记辅助。

新流程登记从 candidate 起步（archive 仅旧流程历史）。
sync_manifest 字段复用为 data/verify 收据路径（spec §7）。
save() 手工保留 yaml 头部注释块（PyYAML 往返不保留注释）。
"""
import argparse
import datetime
import sys
from pathlib import Path

import yaml

# 本文件位于 harness/skills/sync-modify-to-main-base/，parents[2] = harness
CONFIG = Path(__file__).resolve().parents[2] / "config" / "baseline-status.yaml"

# 仿 ws_report.py：引入 cross-device 共享收据模块，candidate 实读真实 verify 收据
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_receipt import read_receipt  # noqa: E402


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
    ap.add_argument("action", choices=["add-candidate", "promote", "revert-candidate"])
    ap.add_argument("--baseline-id")
    ap.add_argument("--source-commit")
    ap.add_argument("--receipt-path")
    ap.add_argument("--approved-by")
    args = ap.parse_args(argv)

    data = load()
    baselines = data.setdefault("baselines", [])
    today = datetime.date.today().strftime("%Y%m%d")

    if args.action == "add-candidate":
        if not args.receipt_path:
            print("error: add-candidate 必须传 --receipt-path（证据链要求实读 verify 收据）",
                  file=sys.stderr)
            return 1
        try:
            r = read_receipt(args.receipt_path)
        except (OSError, UnicodeDecodeError) as e:
            print(f"error: 读取收据失败 {args.receipt_path}: {e}", file=sys.stderr)
            return 1
        # build/package 共用收据 build 阶段，board_verify 取 push_board，均大写（不再伪造 PASS）
        build_result = (r.build or "skip").upper()
        board_verify = (r.push_board or "skip").upper()
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
            "evidence": {
                "build_result": build_result,
                "package_result": build_result,
                "board_verify": board_verify,
                "sync_manifest": args.receipt_path,
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
                b["status"] = "promoted"
                b["approved_by"] = args.approved_by or "lechao"
                b["approved_at"] = datetime.datetime.now(
                    datetime.timezone(datetime.timedelta(hours=8))
                ).strftime("%Y-%m-%dT%H:%M:%S+08:00")
                save(data)
                print(f"promoted: {args.baseline_id}")
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