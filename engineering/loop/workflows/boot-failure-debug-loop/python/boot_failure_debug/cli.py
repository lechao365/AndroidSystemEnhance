"""boot-failure-debug-loop CLI 入口。

支持两种模式：
1. fixture mode：基于 JSONL transcript 离线回放
2. live mode：连接 rp5-serial Host（需要 --host / --port）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from boot_failure_debug.config import load_profiles
from boot_failure_debug.observer import capture_snapshot
from boot_failure_debug.report import render_summary, write_report_bundle
from boot_failure_debug.runner import BootFailureRunner
from boot_failure_debug.transport import FixtureTransport, Rp5SerialTransport


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    Returns:
        退出码（0=成功，非零=失败）
    """
    parser = argparse.ArgumentParser(
        description="boot-failure-debug-loop v1：启动失败诊断闭环"
    )
    parser.add_argument(
        "--fixture",
        help="JSONL transcript fixture 文件路径（离线回放模式）",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="rp5-serial Host 地址（live 模式，默认 127.0.0.1）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9700,
        help="rp5-serial Host 端口（live 模式，默认 9700）",
    )
    parser.add_argument(
        "--device-profile",
        required=True,
        help="设备 profile JSON 路径（rp5）",
    )
    parser.add_argument(
        "--workflow-profile",
        required=True,
        help="workflow profile JSON 路径（boot-failure-debug）",
    )
    parser.add_argument(
        "--override-json",
        help="运行时覆盖字段 JSON 文件路径",
    )
    parser.add_argument(
        "--artifacts-dir",
        required=True,
        help="artifacts 输出目录",
    )

    args = parser.parse_args(argv)

    # 加载配置
    override: dict | None = None
    if args.override_json:
        override = json.loads(Path(args.override_json).read_text())

    cfg = load_profiles(
        args.device_profile,
        args.workflow_profile,
        override=override,
    )

    # 选择 transport
    if args.fixture:
        transport = FixtureTransport.from_jsonl(args.fixture)
    else:
        # live mode：需要 rp5_serial.AutomationClient
        try:
            from rp5_serial.client.automation import AutomationClient
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

    # 执行状态机
    runner = BootFailureRunner(cfg, transport)
    attempt = runner.run()

    # 生成报告
    snapshot_lines = None
    if args.fixture:
        # fixture mode 下 snapshot_lines 来自 fixture 自身
        # 为简化，直接用 capture_snapshot 的 lines 文本
        snap = capture_snapshot(transport, cfg, timeout_sec=cfg.observe_timeout_sec)
        snapshot_lines = [line.text for line in snap.lines]

    write_report_bundle(attempt, args.artifacts_dir, snapshot_lines=snapshot_lines)

    # 输出摘要
    print(render_summary(attempt))

    return 0


if __name__ == "__main__":
    sys.exit(main())