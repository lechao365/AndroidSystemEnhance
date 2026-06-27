"""loop_deploy CLI：le deploy 子命令逻辑。"""
from __future__ import annotations

import argparse
import json as _json
import os
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
    p.add_argument("--artifact", action="append", default=[], help="手动指定编译产物路径（可重复）")
    p.add_argument("--remote", help="手动指定远程推送路径")
    p.add_argument("--service", default="", help="手动指定 restart 的服务名")
    p.add_argument("--adb-endpoint", default="", help="adb endpoint（格式 <ip>:5555，由 serial bootstrap 动态发现）")
    p.add_argument("--adb-serial", default="", help="adb device serial")
    p.add_argument("--skip-compile", action="store_true", help="跳过内置编译（artifacts 由 --artifact 提供）")
    p.set_defaults(func=_handle_deploy)


def _emit_deploy_ctx(result, plan) -> None:
    """输出结构化 deploy_context 到 stdout，供调用方跨进程解析。"""
    block_device = plan.deploy_targets[0].block_device if plan.deploy_targets else "/dev/block/mmcblk0p1"
    ctx = {
        "mode": result.mode.value,
        "backup_path": result.backup_path,
        "backup_sha": result.backup_sha,
        "deployed_files": result.deployed_files,
        "error": result.error,
        "error_code": result.error_code.value,
        "block_device": block_device,
    }
    print(f"DEPLOY_CTX: {_json.dumps(ctx)}")


def _handle_deploy(args: argparse.Namespace) -> int:
    if args.mode:
        mode = DeployMode(args.mode)
        plan = DeployPlan(mode=mode, reason="manual mode override")
    else:
        ws_root = os.environ.get("AOSP_ROOT", "")
        diff_files = get_diff_files(args.diff_rev, cwd=ws_root)
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

    artifacts: list[str] = list(args.artifact)
    if not args.skip_compile and not artifacts:
        compile_result = compile_plan(plan)
        if not compile_result.success:
            print(f"COMPILE FAILED: {compile_result.error}", file=sys.stderr)
            return 1
        artifacts = compile_result.artifacts

    if not args.adb_endpoint:
        print("ERROR: --adb-endpoint 未指定。", file=sys.stderr)
        return 1
    endpoint = args.adb_endpoint
    serial = args.adb_serial or endpoint
    client = AdbClient(endpoint, serial)
    deployer = Deployer(client)
    result = deployer.deploy(plan, artifacts)

    # 无论成功失败都输出 DEPLOY_CTX（回滚元数据不能丢在进程边界）
    _emit_deploy_ctx(result, plan)

    if result.success:
        print(f"DEPLOY OK: mode={result.mode.value} duration={result.duration_seconds:.1f}s reboot={result.requires_reboot}")
        return 0
    else:
        print(f"DEPLOY FAILED: {result.error}", file=sys.stderr)
        return 1
