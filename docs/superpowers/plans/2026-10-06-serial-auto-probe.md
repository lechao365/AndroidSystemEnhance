# connection-serial 自适应 COM 探测 + 自动启动转发器 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 lc-skills-connection-serial 具备 COM 口自适应探测（serial_bridge --probe）与 WSL2 侧端点不可达时自动经 cmd.exe 拉起转发器（ensure_bridge）能力。

**Architecture:** 分两层改动——Windows 侧 `serial_bridge.py` 增加探测模式（枚举 COM 发 `echo __PROBE__` 识别 shell 提示符）；WSL2 侧 `lc_conn_serial_core.py` 新增 `ensure_bridge()`（幂等探测→cmd.exe 后台拉起→轮询就绪），`lc_skills_conn_serial.py` 各子命令 connect 前自动接入并新增 `bridge-status`/`bridge-restart` 子命令与 `--no-auto-start` 开关。文档同步更新。

**Tech Stack:** Python 3.10/3.12、pyserial（Windows 侧）、socket、fcntl（WSL2 侧）、cmd.exe（Windows 命令通道）。

**改动目录（发布态）:** `~/.config/opencode/skills/lc-skills-connection-serial/`
**注意:** skill 发布态自包含（不依赖外部源码仓库），直接改发布态。

---

### Task 1: serial_bridge.py 增加 --probe 探测模式

**Files:**
- Modify: `~/.config/opencode/skills/lc-skills-connection-serial/serial_bridge.py`

- [ ] **Step 1: 在文件顶部导入 pyserial 后新增探测辅助函数**

在 `import threading` 之后新增：

```python
import serial.tools.list_ports
```

- [ ] **Step 2: 新增探测函数**

在 `_client_handler` 之后、`main` 之前新增：

```python
def _probe_console(baudrate: int, timeout: float = 1.0) -> str | None:
    """枚举 Windows COM 口，发探测命令识别目标 console（shell 提示符/回显）。

    对每个候选 COM 打开串口，发 `echo __PROBE__`，读响应；响应含
    `__PROBE__` 回显 或 `console:/`/`#`/`$` 提示符特征即判定为目标。
    返回选中的 COM 口；无目标返回 None。
    """
    candidates = list(serial.tools.list_ports.comports())
    if not candidates:
        _log("probe: 无可用串口")
        return None
    for cand in candidates:
        port = cand.device
        try:
            ser = serial.Serial(port, baudrate, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            _log(f"probe: {port} 打开失败: {exc}")
            continue
        try:
            ser.reset_input_buffer()
            ser.write(b"echo __PROBE__\r")
            import time as _t
            _t.sleep(0.6)
            data = ser.read(4096)
        except Exception as exc:  # noqa: BLE001
            _log(f"probe: {port} 通信异常: {exc}")
            ser.close()
            continue
        ser.close()
        if b"__PROBE__" in data or b"console:/" in data or b" #" in data or data.rstrip().endswith(b"$"):
            _log(f"probe: 选中 {port} ({cand.description})")
            return port
        _log(f"probe: {port} 无目标特征（{len(data)}B）")
    return None
```

- [ ] **Step 3: main() 增加 --probe 参数并接入端口选择**

修改 `main` 的参数定义与串口打开段：

```python
    parser.add_argument("--port", default=None,
                        help="串口名（缺省自动探测：--probe 枚举 COM 识别目标 console）")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--probe", action="store_true",
                        help="自动探测 COM 口（缺省 --port 时默认开启）")
    parser.add_argument("--listen", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=9700)
    args = parser.parse_args(argv)
```

替换端口选择逻辑：

```python
    port = args.port
    if port is None:
        if not args.probe:
            _log("未指定 --port，自动进入探测模式（--probe）")
        port = _probe_console(args.baudrate)
        if port is None:
            _log("probe: 未找到目标 console，请用 --port COMx 显式指定")
            return 1
```

并将 `serial.Serial(args.port, ...)` 改为 `serial.Serial(port, ...)`、日志 `f"串口 {args.port}..."` 改为 `f"串口 {port}..."`。

- [ ] **Step 4: 语法与探测冒烟验证**

```bash
python3 -m py_compile ~/.config/opencode/skills/lc-skills-connection-serial/serial_bridge.py
```
Expected: 无输出，exit 0

Windows 侧实机探测（COM 口当前有 COM4=Pi5 console、COM9 静默）：
```bash
/mnt/c/Windows/System32/cmd.exe /c "python D:\\Code\\Github\\AndroidSystemEnhance\\harness\\log\\probe_test.py" 2>&1
```
（该测试脚本临时用 `--probe` 跑探测逻辑，预期选中 COM4；仅作冒烟，探测函数逻辑与上面相同）

- [ ] **Step 5: 归档（本任务改动在发布态，非 git 仓库，无需 commit）**

验证完成即视为归档；任务自检：`py_compile` 通过 + 探测能选中 COM4。

---

### Task 2: lc_conn_serial_core.py 新增 ensure_bridge()

**Files:**
- Modify: `~/.config/opencode/skills/lc-skills-connection-serial/lc_conn_serial_core.py`

- [ ] **Step 1: 新增辅助函数与 ensure_bridge**

在文件末尾 `_execute` 之后新增：

```python
def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    """快速 TCP 探测：端点可达返回 True。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _find_windows_python() -> str | None:
    """探测 Windows 侧 python 路径（where python 首个结果）。"""
    try:
        r = subprocess.run(
            ["/mnt/c/Windows/System32/cmd.exe", "/c", "where python"],
            capture_output=True, text=True, timeout=10,
        )
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.lower().endswith("python.exe"):
                return line
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _skill_dir_ws_path() -> str | None:
    """把本 skill 目录映射为 Windows 可访问路径（\\\\wsl$\\<distro>\\<abspath>）。"""
    import distro  # 不可用则回落
    self_path = Path(__file__).resolve()
    try:
        r = subprocess.run(
            ["/mnt/c/Windows/System32/cmd.exe", "/c", "echo %WSL_DISTRO_NAME%"],
            capture_output=True, text=True, timeout=10,
        )
        distro_name = r.stdout.strip() or "Ubuntu"
    except (OSError, subprocess.TimeoutExpired):
        distro_name = "Ubuntu"
    ws = f"\\\\wsl$\\{distro_name}\\{str(self_path).replace('/', '\\')}"
    return ws


def ensure_bridge(host: str, port: int, timeout: int = 15,
                  env: dict[str, str] | None = None) -> None:
    """确保 TCP 转发端点可达；不可达时自动经 cmd.exe 拉起 Windows 侧转发器。

    幂等：端点已可达直接返回。启动后轮询就绪（默认 15s）。最终失败抛
    SerialError("ENDPOINT_UNREACHABLE", ...)。
    路径定位：LC_SERIAL_WIN_PY（Windows python）> 自动探测；LC_SERIAL_BRIDGE_WS
    （bridge 脚本 Windows 可访问路径）> 按 skill 目录映射 \\\\wsl$。
    """
    env = env or os.environ
    if _tcp_reachable(host, port):
        return

    cmd_exe = "/mnt/c/Windows/System32/cmd.exe"
    if not Path(cmd_exe).exists():
        raise SerialError("ENDPOINT_UNREACHABLE",
                          "TCP 端点不可达且无 Windows 命令通道，无法自动启动转发器")

    win_py = env.get("LC_SERIAL_WIN_PY") or _find_windows_python()
    if not win_py:
        raise SerialError("ENDPOINT_UNREACHABLE",
                          "自动启动失败：未找到 Windows python（设置 LC_SERIAL_WIN_PY）")

    bridge_ws = env.get("LC_SERIAL_BRIDGE_WS") or _skill_dir_ws_path()
    # bridge 脚本与 core 同目录（发布态并入）
    bridge_script = str(Path(__file__).resolve().parent / "serial_bridge.py")
    ws_bridge = bridge_ws if not bridge_ws.endswith("serial_bridge.py") \
        else bridge_ws
    if ws_bridge.endswith("serial_bridge.py") is False and "serial_bridge.py" not in ws_bridge:
        ws_bridge = str(Path(ws_bridge) / "serial_bridge.py")

    start_cmd = (f'{cmd_exe} /c start /b "" "{win_py}" "{ws_bridge}" '
                 f"--probe --listen {host} --listen-port {port}")
    try:
        subprocess.Popen(start_cmd, shell=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise SerialError("ENDPOINT_UNREACHABLE",
                          f"自动启动转发器失败: {exc}") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _tcp_reachable(host, port):
            return
        time.sleep(0.5)
    raise SerialError("ENDPOINT_UNREACHABLE",
                      "自动启动转发器超时（15s 内端口未就绪，可能无目标 COM）")
```

- [ ] **Step 2: 补充顶部 import**

文件头部 `from __future__ import annotations` 后、`import fcntl` 前新增：

```python
import subprocess
```

（`socket`、`os`、`time`、`Path` 已导入；`_env` 已存在）

- [ ] **Step 3: 语法验证**

```bash
python3 -m py_compile ~/.config/opencode/skills/lc-skills-connection-serial/lc_conn_serial_core.py
```
Expected: 无输出，exit 0

- [ ] **Step 4: 行为验证（幂等）**

```bash
cd ~/.config/opencode/skills/lc-skills-connection-serial && python3 -c "
from lc_conn_serial_core import _tcp_reachable, ensure_bridge
print('reachable 9700:', _tcp_reachable('127.0.0.1', 9700))
ensure_bridge('127.0.0.1', 9700)  # 已在跑，应直接返回
print('ensure_bridge(幂等) OK')
"
```
Expected: `reachable 9700: True` + `ensure_bridge(幂等) OK`

---

### Task 3: lc_skills_conn_serial.py 自动接入 + bridge-status/restart + --no-auto-start

**Files:**
- Modify: `~/.config/opencode/skills/lc-skills-connection-serial/lc_skills_conn_serial.py`

- [ ] **Step 1: 导入 ensure_bridge**

扩展 core 导入：

```python
from lc_conn_serial_core import (  # noqa: E402 F401
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    SerialConn,
    SerialError,
    _env,
    _execute,
    _write_lock,
    ensure_bridge,
)
```

- [ ] **Step 2: 各子命令 connect 前接入 ensure_bridge**

对 `cmd_status` / `cmd_exec` / `cmd_monitor` / `cmd_read` / `cmd_ip` / `cmd_ping`，在每个 `conn.connect()` 之前加：

```python
    if not getattr(args, "no_auto_start", False):
        ensure_bridge(args.host, args.port, timeout=args.timeout)
```

注意 `cmd_exec`/`cmd_ip`/`cmd_ping` 要放在 `with _write_lock(lock):` 块内 connect 之前（ensure 也在锁内，避免并发起多个转发器）。

以 `cmd_status` 为例（其余类似）：

```python
def cmd_status(args: argparse.Namespace) -> int:
    if not getattr(args, "no_auto_start", False):
        ensure_bridge(args.host, args.port, timeout=args.timeout)
    conn = SerialConn(args.host, args.port, args.timeout)
    try:
        conn.connect()
        ...
```

`cmd_exec` 内（锁内）：
```python
    with _write_lock(lock):
        if not getattr(args, "no_auto_start", False):
            ensure_bridge(args.host, args.port, timeout=args.timeout)
        conn.connect()
        rc, body = _execute(conn, args.command, args.timeout)
```

- [ ] **Step 3: 新增 bridge-status / bridge-restart 子命令**

在 `cmd_ping` 之后新增：

```python
def cmd_bridge_status(args: argparse.Namespace) -> int:
    """查询 TCP 转发端点可达性（只读，不触发自动启动）。"""
    conn = SerialConn(args.host, args.port, args.timeout)
    try:
        conn.connect()
        log_result("bridge-status", endpoint=f"{args.host}:{args.port}", state="REACHABLE")
        return 0
    except SerialError as exc:
        log_result("bridge-status", endpoint=f"{args.host}:{args.port}", state="UNREACHABLE")
        log_error(f"{exc.category}: {exc}")
        return 1
    finally:
        conn.close()


def cmd_bridge_restart(args: argparse.Namespace) -> int:
    """重启转发器（用于 COM 漂移后人工刷新）：自动启动逻辑 + 等待就绪。"""
    try:
        ensure_bridge(args.host, args.port, timeout=args.timeout)
        log_result("bridge-restart", endpoint=f"{args.host}:{args.port}", state="READY")
        return 0
    except SerialError as exc:
        log_error(f"{exc.category}: {exc}")
        return 1
```

注：`ensure_bridge` 幂等设计下重启需要强制——为满足"重启"语义，`bridge-restart` 先探测端点：若可达则不动作（提示已在跑），若不可达则走 ensure_bridge 拉起。此处实现为"确保就绪"（幂等拉起），强制重启属后续增强，当前文档按此实现标注。

- [ ] **Step 4: main() 注册新子命令与全局 --no-auto-start**

```python
    parser.add_argument("--no-auto-start", action="store_true",
                        help="端点不可达时不自动启动转发器（仅报错）")
```

在 `p_ping` 之后新增：

```python
    p_bridge_status = sub.add_parser("bridge-status", help="查询转发端点可达性（不触发自动启动）")
    p_bridge_status.set_defaults(func=cmd_bridge_status)

    p_bridge_restart = sub.add_parser("bridge-restart", help="确保转发器就绪（不可达时自动拉起）")
    p_bridge_restart.set_defaults(func=cmd_bridge_restart)
```

- [ ] **Step 5: 语法验证**

```bash
python3 -m py_compile ~/.config/opencode/skills/lc-skills-connection-serial/lc_skills_conn_serial.py
```
Expected: 无输出，exit 0

- [ ] **Step 6: 行为验证（自动启动端到端）**

```bash
# 先在 Windows 侧确认转发器未在 9701 端口跑
cd ~/.config/opencode/skills/lc-skills-connection-serial && \
  python3 lc_skills_conn_serial.py --port 9701 --timeout 10 bridge-status; echo "status rc=$? (预期 1 不可达)"
# 自动启动到 9701（用 --host 127.0.0.1 --port 9701）
python3 lc_skills_conn_serial.py --port 9701 --timeout 15 ping; echo "ping rc=$?"
```
Expected: 第一次 UNREACHABLE，第二次自动拉起后 ping 得 pong

---

### Task 4: SKILL.md + docs 更新

**Files:**
- Modify: `~/.config/opencode/skills/lc-skills-connection-serial/SKILL.md`
- Modify: `~/.config/opencode/skills/lc-skills-connection-serial/docs/connection-deploy.md`

- [ ] **Step 1: SKILL.md Preconditions 更新**

将 Preconditions 改为：

```
- Windows 侧已安装 pyserial（`pip install pyserial`）；转发器可自动启动（WSL2 侧经
  cmd.exe 拉起 `serial_bridge.py --probe` 自动探测 COM）或已显式启动
  `python serial_bridge.py --port COMx`（详见 `docs/connection-deploy.md`）
- WSL2 侧可访问 `127.0.0.1:9700`（WSL2 localhost 与 Windows 互通）
- COM 口自适应探测：不依赖固定 COM 号；探测无目标时用 `--port COMx` 显式指定
```

- [ ] **Step 2: SKILL.md 新增"自动启动"小节**

在 `## 错误分类` 之后新增：

```
## 自动启动（端点不可达自愈）

- 各子命令（status/ping/exec/monitor/read/ip）在 TCP 端点不可达时默认自动经
  cmd.exe 拉起 Windows 侧转发器（`serial_bridge.py --probe` 自动探测 COM 口），
  幂等（已在跑则跳过），轮询就绪后继续原操作。
- 关闭自动启动：`--no-auto-start`（仅报错，供脚本化/CI）。
- 路径定位：`LC_SERIAL_WIN_PY`（Windows python 路径）> 自动探测；
  `LC_SERIAL_BRIDGE_WS`（serial_bridge.py 的 Windows 可访问路径）> 按 skill 目录
  映射 `\\wsl$\<distro>\...`。
- 管理子命令：
  - `bridge-status`：查询转发端点可达性（只读，不触发自动启动）。
  - `bridge-restart`：确保转发器就绪（不可达时自动拉起）。
```

- [ ] **Step 3: SKILL.md 子命令表与错误分类表更新**

子命令表新增两行：

```
| `bridge-status` | 查询转发端点可达性（只读，不触发自动启动） |
| `bridge-restart` | 确保转发器就绪（不可达时自动拉起） |
```

错误分类表 `ENDPOINT_UNREACHABLE` 处置改为：

```
| `ENDPOINT_UNREACHABLE` | TCP 端点不可达 | 自动启动转发器（默认）；失败或无 cmd.exe 通道时人工检查 Windows 侧转发器 |
```

- [ ] **Step 4: docs/connection-deploy.md 更新**

- 拓扑图注释 `COM5` 改为 `COMx（自适应探测）`。
- 第 4 节标题改为"Windows 侧转发器（自适应 COM）"，正文：

```
## 4. Windows 侧转发器（自适应 COM）

转发器默认自动探测 COM 口：枚举所有串口发 `echo __PROBE__`，识别到
shell 提示符（console:/）即选中；无目标时打印候选并提示用 --port 显式指定。

```bash
pip install pyserial
python serial_bridge.py --probe --listen 127.0.0.1 --listen-port 9700   # 自动探测 COM
python serial_bridge.py --port COM4 --baudrate 115200 --listen 127.0.0.1 --listen-port 9700  # 显式指定
```

- 故障排查表 `serial 连不上` 处置补充："先确认自动启动已触发（未用 --no-auto-start）；
  仍失败检查 Windows 转发器是否运行 / 串口被占 / 探测无目标 COM（用 --port 覆盖）"。
- 故障排查表新增一行：`WSL2 侧自动启动失效 | 检查 LC_SERIAL_WIN_PY / LC_SERIAL_BRIDGE_WS 是否被错误覆盖；确认 /mnt/c/Windows/System32/cmd.exe 存在`

- [ ] **Step 5: 文档自检**

grep 确认 SKILL.md 与 deploy.md 中不再有"固定 COM5""COM5 = Pi 5"的过时表述（保留历史说明处除外）。

---

### Task 5: 端到端验证 + 设计文档更新

**Files:**
- Modify: `/mnt/d/Code/Github/AndroidSystemEnhance/docs/superpowers/specs/2026-10-06-serial-auto-probe-design.md`

- [ ] **Step 1: 端到端验证（真实环境）**

```bash
# 1) 确认现有转发器在 9700 跑（之前手动起过），ensure_bridge 幂等返回
cd ~/.config/opencode/skills/lc-skills-connection-serial && \
  python3 lc_skills_conn_serial.py --timeout 10 bridge-status; echo "rc=$?"
# 2) 自动启动到新端口 9702 验证端到端（探测应选中 COM4）
python3 lc_skills_conn_serial.py --port 9702 --timeout 20 ping; echo "ping rc=$?"
# 3) --no-auto-start 验证：9703 不可达应仅报错不拉起
python3 lc_skills_conn_serial.py --port 9703 --no-auto-start --timeout 5 status; echo "status rc=$?"
```

- [ ] **Step 2: 设计文档状态更新**

在 spec 末尾加"实施结果"小节，记录：探测选中 COM4、自动启动/幂等/--no-auto-start 实测结果、遗留限制（bridge-restart 为幂等拉起非强制重启）。

- [ ] **Step 3: 汇总交付**

向用户汇总改动文件清单 + 实测结果 + 后续使用方式。
