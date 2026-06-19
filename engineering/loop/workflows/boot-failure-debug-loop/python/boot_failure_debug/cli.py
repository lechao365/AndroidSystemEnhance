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

from loop_core.observer import capture_snapshot as core_capture_snapshot
from loop_core.report import render_summary, write_report_bundle
from loop_core.transport import FixtureTransport

from boot_failure_debug.config import load_profiles
from boot_failure_debug.runner import BootFailureRunner

# boot-failure 特有的报告建议映射
BOOT_FAILURE_ADVICE = {
    "kernel_panic_detected": "检查 kernel panic 日志定位崩溃模块",
    "reboot_loop_detected": "分析 boot cycle 边界，检查 early boot 失败点",
    "no_output_after_attach": "检查串口连接、设备上电状态",
    "kernel_boot_hang": "检查 boot 卡死位置，尝试延长观察窗口",
    "login_prompt_not_reached": "尝试 send_enter 唤起 shell prompt",
    "shell_prompt_available": "系统正常启动，可继续正常开发流程",
}


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
        # live mode：需要 rp5_serial provider
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

    # 执行状态机
    runner = BootFailureRunner(cfg, transport)
    attempt = runner.run()

    # 生成报告
    snapshot_lines = None
    if args.fixture:
        snap = core_capture_snapshot(
            transport,
            timeout_sec=cfg.observe_timeout_sec,
            prompt_markers=cfg.prompt_markers,
            recent_limit=cfg.recent_lines_limit,
            cycle_markers=cfg.reboot_markers,
        )
        snapshot_lines = [line.text for line in snap.lines]

    write_report_bundle(
        attempt,
        args.artifacts_dir,
        snapshot_lines=snapshot_lines,
        advice_map=BOOT_FAILURE_ADVICE,
    )

    # 输出摘要
    print(render_summary(attempt, advice_map=BOOT_FAILURE_ADVICE))

    return 0


if __name__ == "__main__":
    sys.exit(main())
