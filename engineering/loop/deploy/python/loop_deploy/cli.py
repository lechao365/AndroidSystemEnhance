"""loop_deploy CLI：le deploy 子命令逻辑。"""
from __future__ import annotations

import argparse
import sys
from loop_deploy.decider import decide, get_diff_files
from loop_deploy.compiler import compile_plan
from loop_deploy.deployer import Deployer
from loop_deploy.models import DeployMode, DeployPlan
from loop_adb.client import AdbClient


_DEPLOY_MODES = [m.value for m in DeployMode]


def add_deploy_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("deploy", help="部署 binary/image 到设备")
    p.add_argument("--decide", action="store_true", help="仅输出决策不执行（dry-run）")
    p.add_argument("--diff-rev", default="HEAD", help="git diff 基准（默认 HEAD）")
    p.add_argument("--mode", choices=_DEPLOY_MODES, help="强制指定部署模式（跳过决策器）")
    p.add_argument("--artifact", help="手动指定编译产物路径")
    p.add_argument("--remote", help="手动指定远程推送路径")
    p.add_argument("--service", default="", help="手动指定 restart 的服务名")
    p.add_argument("--adb-endpoint", default="", help="adb endpoint（格式 <ip>:5555，由 serial bootstrap 动态发现）")
    p.add_argument("--adb-serial", default="", help="adb device serial")
    p.set_defaults(func=_handle_deploy)


def _handle_deploy(args: argparse.Namespace) -> int:
    if args.mode:
        mode = DeployMode(args.mode)
        plan = DeployPlan(mode=mode, reason="manual mode override")
    else:
        diff_files = get_diff_files(args.diff_rev)
        plan = decide(diff_files)

    if args.decide:
        print(f"mode={plan.mode.value}")
        print(f"changed={plan.changed_files}")
        print(f"reason={plan.reason}")
        print(f"build_targets={plan.build_targets}")
        print(f"reboot={plan.requires_reboot}")
        return 0

    if plan.mode == DeployMode.FLASH_FULL:
        print(f"ERROR: FLASH_FULL required: {plan.reason}", file=sys.stderr)
        print("Please manually build full image and flash SD card.", file=sys.stderr)
        return 1

    if plan.mode == DeployMode.SKIP:
        print(f"SKIP: {plan.reason}")
        return 0

    artifacts = [args.artifact] if args.artifact else []
    if not args.artifact:
        compile_result = compile_plan(plan)
        if not compile_result.success:
            print(f"COMPILE FAILED: {compile_result.error}", file=sys.stderr)
            return 1
        artifacts = compile_result.artifacts

    if not args.adb_endpoint:
        print("ERROR: --adb-endpoint 未指定。", file=sys.stderr)
        print("动态 IP 场景下必须先跑 serial bootstrap 获取设备 IP：", file=sys.stderr)
        print("  le run --suite cases/system/network-adbd-success.yaml --host <serial_host> --port <serial_port>", file=sys.stderr)
        print("或手动通过 rp5_serial_helper.py device-ip 获取后传入 --adb-endpoint <ip>:5555", file=sys.stderr)
        return 1
    endpoint = args.adb_endpoint
    serial = args.adb_serial or endpoint
    client = AdbClient(endpoint, serial)
    deployer = Deployer(client)
    result = deployer.deploy(plan, artifacts)

    if result.success:
        print(f"DEPLOY OK: mode={result.mode.value} duration={result.duration_seconds:.1f}s reboot={result.requires_reboot}")
        # 输出结构化 deploy_context（供调用方解析，跨进程传递 backup/deploy 元数据）
        import json as _json
        _ctx = {
            "mode": result.mode.value,
            "backup_path": result.backup_path,
            "backup_sha": result.backup_sha,
            "deployed_files": result.deployed_files,
        }
        print(f"DEPLOY_CTX: {_json.dumps(_ctx)}")
        return 0
    else:
        print(f"DEPLOY FAILED: {result.error}", file=sys.stderr)
        return 1
