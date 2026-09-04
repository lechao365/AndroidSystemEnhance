"""adb 连接子模块（workspace-verify 自包含，不依赖外部 skill）。

连接策略：mDNS 发现（adb mdns services，输出 3 列，endpoint 为最后一列
ip:port；排除 _adb-tls-pairing）→ 静态 fallback（默认 rp5.local:5555，
LC_VERIFY_ADB_HOST/PORT 覆盖）。
在线判定：adb devices 两列制表符，serial 全匹配且 state == "device"，
轮询 3 次隔 2s（重启后 adb 首次常 offline）。
就绪判定：ensure_ready 每 5s 轮询 sys.boot_completed 到 1（timeout=180）。

CLI:
  ws_adb_connect.py ensure [--rescue]          # 连接并输出 endpoint（失败 exit 1；--rescue 显式启用串口救援）
  ws_adb_connect.py ready                      # 轮询 sys.boot_completed 就绪（超时 exit 1）
  ws_adb_connect.py clock [--max-skew 120]     # 设备时钟偏差超阈值则 root 修正（exit 1 失败）
  ws_adb_connect.py devices                    # adb devices 原样输出
  ws_adb_connect.py exec --cmd "<shell>"       # 执行命令，末行输出 exit_code: N
  ws_adb_connect.py logcat [--filter f] [--tail N] [--since <MM-DD HH:MM:SS.mmm>]
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 设备身份期望序列号经 paths.conf 配置位（LC_VERIFY_EXPECT_SERIAL）读取，
# 支持同名环境变量覆盖；env_path 从 harness/lib 导入（与 ws_report 同源）
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "lib"))
from paths import env_path  # noqa: E402

_EXEC_TAG_RE = re.compile(r"__LE_EXIT_CODE__=(\d+)\s*$", re.MULTILINE)

# 可注入睡眠点（方向 5，与 ws_push._sleep 同款）：clock_sync 的 root 重启
# adbd settle 与修正前 settle 两处 2s 实时等待经此下发；单测 patch 本符号
# 消除真实等待——三个时钟同步用例曾各真等 2s，在 slow guard 3s 阈值下
# 逃过守卫（贴近阈值的等待混入自检）
_sleep = time.sleep


def adb_bin():
    return os.environ.get("LC_VERIFY_ADB_BIN", "adb")


def run_adb(args, timeout=60):
    """通用 adb 执行：异常（adb 缺失/超时）统一返回 ("", -1)，不抛。

    供本模块及用例资产（如 cases/lcview_check.py）共用，避免各处自建 adb 层。
    """
    try:
        r = subprocess.run([adb_bin()] + args, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
        return r.stdout, r.returncode
    except (OSError, subprocess.TimeoutExpired):
        return "", -1


def host_port():
    h = os.environ.get("LC_VERIFY_ADB_HOST", "rp5.local")
    p = os.environ.get("LC_VERIFY_ADB_PORT", "5555")
    return f"{h}:{p}"


def build_connect_cmd(endpoint=None):
    return [adb_bin(), "connect", endpoint or host_port()]


def mdns_discover():
    """返回 endpoint 列表（每行最后一列为 ip:port）；mDNS 不可用返回空列表。"""
    try:
        r = subprocess.run([adb_bin(), "mdns", "services"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        if r.returncode != 0:
            return []
        eps = []
        for ln in r.stdout.splitlines():
            if "_adb" in ln and "._tcp" in ln and "_adb-tls-pairing" not in ln:
                parts = ln.split()
                if len(parts) >= 3 and (":" in parts[-1]):
                    eps.append(parts[-1])
        return eps
    except (OSError, subprocess.TimeoutExpired):
        return []


def parse_devices(text):
    """解析 adb devices 输出为 {serial: state}。"""
    out = {}
    for ln in text.splitlines()[1:]:
        if "\t" in ln:
            serial, state = ln.split("\t", 1)
            out[serial.strip()] = state.strip()
    return out


def _state_online(endpoint, devices_stdout):
    return parse_devices(devices_stdout).get(endpoint) == "device"


def _is_online(endpoint, attempts=3, interval=2):
    """轮询 attempts 次、隔 interval 秒判在线：重启后 adb 首次常报 offline，重试提高命中。"""
    for i in range(attempts):
        try:
            r = subprocess.run([adb_bin(), "devices"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=10)
            if _state_online(endpoint, r.stdout):
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        if i + 1 < attempts:
            time.sleep(interval)
    return False


def ensure_ready(timeout=180, poll_interval=5):
    """轮询 sys.boot_completed 直到为 1；超时返回 False（设备未完全就绪）。

    reboot 后 adb 可能已在线但系统未起完，立即验收会误判；本函数保证
    boot 完成后再进入验收（ws_acceptance.py --wait-ready 调用）。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            r = subprocess.run(build_exec_cmd("getprop sys.boot_completed"),
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=10)
            body, code = parse_exec_output(r.stdout)
            if code == 0 and body.strip() == "1":
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
        time.sleep(poll_interval)
    return False


def _verify_identity(endpoint):
    """设备身份校验（方向 5）：LC_VERIFY_EXPECT_SERIAL 设置时核对设备序列号。

    返回 (ok, detail)。身份不符即拒（防连错设备/误连旧机）——设备序列号只认
    基镜像烧录固化值，增量推送不改变；期望未设置时跳过校验（返回 ok）。
    期望来源（方向 1 接线）：同名环境变量优先（测试/临时覆盖），回落
    paths.conf 配置位（LC_VERIFY_EXPECT_SERIAL，支持环境变量覆盖），
    两者皆空即视为未设置跳过校验。
    """
    expect = (os.environ.get("LC_VERIFY_EXPECT_SERIAL")
              or env_path("LC_VERIFY_EXPECT_SERIAL", "")).strip()
    if not expect:
        return True, ""
    out, rc = run_adb(["-s", endpoint, "shell", "getprop ro.serialno"], timeout=15)
    serial = out.strip()
    if rc != 0 or not serial:
        return False, f"无法读取设备序列号（endpoint={endpoint} rc={rc}）"
    if serial != expect:
        return False, (f"设备身份不符：期望 {expect}，实际 {serial}"
                       f"（endpoint={endpoint}）")
    return True, ""


def _adb_devices_online():
    """adb devices 在线预检（方向 2）：返回当前已连接且在线的 endpoint 列表。

    已连接的 adb 设备（state=device）直接可用，免 mDNS 逐候选 connect 的
    重连开销；返回空列表（adb 不可用/无在线设备）时由调用方回落后续路径。
    """
    try:
        r = subprocess.run([adb_bin(), "devices"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ep for ep, st in parse_devices(r.stdout).items() if st == "device"]


def ensure_connected(rescue_enabled=False):
    """在线预检快路径 → mDNS 优先逐个尝试，失败回退静态；皆败后经串口救援
    （第三级通道）。

    返回在线 endpoint 或 None。rescue 会重启设备 adbd（副作用），默认关闭，
    须调用方显式开（ensure --rescue）；触发时必须打印 detail；rescue 返回
    端点后再 connect 复核在线才算成功。
    方向 5：连上后核对设备身份（LC_VERIFY_EXPECT_SERIAL），不符即拒——
    快路径/mDNS 多候选逐拒，静态/救援路径不符直接返 None。
    方向 2：先查 adb devices 已连接在线设备（快路径），命中即过身份校验
    返回，未命中/全拒再走 mDNS/静态/rescue——已连接设备免逐候选 connect
    重连（verify_acceptance_connect 六次累计 961s 占全批 31.4% 的提速点）。
    """
    for ep in _adb_devices_online():
        if not _is_online(ep):
            continue
        ok, detail = _verify_identity(ep)
        if not ok:
            print(f"[identity] {detail}（拒绝该端点，继续尝试）")
            continue
        return ep
    for ep in mdns_discover():
        try:
            subprocess.run(build_connect_cmd(ep), capture_output=True,
                           encoding="utf-8", errors="replace", timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if not _is_online(ep):
            continue
        ok, detail = _verify_identity(ep)
        if not ok:
            print(f"[identity] {detail}（拒绝该端点，继续尝试）")
            continue
        return ep
    ep = host_port()
    try:
        subprocess.run(build_connect_cmd(ep), capture_output=True,
                       encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        ep = None
    if ep and _is_online(ep):
        ok, detail = _verify_identity(ep)
        if not ok:
            print(f"[identity] {detail}（静态端点身份不符拒绝）")
            return None
        return ep
    if not rescue_enabled:
        return None
    ep_rescued, state, detail = rescue()
    print(f"[rescue] {detail}")
    if not ep_rescued:
        print(f"[rescue] state={state}（{RESCUE_STATES.get(state, state)}）")
        return None
    try:
        subprocess.run(build_connect_cmd(ep_rescued), capture_output=True,
                       encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        print(f"[rescue] 连接 {ep_rescued} 失败（adb 超时）")
        return None
    if not _is_online(ep_rescued):
        print(f"[rescue] 连接 {ep_rescued} 后复核不在线（adbd 未就绪）")
        return None
    # 救援通道返回路径同样过身份校验（方向 5）
    ok, detail = _verify_identity(ep_rescued)
    if not ok:
        print(f"[identity] {detail}（救援通道端点身份不符拒绝）")
        return None
    return ep_rescued


def build_exec_cmd(cmd):
    """exec 命令：输出末尾附 __LE_EXIT_CODE__=<n> 以便解析退出码。"""
    return [adb_bin(), "shell", f"{cmd}; echo __LE_EXIT_CODE__=$?"]


def build_logcat_cmd(filter_expr=None, tail=200, since=None, pid=None):
    """since 非空时以 `-t <since>` 收窄时间窗（代 -t <tail>），
    避免命中上轮旧日志致假绿（如 reboot 后验收须从 reboot 时刻起）；
    pid 非空时追加 --pid=<pid> 按进程归属收窄（logfield 5 段写法：日志按
    进程筛，防旧进程心跳残留行被当新进程心跳）。"""
    cmd = [adb_bin(), "logcat", "-d"]
    if filter_expr:
        cmd += ["-s", filter_expr]
    cmd += ["-t", since if since else str(tail)]
    if pid:
        cmd += [f"--pid={pid}"]
    return cmd


def parse_exec_output(text):
    """解析 exec 输出，返回 (stdout_body, exit_code)。"""
    m = _EXEC_TAG_RE.search(text)
    if not m:
        return text, None
    body = text[: m.start()].rstrip()
    return body, int(m.group(1))


def clock_sync(endpoint=None, max_skew=120):
    """设备时钟与本地偏差 > max_skew 秒时 root 后修正（date -u MMDDhhmmCCYY.ss）。

    返回 (ok, detail)：偏差在阈值内只检查不动设备；修正前 adb root（会重启
    adbd）并重连；修正后重读 date +%s 复核偏差落回阈值（仅看退出码不可信）。
    时间戳以 UTC 组串并 -u 下发，避免设备时区解释引入新偏差（PIT-5 复发防护）。
    """
    try:
        r = subprocess.run(build_exec_cmd("date +%s"), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=10)
        body, code = parse_exec_output(r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return False, "无法读取设备时钟（adb 失败或超时）"
    if code != 0 or not body.strip().isdigit():
        return False, f"设备 date 返回异常: {body.strip()!r}"
    skew = int(time.time()) - int(body.strip())
    if abs(skew) <= max_skew:
        return True, f"设备时钟偏差 {skew}s ≤ {max_skew}s，无需修正"
    # 修正：root（重启 adbd）→ 重连 → date -u MMDDhhmmCCYY.ss。
    # 时间戳用 gmtime 组 UTC 串并以 -u 下发：date 按设备时区解释，若取宿主
    # 本地时间（strftime 默认时区）而设备时区不同会引入新的系统偏差
    try:
        subprocess.run([adb_bin(), "root"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False, "adb root 执行失败"
    _sleep(2)
    try:
        subprocess.run(build_connect_cmd(endpoint), capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False, "root 后重连失败"
    stamp = time.strftime("%m%d%H%M%Y.%S", time.gmtime())
    try:
        r = subprocess.run(build_exec_cmd(f"date -u {stamp}"),
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        body2, code2 = parse_exec_output(r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return False, "date 修正执行失败（adb 超时）"
    if code2 != 0:
        return False, f"date 修正失败 exit={code2}: {body2.strip()!r}"
    # 复核：重读设备时钟确认偏差落回阈值内（仅看退出码不可信，date 可能静默失败）
    try:
        r = subprocess.run(build_exec_cmd("date +%s"), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=10)
        body3, code3 = parse_exec_output(r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return False, "修正后复核失败（adb 超时）"
    if code3 != 0 or not body3.strip().isdigit():
        return False, f"修正后设备 date 返回异常: {body3.strip()!r}"
    skew2 = int(time.time()) - int(body3.strip())
    if abs(skew2) > max_skew:
        return False, f"修正后偏差仍 {skew2}s > {max_skew}s（复核未通过）"
    return True, (f"设备时钟偏差 {skew}s > {max_skew}s，已修正为 {stamp}"
                  f"（复核偏差 {skew2}s）")


RESCUE_STATES = {
    "ok": "正常（串口救援成功）",
    "full_brick": "全砖（串口静默无输出，断电/硬件级）",
    "half_brick": "半砖（设备无网或 adbd 未起，串口有输出）",
    "boot_loop": "boot loop（串口反复输出相同启动日志）",
    "rescue_unavailable": "救援通道不可用（串口转发器未起/连不上，非设备故障）",
}


def _detect_boot_loop(conn, seconds=3.0) -> bool:
    """读串口输出 seconds 秒，同一行出现 ≥3 次判 boot loop。"""
    import ws_serial  # noqa: F401（ws_serial 反向依赖本模块，须惰性）
    try:
        text = ws_serial._read_some(conn, seconds)
    except ws_serial.SerialError:
        return False
    from collections import Counter
    counts = Counter(ln.strip() for ln in text.splitlines() if ln.strip())
    return any(cnt >= 3 for cnt in counts.values())


def rescue(serial_host=None, serial_port=None, adb_port="5555"):
    """串口救援（第三级通道）：设置 adb over TCP、重启 adbd、取 wlan0 IPv4。

    返回 (endpoint, state, detail)，state ∈ RESCUE_STATES。经 ws_serial 链路
    执行（惰性 import，串口通道不常用不拖慢主路径）；重启 adbd 有副作用，
    调用方（ensure_connected）必须把 detail 打印出来。
    connect 抛 ENDPOINT_UNREACHABLE 是转发器未起（救援通道不可用），与设备
    断电全砖（SERIAL_SILENT）区分，不得错判 full_brick。
    """
    import ws_serial  # noqa: F401
    # 转发器端点走 ws_serial.serial_endpoint 单一事实源（消 LC_SERIAL_HOST/PORT
    # 默认值重复推导，端口非数字由该函数兜底）
    def_host, def_port = ws_serial.serial_endpoint()
    conn = ws_serial.SerialConn(
        serial_host or def_host,
        serial_port if serial_port is not None else def_port)
    try:
        try:
            conn.connect()
        except ws_serial.SerialError as exc:
            if exc.category == "ENDPOINT_UNREACHABLE":
                return None, "rescue_unavailable", f"救援通道不可用（转发器未起）: {exc}"
            return None, "full_brick", f"串口不可达: {exc}"
        try:
            body, code = ws_serial._execute(
                conn,
                f"setprop service.adb.tcp.port {adb_port}; stop adbd; start adbd",
                10.0)
        except ws_serial.SerialError as exc:
            if exc.category == "SERIAL_SILENT":
                return None, "full_brick", f"串口静默（无任何输出，断电全砖）: {exc}"
            return None, "half_brick", f"串口执行失败: {exc}"
        if str(code) != "0":
            if _detect_boot_loop(conn):
                return None, "boot_loop", "adbd 重启失败且串口反复输出相同启动日志（boot loop）"
            return None, "half_brick", f"adbd 未起 exit={code}: {body.strip()[:120]!r}"
        # adbd 重启后 settle（照 clock_sync），否则立刻取 IP 会假半砖
        _sleep(2)
        # 重启 adbd 后取 wlan0 IPv4（复用 ws_serial 的 IPv4 过滤）
        ip = None
        try:
            body2, code2 = ws_serial._execute(conn, "ip -o -4 addr show wlan0", 10.0)
            if str(code2) == "0":
                for line in body2.splitlines():
                    m = ws_serial._IPV4_RE.search(line)
                    if m and not m.group(1).startswith(ws_serial._BAD_IPS):
                        ip = m.group(1)
                        break
        except ws_serial.SerialError as exc:
            return None, "half_brick", f"取 IPv4 失败: {exc}"
        if not ip:
            if _detect_boot_loop(conn):
                return None, "boot_loop", "取 IPv4 失败且串口反复输出相同启动日志（boot loop）"
            return None, "half_brick", "设备无有效 IPv4（wlan0 未拿到地址，半砖）"
        return f"{ip}:{adb_port}", "ok", (
            f"串口救援成功 {ip}:{adb_port}（service.adb.tcp.port={adb_port} 已设置，"
            f"adbd 已重启——设备 adb 链路被重启，属预期副作用）")
    finally:
        conn.close()  # 各返回路径统一关连接，防 socket 句柄泄漏


def _ensure_failure_detail() -> list:
    """ensure 失败诊断：mDNS 与静态 fallback 两因分开，.local 解析失败单列。

    PIT-1 复发时（WSL2 镜像模式 rp5.local 解析失败）若只报"均失败"无法分辨
    是设备离线还是域名解析问题，须把 .local 域名单列提示改用真实 IP。
    """
    mdns = mdns_discover()
    parts = []
    if mdns:
        parts.append(f"mDNS 发现 {len(mdns)} 个端点但均未在线: {', '.join(mdns)}")
    else:
        parts.append("mDNS 未发现端点")
    # 静态端点走 host_port()（单一事实源，不再重复推导 rp5.local/5555 默认值）
    ep_static = host_port()
    host_part = ep_static.rsplit(":", 1)[0]
    if host_part.endswith(".local"):
        parts.append(f"静态 {ep_static} 不可达——.local 域名解析失败"
                     "（WSL2 镜像模式常见，PIT-1：export LC_VERIFY_ADB_HOST=<真实IP> 覆盖）")
    else:
        parts.append(f"静态 {ep_static} 不可达")
    return parts


def main(argv=None):
    ap = argparse.ArgumentParser(description="adb 连接工具（mDNS→静态 fallback）")
    sub = ap.add_subparsers(dest="action", required=True)
    p_ensure = sub.add_parser("ensure")
    p_ensure.add_argument("--rescue", action="store_true",
                          help="mDNS 与静态皆败后启用串口救援（重启设备 adbd，"
                               "副作用，默认不触发）")
    p_ready = sub.add_parser("ready")
    p_ready.add_argument("--timeout", type=int, default=180,
                         help="就绪等待上限（秒），覆盖默认 180")
    p_clock = sub.add_parser("clock")
    p_clock.add_argument("--max-skew", type=int, default=120,
                         help="设备时钟与本地最大允许偏差（秒），超阈值则 UTC 修正并复核")
    sub.add_parser("devices")
    p_exec = sub.add_parser("exec")
    p_exec.add_argument("--cmd", dest="shell_cmd", required=True)
    p_exec.add_argument("--timeout", type=int, default=60)
    p_log = sub.add_parser("logcat")
    p_log.add_argument("--filter")
    p_log.add_argument("--tail", type=int, default=200)
    p_log.add_argument("--since",
                       help="logcat 时间窗起点（MM-DD HH:MM:SS.mmm），代 -t 行数")
    args = ap.parse_args(argv)

    if args.action == "ensure":
        ep = ensure_connected(rescue_enabled=args.rescue)
        if ep:
            # 方向 5：verify_push 打点已移至 ws_push.py 实际推送循环完成后
            # （此前连接成功即打，量不到推送），本 CLI 只负责连接并输出 endpoint
            print(ep)
        else:
            print(json.dumps(
                {"error": "设备不可达", "detail": _ensure_failure_detail()},
                ensure_ascii=False))
        return 0 if ep else 1
    if args.action == "ready":
        if ensure_ready(timeout=args.timeout):
            print("ready: sys.boot_completed=1")
            return 0
        print("error: 设备未就绪（sys.boot_completed 超时）")
        return 1
    if args.action == "clock":
        ok, detail = clock_sync(max_skew=args.max_skew)
        print(detail)
        return 0 if ok else 1
    if args.action == "devices":
        try:
            r = subprocess.run([adb_bin(), "devices"], capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=10)
            print(r.stdout)
            return 0
        except (OSError, subprocess.TimeoutExpired):
            print("error: adb devices 执行失败（adb 缺失或超时）")
            return 1
    if args.action == "exec":
        try:
            r = subprocess.run(build_exec_cmd(args.shell_cmd),
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=args.timeout)
            body, code = parse_exec_output(r.stdout)
            print(body)
            print(f"exit_code: {code}")
            return 0
        except subprocess.TimeoutExpired:
            print("error: exec 超时")
            return 1
        except OSError:
            print("error: adb exec 执行失败（adb 缺失）")
            return 1
    if args.action == "logcat":
        try:
            r = subprocess.run(build_logcat_cmd(args.filter, args.tail,
                                                since=args.since),
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            print(r.stdout)
            return 0
        except (OSError, subprocess.TimeoutExpired):
            print("error: adb logcat 执行失败（adb 缺失或超时）")
            return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())