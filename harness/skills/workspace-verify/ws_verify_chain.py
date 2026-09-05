#!/usr/bin/env python3
# ============================================================
# ws_verify_chain.py — 上板验证确定性全链编排（六步串联）
# 所属模块：workspace-verify — 编译产物上板验证
# 设计目的：各步间原为 AI 编排往返（收据 gap_before_verify_* 多段 ~55s/批）。
#   本脚本把确定性步骤串联为单次执行：
#     sync → connect → push → unit_test → acceptance → report
#   逐段透传 stdout、rc 逐段门禁、失败停链（余下验证步记 skipped，report
#   步仍执行——fail 收据由前序真实 rc 机械派生落盘，loop done --receipt
#   契约要求失败轮次也有收据记账，A1 修订），
#   末尾输出自描述 JSON（run_id/逐段真实 rc 与起止/overall/skipped/canceled）。
#   acceptance/report 参数由 --batch-file/--case/--wait-ready/--log-since
#   确定性构造；report 收据参数（result/build/board/summary）由前序步骤
#   真实 rc 机械派生（AI 不再手填，消除三步间编排 gap 约 55s/批）。
# 运行态落盘（仅编排器写）：harness/log/workspace-verify/runs/<run_id>.json
#   记每步 stage 真实 rc/起止 epoch/canceled 与 skipped 记账，原子写；
#   子脚本与 AI 只读不写（ws_session done --run-file 取运行态 stage/rc）。
# 并发安全：编排进出经 ws_lock.verify_locks 加解 workspace/device 两把
#   文件锁（finally 成对释放；占用 exit 3，等待策略归调用方）。
# 进程隔离：每步子进程 start_new_session 独立进程组；单步超时 killpg
#   有界 teardown（TERM→宽限 10s→KILL），被杀步骤 rc=None + canceled=true。
# 打点：链起止自发 verify_start/verify_end（batch 归属明确时）；各子脚本
#   自发 mark（verify_sync/push/unit_test/acceptance 口径不变）；
#   connect/report 不打点（连接量不到、收据即终点）。
# 子步骤产物共享 run_id：编排器把 run_id 注入 CDP_RUN_ID 环境变量，push/
#   unit_test/acceptance 产物同批同 run_id（ws_report PASS 同批核验依赖）。
# 用法：python3 ws_verify_chain.py [--product rpi5] [--out <aosp out>]
#   [--result-file <json>] [--batch-file <cdp>] [--case <标签>]
#   [--wait-ready] [--log-since <ts>] [--build pass|fail|skip]
# 退出码：0 全链过 / 1 某步失败（JSON 标注停在哪步；fail 收据仍落盘）/
#   2 参数错误 / 3 编排锁被占用（workspace/device 互斥）
# ============================================================

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from contextlib import nullcontext
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SYNC = _SCRIPT_DIR.parent / "sync-code-to-workspace" / "sync_code_to_workspace.py"
# 复用仓内共享库：cdp_parse（batch_id 解析，与 ws_report 同路径注入方式）
# _SCRIPT_DIR=harness/skills/workspace-verify（目录）：parents[0]=skills
sys.path.insert(0, str(_SCRIPT_DIR.parents[0] / "cross-device" / "lib" / "python"))
sys.path.insert(0, str(_SCRIPT_DIR.parents[1] / "lib"))
from cdp_parse import batch_id_from_text  # noqa: E402
from verify_common import atomic_write_json as _atomic_write_json_impl  # noqa: E402

import ws_lock  # noqa: E402

# 链式步骤名序列（可注入单测）：argv 由 _build_argv/_build_report_argv 按名构造
_CHAIN_STEPS = ("sync", "connect", "push", "unit_test", "acceptance", "report")

# 单步超时（秒）：覆盖各子脚本内部 timeout 之上的一层编排护栏；
# 超时走 killpg 有界 teardown，防止子脚本挂死拖垮整链
_STEP_TIMEOUTS = {
    "sync": 900,        # 同步含增量 rsync，历史上 <300s
    "connect": 420,     # ensure 含 mDNS 发现 + 静态 fallback + rescue 重试
    "push": 1800,       # 推送含 reboot_and_wait（boot_timeout 240s）
    "unit_test": 1500,  # 全量 gtest 上板执行
    "acceptance": 1200, # 逐标签探针执行
    "report": 300,      # 收据落盘（PASS 核验读产物）
}

# 进程组 teardown 有界宽限：TERM 后等待退出上限，超限 KILL
_TERM_GRACE_S = 10
_KILL_WAIT_S = 30

_RUNS_DIR = _SCRIPT_DIR.parents[1] / "log" / "workspace-verify" / "runs"
_CROSS_DEVICE_LOG = _SCRIPT_DIR.parents[1] / "log" / "cross-device"


def _atomic_write_json(path, data):
    """原子写 JSON：薄壳委托 verify_common.atomic_write_json（批次四收敛，
    统一 tmp 带 pid 原语；签名与调用点不变，防半写产物污染证据链）。"""
    _atomic_write_json_impl(path, data)


def _run_step(argv, timeout):
    """独立进程组执行一步；返回 (rc, canceled)。

    start_new_session 使子进程自成进程组：超时可 killpg 整组回收
    （子脚本再 spawn 的 adb/make 孙进程一并终止，不留孤儿占用设备）。
    stdout/stderr 不 capture：直通终端，rc 真实。
    """
    proc = subprocess.Popen(argv, start_new_session=True)
    try:
        return proc.wait(timeout=timeout), False
    except subprocess.TimeoutExpired:
        # 有界 teardown：TERM → 宽限 → KILL，两段都有上界，不无限等
        for sig, grace in ((signal.SIGTERM, _TERM_GRACE_S),
                           (signal.SIGKILL, _KILL_WAIT_S)):
            try:
                os.killpg(proc.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass  # 进程组已退出/无权限：直接进入下一段等待
            try:
                proc.wait(timeout=grace)
                return None, True  # 已终止：无真实 rc，记 canceled
            except subprocess.TimeoutExpired:
                continue
        proc.wait()  # KILL 后必退（防御兜底，不预期到达）
        return None, True


def _build_argv(name, product, out, chain_args):
    """步骤名 → 子脚本 argv（各子脚本参数均为真实支持的参数）。"""
    if name == "sync":
        # code→workspace 同步与 AOSP out 无关，仅 --auto
        return [sys.executable, str(_SYNC), "--auto"]
    if name == "connect":
        # 连接 fail-fast：设备不可达时不浪费推送/单测轮次
        # （push/acceptance 内部仍各自 ensure，双保险不冲突）
        return [sys.executable, str(_SCRIPT_DIR / "ws_adb_connect.py"), "ensure"]
    if name == "push":
        cmd = [sys.executable, str(_SCRIPT_DIR / "ws_push.py"),
               "--product", product]
    elif name == "unit_test":
        cmd = [sys.executable, str(_SCRIPT_DIR / "ws_upload_tests.py"),
               "--product", product]
    elif name == "acceptance":
        cmd = [sys.executable, str(_SCRIPT_DIR / "ws_acceptance.py"), "run"]
        # 验收源三选一互斥（ws_acceptance 硬约束）：批次文件优先（模式 A
        # 真相源，case: 前缀自动查表），仅无批次时回落 --case
        if chain_args.get("batch_file"):
            cmd += ["--batch-file", chain_args["batch_file"]]
        elif chain_args.get("case"):
            cmd += ["--case", chain_args["case"]]
        if chain_args.get("wait_ready"):
            cmd += ["--wait-ready"]
        if chain_args.get("log_since"):
            cmd += ["--log-since", chain_args["log_since"]]
        acc_file = chain_args.get("acc_file")
        if acc_file:
            cmd += ["--result-file", acc_file]
        return cmd
    else:
        raise ValueError(f"未知链式步骤: {name}")
    if out:
        cmd += ["--out", out]
    # push/unit_test 产物落盘（--batch-file 在场才命名，供 report PASS 核验）
    art = chain_args.get(f"{name}_file")
    if art:
        cmd += ["--result-file", art]
    return cmd


def _run_selfcheck(timeout=600):
    """跑 harness/lib/selfcheck.py 取自检摘要文本（board 收据强制入收据，方向 4）。

    rc 全 0 与否由 ws_report 扫描 *_rc 键判定——本函数只负责把真实输出
    原样带入收据，不自评不吞错。异常兜底（B3/C2）：selfcheck 进程挂死
    （TimeoutExpired）或启动失败（OSError）时返回带 error 标注的文本交
    ws_report 的 *_rc 扫描判红，不沿编排栈上抛破坏链的自描述 JSON 输出
    与退出码语义。
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT_DIR.parents[1] / "lib" / "selfcheck.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
        return ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return (f"error: selfcheck 超时（>{timeout}s）pytest_rc=124 | "
                "refs_rc=1 | config_rc=1 | contract_rc=1")
    except OSError as e:
        return (f"error: selfcheck 启动失败: {e} pytest_rc=1 | refs_rc=1 | "
                "config_rc=1 | contract_rc=1")


def _build_report_argv(chain_args, derive):
    """report 步骤 argv：收据参数由前序真实结果机械派生（derive dict）。"""
    cmd = [sys.executable, str(_SCRIPT_DIR / "ws_report.py"),
           "--batch-file", chain_args["batch_file"],
           "--body", chain_args["batch_file"],
           "--result", derive["result"], "--build", derive["build"],
           "--board", derive["board"], "--summary", derive["summary"]]
    for key, flag in (("push_file", "--push-file"),
                      ("unit_test_file", "--unit-test-file"),
                      ("acc_file", "--acceptance-file"),
                      ("timings_file", "--timings-file")):
        if chain_args.get(key):
            cmd += [flag, chain_args[key]]
    # board 收据强制自检证据（ws_report 方向 4 门禁；rc 全 0 与否由其扫描判定）
    if chain_args.get("selfcheck"):
        cmd += ["--selfcheck", chain_args["selfcheck"]]
    if chain_args.get("case"):
        cmd += ["--case", chain_args["case"]]
    return cmd


def _step_rc(steps, name):
    """已执行步骤的真实 rc；未执行返 None。"""
    for s in steps:
        if s["name"] == name:
            return s["rc"]
    return None


def _derive_report_args(steps, overall):
    """收据参数派生：result/build/board/summary 全部由真实 rc 机械推导。

    - result：overall（pass/fail）
    - build：push 过=产物在位记 pass；push 未跑或失败=skip（AI 可显式覆盖）
    - board：全过=pass；push/unit_test/acceptance 失败=fail（设备已被动过）；
      sync/connect 阶段失败=skip（未触及设备态）
    """
    failed = next((s for s in steps if s.get("canceled")
                   or s["rc"] is None or s["rc"] != 0), None)
    result = "pass" if overall == "pass" else "fail"
    build = "pass" if _step_rc(steps, "push") == 0 else "skip"
    if overall == "pass":
        board = "pass"
    elif failed and failed["name"] in ("push", "unit_test", "acceptance"):
        board = "fail"
    else:
        board = "skip"
    if overall == "pass":
        ran = "→".join(s["name"] for s in steps) or "无步骤"
        summary = f"全链通过（{ran}）"
    else:
        why = "超时取消" if failed and failed.get("canceled") else f"rc={failed['rc'] if failed else '?'}"
        summary = f"链停于 {failed['name'] if failed else '?'}（{why}）"
    return {"result": result, "build": build, "board": board,
            "summary": summary}


# selfcheck 预跑 join 上限（秒）：selfcheck 内部对 pytest/治理工具各有
# 超时兜底（900s/120s），join 再放宽一层防线程悬挂拖死链
_SELFCHECK_JOIN_TIMEOUT_S = 1200


def _start_selfcheck_preflight():
    """锁外预跑 selfcheck（B3）：返回 (thread, result_dict)。

    selfcheck 是纯文件系统检查（pytest harness + 治理扫描），不触碰
    workspace/设备态，与锁内 sync/connect/push 等步骤并行无资源冲突；
    改锁内同步串行（report 前固定 +30s，且拉长 workspace/device 双锁
    互斥窗口）为后台并行，report 步 join 收割。batch_file 缺失（report
    必 skipped）时不启动。daemon=True：LockHeld 提前返回等场景主进程
    退出不挂。
    """
    result = {}

    def _worker():
        result["text"] = _run_selfcheck()

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t, result


def _join_selfcheck_preflight(thread, result):
    """收割预跑结果：join 超限/空结果时同步兜底重跑（保证收据有值）。"""
    if thread is not None:
        thread.join(timeout=_SELFCHECK_JOIN_TIMEOUT_S)
    text = (result.get("text") or "").strip()
    return text if text else _run_selfcheck()


def run_chain(product="rpi5", out=None, result_file=None, batch_file=None,
              case=None, wait_ready=False, log_since=None, build=None,
              timeouts=None, use_locks=True):
    """顺序执行全链，返回 (rc, result_dict)。失败即停，余步记入 skipped。

    batch_file：模式 A 批次文件（acceptance 验收源 + report 收据源）；
    缺 report 源时 report 记 skipped，缺验收源时 acceptance 记 skipped。
    timeouts：步骤名→秒 覆盖表（缺省 _STEP_TIMEOUTS）。
    use_locks：编排互斥锁开关（单测注入 False；生产恒 True）。
    """
    run_id = os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex
    # 子步骤产物共享同 run_id：ws_report PASS 核验按 run_id 判同批
    os.environ["CDP_RUN_ID"] = run_id
    timeout_map = dict(_STEP_TIMEOUTS)
    if timeouts:
        timeout_map.update(timeouts)

    batch_id = None
    if batch_file:
        try:
            batch_id = batch_id_from_text(
                Path(batch_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            batch_id = None  # 命名回落 run_id；批次合法性由 ws_report 把关
    suffix = batch_id or run_id
    chain_args = {
        "batch_file": batch_file, "case": case, "wait_ready": wait_ready,
        "log_since": log_since,
        "push_file": str(_CROSS_DEVICE_LOG / f"push-{suffix}.json"),
        "unit_test_file": str(_CROSS_DEVICE_LOG / f"unit-tests-{suffix}.json"),
        "acc_file": str(_CROSS_DEVICE_LOG / f"acceptance-{suffix}.json"),
    }
    if batch_file:
        timings = _CROSS_DEVICE_LOG / f"timings-{batch_id}.json" if batch_id else None
        chain_args["timings_file"] = str(timings) if timings and timings.is_file() else None

    # 锁外预跑 selfcheck（B3）：与锁等待/前序步骤并行，report 步收割
    selfcheck_thread = selfcheck_result = None
    if batch_file:
        selfcheck_thread, selfcheck_result = _start_selfcheck_preflight()

    try:
        # 锁获取在 with 进入点发生（生成器上下文），LockHeld 统一走 rc 3 处理
        with (ws_lock.verify_locks() if use_locks else nullcontext()):
            return _run_chain_locked(run_id, batch_id, product, out,
                                     result_file, batch_file, build,
                                     timeout_map, chain_args,
                                     selfcheck_thread, selfcheck_result)
    except ws_lock.LockHeld as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3, {"run_id": run_id, "batch_id": batch_id, "overall": "fail",
                   "exit_rc": 3, "canceled": False, "steps": [],
                   "skipped": list(_CHAIN_STEPS), "skip_reasons": {},
                   "error": str(exc)}


def _chain_mark(name, batch_id):
    """verify 链起止自发 mark（verify_start/verify_end，B1：脚本自发替代
    AI 手打——旧 SKILL 手动 mark verify_start/verify_end 实测漂移且不在
    段名常量表）。仅当批次归属明确（batch_id 解析成功）时打点，防把链段
    打到 current-batch.json 回落的无关批次上；失败静默不阻断编排。"""
    if not batch_id:
        return
    try:
        import cdp_timing
        cdp_timing.emit_mark(name, batch_id=batch_id)
    except Exception:
        pass


def _run_chain_locked(run_id, batch_id, product, out, result_file, batch_file,
                      build, timeout_map, chain_args,
                      selfcheck_thread=None, selfcheck_result=None):
    """锁内编排主体：逐步执行 + 运行态落盘（仅编排器写）。

    失败停链语义（A1 修订）：某步失败/取消后，其余验证步记 skipped，
    但 report 步仍执行——fail 收据由前序真实 rc 机械派生落盘（loop done
    --receipt 契约要求失败轮次也有收据记账；report 源缺失时仍 skipped）。
    """
    steps, skipped, skip_reasons = [], [], {}
    overall, canceled_any = "pass", False
    fail_stop = False
    started_at = time.time()
    _chain_mark("verify_start", batch_id)
    for name in _CHAIN_STEPS:
        # 无验收源/无收据源：确定性跳过（记账留痕，不算失败）
        if name == "acceptance" and not (chain_args.get("case") or batch_file):
            skipped.append(name)
            skip_reasons[name] = "缺验收源（--case/--batch-file 均未传）"
            continue
        if name == "report" and not batch_file:
            skipped.append(name)
            skip_reasons[name] = "缺 --batch-file（模式 A 收据需批次源）"
            continue
        if fail_stop and name != "report":
            # 链已停：余下验证步记 skipped；report 豁免（fail 收据落盘）
            skipped.append(name)
            skip_reasons[name] = "链已停（前序步骤失败，收据仍落盘）"
            continue
        if name == "report":
            derive = _derive_report_args(steps, overall)
            if build:  # 显式传参优先（AI 对 build 段的判定不可替代时使用）
                derive["build"] = build
            # board 收据强制自检证据：锁外预跑收割（B3；异常兜底见
            # _run_selfcheck——挂死/启动失败返回带 error 标注文本判红）
            chain_args["selfcheck"] = _join_selfcheck_preflight(
                selfcheck_thread, selfcheck_result)
            argv = _build_report_argv(chain_args, derive)
        else:
            argv = _build_argv(name, product, out, chain_args)
        t0m, t0 = time.monotonic(), time.time()
        rc, canceled = _run_step(argv, timeout_map[name])
        steps.append({"name": name, "rc": rc, "start": t0,
                      "end": time.time(),
                      "dur_s": round(time.monotonic() - t0m, 3),
                      "canceled": canceled})
        canceled_any = canceled_any or canceled
        if canceled or rc is None or rc != 0:
            overall = "fail"
            fail_stop = True  # 不 break：report 步仍执行落 fail 收据（A1）
    _chain_mark("verify_end", batch_id)
    ended_at = time.time()
    exit_rc = 0 if overall == "pass" else 1
    result = {"run_id": run_id, "batch_id": batch_id,
              "started_at": started_at, "ended_at": ended_at,
              "overall": overall, "exit_rc": exit_rc,
              "canceled": canceled_any, "steps": steps,
              "skipped": skipped, "skip_reasons": skip_reasons}
    # 运行态落盘（仅编排器写）：ws_session done --run-file 的 stage/rc 真相源
    _atomic_write_json(_RUNS_DIR / f"{run_id}.json", result)
    if result_file:
        _atomic_write_json(result_file, result)
    return exit_rc, result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="上板验证确定性全链编排（sync→connect→push→unit_test→"
                    "acceptance→report）")
    ap.add_argument("--product", default="rpi5")
    ap.add_argument("--out", default=None, help="AOSP out 目录（透传）")
    ap.add_argument("--result-file", default=None,
                    help="自描述链式产物 JSON（原子写；运行态恒落 runs/ 目录）")
    ap.add_argument("--batch-file", default=None,
                    help="模式 A 批次文件（acceptance 验收源 + report 收据源）")
    ap.add_argument("--case", default=None,
                    help="验收用例标签（透传 ws_acceptance --case）")
    ap.add_argument("--wait-ready", action="store_true",
                    help="push 有 reboot 时透传（ws_acceptance --wait-ready）")
    ap.add_argument("--log-since", default=None,
                    help="logcat 时间窗起点（透传 ws_acceptance --log-since）")
    ap.add_argument("--build", choices=["pass", "fail", "skip"], default=None,
                    help="编译段结果（缺省按 push 真实 rc 派生：push 过=pass）")
    args = ap.parse_args(argv)
    rc, result = run_chain(args.product, args.out, args.result_file,
                           batch_file=args.batch_file, case=args.case,
                           wait_ready=args.wait_ready,
                           log_since=args.log_since, build=args.build)
    print(json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
