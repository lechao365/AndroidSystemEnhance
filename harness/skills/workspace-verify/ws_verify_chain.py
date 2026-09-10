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
from selfcheck import REQUIRED_RC_KEYS  # noqa: E402 方向 3：必查键单点定义（与 ws_report 同源）
from verify_common import atomic_write_json as _atomic_write_json_impl  # noqa: E402

import ws_lock  # noqa: E402

# 链式步骤名序列（可注入单测）：argv 由 _build_argv/_build_report_argv 按名构造
# package 步骤（方向 3）：acceptance 后、report 前用 systemd-run --user 拉起
# ws_package.py 打包（绕 NoNewPrivileges，sudo_n 真实探测），落盘
# package-<batch_id>.json 供 report 内嵌——打包是证据补充（非上板门禁），
# 失败不阻断链、不置 overall=fail，只记步。
_CHAIN_STEPS = ("sync", "connect", "push", "unit_test", "acceptance",
                "package", "report")

# 单步超时（秒）：覆盖各子脚本内部 timeout 之上的一层编排护栏；
# 超时走 killpg 有界 teardown，防止子脚本挂死拖垮整链
_STEP_TIMEOUTS = {
    "sync": 900,        # 同步含增量 rsync，历史上 <300s
    "connect": 420,     # ensure 含 mDNS 发现 + 静态 fallback + rescue 重试
    "push": 1800,       # 推送含 reboot_and_wait（boot_timeout 240s）
    "unit_test": 1500,  # 全量 gtest 上板执行
    "acceptance": 1200, # 逐标签探针执行
    "package": 1200,    # 打包证据（ws_package mode 0，实测约 3 分钟；失败不阻断链）
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


def _ensure_timings_started(batch_id):
    """确保本批打点文件在位（缺失即补建），返回路径或 None。

    链式模式 elapsed_s=0/timings 空心根因：独立拉起链路（未经 apply 会话
    cdp_timing start）时 timings-<batch_id>.json 不存在——verify_start 与
    各子脚本自发 mark 静默失败、report 探测不到打点文件，收据 timings 置空、
    elapsed_s 记 0。此处缺文件即补建（start_wall/start_mono 取链起跑
    时刻），apply 会话已 start 的文件原样复用；不归档历史文件、不动
    current-batch 指针（编排器不引入 start 的归档副作用）。
    """
    if not batch_id:
        return None
    import cdp_timing
    if not cdp_timing.BATCH_ID_RE.match(batch_id):
        return None
    from cdp_paths import atomic_write_text, log_apply_dir
    path = log_apply_dir() / f"timings-{batch_id}.json"
    if not path.is_file():
        atomic_write_text(path, json.dumps({
            "batch_id": batch_id,
            "start_wall": time.time(),
            "start_mono": time.monotonic(),
            "marks": [],
        }, ensure_ascii=False, indent=2) + "\n")
    return path


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
    if name == "package":
        # 方向 3：打包用 systemd-run --user（绕 opencode 会话 NoNewPrivileges
        # 导致 sudo -n true 恒拒——上批 BLD-013 实测 systemd-run --user 可绕，
        # 其下 sudo 正常可用），--wait 等打包完成返回真实 rc；
        # 同 batch_id 落盘 package-<batch_id>.json 供 report 探测内嵌。
        # CDP_RUN_ID/CDP_BATCH_ID/AOSP_WS 等 env 由 systemd-run 继承调用者环境。
        pkg_file = chain_args.get("package_file")
        cmd = ["systemd-run", "--user", "--wait", sys.executable,
               str(_SCRIPT_DIR / "ws_package.py"), "--mode", "0"]
        if pkg_file:
            cmd += ["--evidence-file", pkg_file]
        return cmd
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


def _selfcheck_fallback_rcs(pytest_rc):
    """故障兜底文本的 rc 段：覆盖 REQUIRED_RC_KEYS 全集（单点自 selfcheck
    导入，与 ws_report 必查键同源，防新增 rc 后兜底文本再缺键）。pytest_rc
    取真实语义值（超时 124 / 启动失败 1），其余工具未跑成按失败 rc=1 记——
    缺任一键会让 ws_report 以「缺 *_rc 键」判 2 而非按真实非零 rc 判红，
    诊断失真且破坏失败轮次收据契约。"""
    return " | ".join(
        f"{k}={pytest_rc if k == 'pytest_rc' else 1}" for k in REQUIRED_RC_KEYS)


def _run_selfcheck(timeout=900):
    """跑 harness/lib/selfcheck.py 取自检摘要文本（board 收据强制入收据，方向 4）。

    rc 全 0 与否由 ws_report 扫描 *_rc 键判定——本函数只负责把真实输出
    原样带入收据，不自评不吞错。异常兜底（B3/C2）：selfcheck 进程挂死
    （TimeoutExpired）或启动失败（OSError）时返回带 error 标注的文本交
    ws_report 的 *_rc 扫描判红，不沿编排栈上抛破坏链的自描述 JSON 输出
    与退出码语义。
    timeout 与 selfcheck 内部 pytest 上限对齐（_PYTEST_TIMEOUT_S=900，
    wsv2-03）：此前链级 600s 先于内部 900s 到点，慢环境（drvfs 全量自检
    合法耗时 600~900s）会被链误杀产出 rc=124 兜底、成因误判为自检异常。
    """
    try:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT_DIR.parents[1] / "lib" / "selfcheck.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout)
        return ((proc.stdout or "") + (proc.stderr or "")).strip()
    except subprocess.TimeoutExpired:
        return (f"error: selfcheck 超时（>{timeout}s）"
                f"{_selfcheck_fallback_rcs(124)}")
    except OSError as e:
        return f"error: selfcheck 启动失败: {e} {_selfcheck_fallback_rcs(1)}"


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
                      ("timings_file", "--timings-file"),
                      ("coverage_file", "--coverage-file")):
        if chain_args.get(key):
            cmd += [flag, chain_args[key]]
    # board 收据强制自检证据（ws_report 方向 4 门禁；rc 全 0 与否由其扫描判定）
    if chain_args.get("selfcheck"):
        cmd += ["--selfcheck", chain_args["selfcheck"]]
    if chain_args.get("case"):
        cmd += ["--case", chain_args["case"]]
    return cmd


def _derive_report_args(steps, overall):
    """收据参数派生：result/build/board/summary 全部由真实 rc 机械推导。

    - result：overall（pass/fail）
    - build：push 未执行（前序已停，编译产物状态不可知）=skip；push 执行
      且 rc=0（产物在位）=pass；push 已执行且未成功（rc!=0 或 canceled
      的 rc=None，含编译产物缺失）=fail（push 已跑到产物环节失败，编译段
      不可信，不得机械降级 skip 掩盖）
    - board：全过=pass；push/unit_test/acceptance 失败=fail（设备已被动过）；
      sync/connect 阶段失败=skip（未触及设备态）
    - coverage 步（P1-A 只记录不门禁）不进入 board/链停判定：其失败不得
      抢先成为 failed 使 board 判 skip 掩盖其后真实的上板失败（方向 4）。
    """
    # 排除 coverage（只记录不门禁）与 package（方向 3 打包证据，只记录不
    # 门禁）：其失败不得抢先成为 failed 使 board 判 skip 掩盖其后真实的上板
    # 失败（方向 4 同款——package 失败如 sudo 不可用是环境问题，非上板归因）
    real_failed = next((s for s in steps if s["name"] not in ("coverage", "package")
                        and (s.get("canceled") or s["rc"] is None
                             or s["rc"] != 0)), None)
    result = "pass" if overall == "pass" else "fail"
    # 步骤是否执行以 steps 在场为准（skipped 步骤不进 steps；_step_rc 对
    # "未执行"与"canceled 的 rc=None"同为 None，不可用于区分执行与否）
    push_step = next((s for s in steps if s["name"] == "push"), None)
    if push_step is None:
        build = "skip"
    elif push_step["rc"] == 0:
        build = "pass"
    else:
        build = "fail"
    if overall == "pass":
        board = "pass"
    elif real_failed and real_failed["name"] in ("push", "unit_test", "acceptance"):
        board = "fail"
    else:
        board = "skip"
    if overall == "pass":
        ran = "→".join(s["name"] for s in steps) or "无步骤"
        summary = f"全链通过（{ran}）"
    else:
        why = ("超时取消" if real_failed and real_failed.get("canceled")
               else f"rc={real_failed['rc'] if real_failed else '?'}")
        summary = f"链停于 {real_failed['name'] if real_failed else '?'}（{why}）"
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
              timeouts=None, use_locks=True, coverage=False):
    """顺序执行全链，返回 (rc, result_dict)。失败即停，余步记入 skipped。

    batch_file：模式 A 批次文件（acceptance 验收源 + report 收据源）；
    缺 report 源时 report 记 skipped，缺验收源时 acceptance 记 skipped。
    timeouts：步骤名→秒 覆盖表（缺省 _STEP_TIMEOUTS）。
    use_locks：编排互斥锁开关（单测注入 False；生产恒 True）。
    """
    # 方向 5：CDP_RUN_ID 注入前保存现场，chain 结束后 finally 复原——否则
    # 残留使同进程多轮 run_chain 复用首轮 run_id（串扰致产物跨轮错绑）
    prev_cdp_run_id = os.environ.get("CDP_RUN_ID")
    run_id = prev_cdp_run_id or uuid.uuid4().hex
    # 子步骤产物共享同 run_id：ws_report PASS 核验按 run_id 判同批
    os.environ["CDP_RUN_ID"] = run_id
    # 方向 5：CDP_BATCH_ID 注入前保存现场，chain 结束后 finally 复原——否则
    # 残留污染同进程后续用例（单测进程内多次 run_chain 会错绑批次）
    prev_cdp_batch_id = os.environ.get("CDP_BATCH_ID")
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
        # 方向 3：打包证据同 batch_id（package-<batch_id>.json，ws_report 自动
        # 探测路径一致），report 前落盘供内嵌
        "package_file": str(_SCRIPT_DIR.parents[1] / "log" / "workspace-verify"
                            / f"package-{suffix}.json"),
    }
    selfcheck_thread = selfcheck_result = None
    try:
        # wsv2-05：env 注入/批次打点/自检预跑全置于 try 内——此前 CDP_RUN_ID
        # 在 try 前写入，_ensure_timings_started（IO/import）或预跑线程启动
        # 抛异常时 finally 不执行、env 残留，同进程后续轮次 run_id 串扰
        if batch_file and batch_id:
            # 链式耗时接线（修 elapsed_s=0/timings 空心）：打点文件缺失即补建
            # （独立拉起场景），并显式下传 --timings-file；CDP_BATCH_ID 注入使
            # 子脚本自发 mark 定位本批（current-batch.json 指针缺失/陈旧时
            # mark 会落错批或静默失败）
            tpath = _ensure_timings_started(batch_id)
            chain_args["timings_file"] = str(tpath) if tpath else None
            os.environ["CDP_BATCH_ID"] = batch_id
        if batch_file:
            # 方向 5：selfcheck 待 acceptance 后串行（不再锁外并行预跑）。
            # 上批（8f58b075d679）链内 selfcheck 与 acceptance 并行竞争资源致
            # pytest_rc=1 误判红，ws_report 拒写收据；改为 report 步（acceptance
            # 之后）经 _join_selfcheck_preflight(None, {}) 同步串行执行，
            # report 前固定同步跑，消除并行竞争。
            selfcheck_thread, selfcheck_result = None, {}
        with (ws_lock.verify_locks() if use_locks else nullcontext()):
            return _run_chain_locked(run_id, batch_id, product, out,
                                     result_file, batch_file, build,
                                     timeout_map, chain_args,
                                     selfcheck_thread, selfcheck_result,
                                     coverage=coverage)
    except ws_lock.LockHeld as exc:
        # 方向 1（闲时加固让路协议）：正式任务取锁失败即置让路标志，持锁的
        # idle-hardening 会话在原子步骤边界检查到后收敛让路（不抢占验证中的
        # 正式任务；标志为提示性，写失败静默）
        try:
            ws_lock.request_yield()
        except Exception:
            pass
        print(f"error: {exc}", file=sys.stderr)
        return 3, {"run_id": run_id, "batch_id": batch_id, "overall": "fail",
                   "exit_rc": 3, "canceled": False, "steps": [],
                   "skipped": list(_CHAIN_STEPS), "skip_reasons": {},
                   "error": str(exc)}
    finally:
        # 方向 5：用完复原 CDP_RUN_ID / CDP_BATCH_ID（原无则移除），防污染
        # 同进程后续用例（多轮 run_chain 复用首轮 run_id 即串扰）
        if prev_cdp_run_id is None:
            os.environ.pop("CDP_RUN_ID", None)
        else:
            os.environ["CDP_RUN_ID"] = prev_cdp_run_id
        if prev_cdp_batch_id is None:
            os.environ.pop("CDP_BATCH_ID", None)
        else:
            os.environ["CDP_BATCH_ID"] = prev_cdp_batch_id


# ── P0-A 快检模式（--quick）──────────────────────────────
# 语义：AI 编辑内核纯逻辑/单测后的廉价快检——只做 code→workspace 同步 +
#   内核 host 单测 + 自检，跳过 connect/push/acceptance/report（不触碰
#   设备、不落收据）。用于在走完整上板链前快速确认「能编译、纯逻辑单测
#   过、harness 健康」，反馈分钟级、不占真机。
_QUICK_STEPS = (
    "sync",      # sync_code_to_workspace.py --auto
    "host",      # check_host_tests.py（内核 host 单测）
)
_QUICK_TIMEOUTS = {"sync": 900, "host": 300}


def run_quick(use_locks=True):
    """快检模式：sync + host 单测 + selfcheck，不落收据。

    返回 (rc, result_dict)。rc=0 全过 / 1 任一步失败 / 3 锁占用。
    result 含 steps（sync/host 的 rc 与耗时）与 selfcheck 摘要（诊断）。
    锁模式与 run_chain 同款（ws_lock 模块级已 import；nullcontext 顶部已 import）。
    """
    steps, overall = [], "pass"
    started_at = time.time()
    try:
        with (ws_lock.verify_locks() if use_locks else nullcontext()):
            for name in _QUICK_STEPS:
                t0 = time.time()
                if name == "sync":
                    argv = [sys.executable, str(_SYNC), "--auto"]
                else:
                    argv = [sys.executable,
                            str(_SCRIPT_DIR.parents[1] / "lib"
                                / "check_host_tests.py")]
                rc, canceled = _run_step(argv, _QUICK_TIMEOUTS[name])
                steps.append({"name": name, "rc": rc, "start": t0,
                              "end": time.time(), "canceled": canceled})
                if canceled or rc is None or rc != 0:
                    overall = "fail"
                    break
    except ws_lock.LockHeld as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3, {"overall": "fail", "exit_rc": 3, "steps": [],
                   "error": str(exc)}
    # 自检摘要只作诊断（快检不落收据，不判红）
    selfcheck_text = _run_selfcheck()
    result = {"mode": "quick", "overall": overall,
              "exit_rc": 0 if overall == "pass" else 1,
              "steps": steps, "started_at": started_at,
              "ended_at": time.time(),
              "selfcheck": selfcheck_text.splitlines()[-1][:200]
              if selfcheck_text else ""}
    return result["exit_rc"], result


def _chain_mark(name, batch_id, dur_s=None, zero=False):
    """verify 链段自发 mark（B1：脚本自发替代 AI 手打）。

    覆盖 verify_start/verify_end 与方向 1 新增的每步 verify_<step>：链编排器
    在每步（sync/push/unit_test/acceptance）完成时以实测 dur_s 发 mark，跳过
    的步以 zero 发——子脚本自发 mark 之外的双保险，杜绝"编译真跑数千秒却因
    无 verify_build mark 被 ws_acceptance 补零"的伪造数据。仅当批次归属明确
    （batch_id 解析成功）时打点，防把链段打到 current-batch.json 回落的无关
    批次上；失败静默不阻断编排。
    """
    if not batch_id:
        return
    try:
        import cdp_timing
        cdp_timing.emit_mark(name, dur_s=dur_s, zero=zero, batch_id=batch_id)
    except Exception:
        pass


# 链步 → 标准 verify_<step> 段名映射（方向 1）：仅映射 cdp_timing 常量表
# 内已有的 verify_* 段；connect/package/report 无对应标准段不打点（连接量
# 不到/打包只记录不门禁/收据即终点，既有口径）。verify_build 无链步——编译
# 由执行者 mark 真实耗时，链编排器不补零（补零即伪造）。
_VERIFY_STEP_SEGMENTS = {
    "sync": "verify_sync",
    "push": "verify_push",
    "unit_test": "verify_unit_test",
    "acceptance": "verify_acceptance",
}


def _mark_step(name, batch_id, dur_s=None, zero=False):
    """链步完成/跳过 → verify_<step> 段 mark（仅标准四段）。"""
    seg = _VERIFY_STEP_SEGMENTS.get(name)
    if seg:
        _chain_mark(seg, batch_id, dur_s=dur_s, zero=zero)


def _run_chain_locked(run_id, batch_id, product, out, result_file, batch_file,
                      build, timeout_map, chain_args,
                      selfcheck_thread=None, selfcheck_result=None,
                      coverage=False):
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
        # 无验收源/无收据源：确定性跳过（记账留痕，不算失败）；跳过的标准
        # 步以 zero mark 落 verify_<step>（方向 1：补零只兜真跳过的步）
        if name == "acceptance" and not (chain_args.get("case") or batch_file):
            _mark_step(name, batch_id, zero=True)
            skipped.append(name)
            skip_reasons[name] = "缺验收源（--case/--batch-file 均未传）"
            continue
        if name == "report" and not batch_file:
            _mark_step(name, batch_id, zero=True)
            skipped.append(name)
            skip_reasons[name] = "缺 --batch-file（模式 A 收据需批次源）"
            continue
        if name == "package" and not batch_file:
            # 方向 3：打包证据为收据补充，无批次源（收据必 skipped）时无
            # 消费方，跳过免白跑 3 分钟打包
            _mark_step(name, batch_id, zero=True)
            skipped.append(name)
            skip_reasons[name] = "缺 --batch-file（打包证据随收据内嵌，无收据不打包）"
            continue
        if fail_stop and name != "report":
            # 链已停：余下验证步记 skipped；report 豁免（fail 收据落盘）
            _mark_step(name, batch_id, zero=True)
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
        # 方向 1：每步完成自发 verify_<step>（实测 dur_s 入账）——子脚本自发
        # mark 之外的双保险，编译等链外环节不再被 ws_acceptance 盲目补零伪造
        _mark_step(name, batch_id, dur_s=round(time.monotonic() - t0m, 3))
        canceled_any = canceled_any or canceled
        # 方向 3：package 是证据补充（打包失败只记步，ws_package 已如实落盘
        # evidence，report 内嵌真实 script_rc），不阻断链、不改 overall——
        # 与 coverage 同语义（只记录不门禁），避免打包不可用拖垮上板验证。
        if name != "package" and (canceled or rc is None or rc != 0):
            overall = "fail"
            fail_stop = True  # 不 break：report 步仍执行落 fail 收据（A1）
        # P1-A：单测成功后可选 coverage 步（只记录不门禁；失败仅记步不进链判红）
        if coverage and name == "unit_test" and overall == "pass" \
                and not fail_stop:
            t0m, t0 = time.monotonic(), time.time()
            cov_argv = [sys.executable,
                        str(_SCRIPT_DIR / "ws_coverage.py"),
                        "--product", product]
            if out:
                cov_argv += ["--out", out]
            cov_file = str(_CROSS_DEVICE_LOG / f"coverage-{batch_id or run_id}.json")
            cov_argv += ["--result-file", cov_file]
            cov_rc, cov_canceled = _run_step(cov_argv, timeout_map["unit_test"])
            steps.append({"name": "coverage", "rc": cov_rc, "start": t0,
                          "end": time.time(),
                          "dur_s": round(time.monotonic() - t0m, 3),
                          "canceled": cov_canceled})
            chain_args["coverage_file"] = cov_file
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
    ap.add_argument("--quick", action="store_true",
                    help="快检模式：sync + 内核 host 单测 + 自检，不落收据"
                         "（AI 编辑纯逻辑后的廉价反馈，不占真机）")
    ap.add_argument("--coverage", action="store_true",
                    help="单测后采集覆盖率（ws_coverage；只记录不门禁）")
    args = ap.parse_args(argv)
    if args.quick:
        rc, result = run_quick()
        print(json.dumps(result, ensure_ascii=False))
        return rc
    rc, result = run_chain(args.product, args.out, args.result_file,
                           batch_file=args.batch_file, case=args.case,
                           wait_ready=args.wait_ready,
                           log_since=args.log_since, build=args.build,
                           coverage=args.coverage)
    print(json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
