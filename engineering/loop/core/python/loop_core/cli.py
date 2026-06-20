"""LE 统一 CLI 入口。

子命令：
- run：执行用例集，输出 EvidenceBundle
- gen-cases：AI 辅助用例生成（第二步实现，当前占位）
- deploy：部署 binary/image（第二步实现，当前占位）

用法：
    python3 -m loop_core.cli run --suite boot-success --fixture <jsonl> ...
    python3 -m loop_core.cli run --suite boot-success --host 127.0.0.1 --port 9700 ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loop_core.case_loader import load_suite
from loop_core.config import DeviceProfile
from loop_core.evidence import write_evidence_bundle
from loop_core.report import render_summary
from loop_core.runner import LoopRunner
from loop_core.transport import FixtureTransport


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    Returns:
        退出码（0=成功，非零=失败）
    """
    parser = argparse.ArgumentParser(
        description="Loop Engineering v2：用例驱动验收器"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run 子命令
    run_parser = sub.add_parser("run", help="执行用例集")
    run_parser.add_argument("--suite", required=True, help="suite YAML 文件路径")
    run_parser.add_argument("--fixture", help="JSONL fixture 文件路径（离线回放模式）")
    run_parser.add_argument("--host", default="127.0.0.1", help="host 地址（live 模式）")
    run_parser.add_argument("--port", type=int, default=9700, help="host 端口（live 模式）")
    run_parser.add_argument(
        "--device-profile", required=True, help="设备 profile JSON 路径"
    )
    run_parser.add_argument(
        "--case-dirs",
        default="",
        help="include 搜索目录（逗号分隔）",
    )
    run_parser.add_argument(
        "--artifacts-dir", required=True, help="artifacts 输出目录"
    )
    run_parser.add_argument(
        "--capture-timeout",
        type=float,
        default=None,
        help="输出采集超时（秒），缺省按 CLI > suite.defaults > profile 默认 兜底",
    )
    run_parser.add_argument(
        "--recent-limit",
        type=int,
        default=None,
        help="采集行数上限，缺省按 CLI > suite.defaults > profile 默认 兜底",
    )

    # gen-cases 占位
    sub.add_parser("gen-cases", help="AI 辅助用例生成（第二步实现）")

    # deploy 占位
    sub.add_parser("deploy", help="部署 binary/image（第二步实现）")

    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "gen-cases":
        print("gen-cases 命令将在第二步实现", file=sys.stderr)
        return 1
    if args.command == "deploy":
        print("deploy 命令将在第二步实现", file=sys.stderr)
        return 1
    return 1


def _cmd_run(args) -> int:
    """执行 run 子命令。"""
    # 加载 device profile
    device_raw = json.loads(Path(args.device_profile).read_text(encoding="utf-8"))
    profile = DeviceProfile(**{
        k: v for k, v in device_raw.items()
        if k in DeviceProfile.__dataclass_fields__
    })

    # 解析 case-dirs
    case_dirs = [d.strip() for d in args.case_dirs.split(",") if d.strip()] if args.case_dirs else []
    # suite 文件所在目录自动加入搜索路径
    suite_dir = str(Path(args.suite).parent)
    if suite_dir not in case_dirs:
        case_dirs.append(suite_dir)

    # 加载 suite
    suite = load_suite(args.suite, case_dirs)

    # 选择 transport
    if args.fixture:
        transport = FixtureTransport.from_jsonl(args.fixture)
    else:
        try:
            from rp5_serial.client.automation import AutomationClient
            from rp5_serial.transport import Rp5SerialTransport
        except ImportError:
            print(
                "ERROR: live mode 需要 rp5_serial provider，请设置 PYTHONPATH",
                file=sys.stderr,
            )
            return 1
        client = AutomationClient(args.host, args.port)
        try:
            client.connect()
        except OSError as e:
            print(f"ERROR: 无法连接 host {args.host}:{args.port}: {e}", file=sys.stderr)
            return 1
        transport = Rp5SerialTransport(client)

    # 执行
    # 参数优先级：CLI 显式 > suite.defaults > profile 默认 > 硬编码兜底
    if args.capture_timeout is not None:
        capture_timeout = args.capture_timeout
    elif suite.defaults.capture_timeout is not None:
        capture_timeout = suite.defaults.capture_timeout
    else:
        capture_timeout = profile.default_capture_timeout

    if args.recent_limit is not None:
        recent_limit = args.recent_limit
    elif suite.defaults.recent_limit is not None:
        recent_limit = suite.defaults.recent_limit
    else:
        recent_limit = profile.default_recent_limit

    runner = LoopRunner(
        device_id=profile.device_id,
        prompt_markers=profile.prompt_markers,
        transport=transport,
        suite=suite,
        capture_timeout=capture_timeout,
        recent_limit=recent_limit,
    )
    if hasattr(transport, "set_cycle_markers"):
        transport.set_cycle_markers(profile.reboot_markers)
    try:
        bundle = runner.run()
    except Exception as exc:
        # 顶层兜底：任何运行时异常都产出 failure bundle，保证 AI 拿到结构化证据
        bundle = runner.build_failure_bundle(f"runtime error: {exc}")

    # 输出
    paths = write_evidence_bundle(bundle, args.artifacts_dir)
    print(render_summary(bundle))
    print(f"\nEvidenceBundle: {paths['evidence_json']}")

    return 0 if bundle.summary["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
