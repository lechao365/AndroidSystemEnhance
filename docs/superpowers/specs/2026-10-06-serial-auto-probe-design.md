# connection-serial skill 自适应 COM 探测 + 自动启动转发器 设计文档

> 日期：2026-10-06
> 范围：lc-skills-connection-serial（发布态 `~/.config/opencode/skills/lc-skills-connection-serial/`）
> 背景：树莓派 Pi5 console 的 Windows COM 口会随枚举变化（实测 COM5→COM4），且 WSL2 侧在转发器未启动时无法自动恢复，需人工去 Windows 侧手工起 `serial_bridge.py`。

## 问题

1. `serial_bridge.py` 默认固定 `--port COM5`，COM 口漂移（换 USB 口/重枚举）后失效，需人工重新指定。
2. WSL2 侧 `status/ping/exec` 等命令遇 `ENDPOINT_UNREACHABLE` 仅报错，无法自愈——转发器未起时链路完全不可用，且须跨 Windows/WSL 人工干预。

## 目标

- **COM 口自适应**：转发器启动时自动枚举 Windows COM 口，发探测命令识别目标设备 console（不依赖固定 COM 号）。
- **自动起转发器**：WSL2 侧命令检测不到 TCP 端点时，默认自动经 `cmd.exe` 拉起 Windows 侧转发器（幂等：已在跑则跳过；失败才报错）。
- **可运维**：保留显式 `--port` 覆盖；提供人工管理入口；文档同步更新，移除"固定 COM5"假设。

## 非目标（YAGNI）

- 不做 systemd 常驻服务（COM 漂移场景仍需重启逻辑，运维重）。
- 不做 Windows 侧开机自启（已有任务计划程序方案，文档保留）。
- 不做多设备并发探测选择（候选多个时取第一个能判定为目标的，打印候选供人工覆盖）。

## 架构与组件

```
WSL2 侧                          Windows 侧
lc_skills_conn_serial.py         serial_bridge.py
  └─ ensure_bridge():            ├─ 默认 --probe：枚举 COM 逐个探测
       先重试 TCP 连通               └─ --port COMx：显式直连（跳过探测）
       不可达 → cmd.exe 调
       Windows python 起转发器
       → 轮询 TCP 就绪 → 返回
```

### 组件 1：`serial_bridge.py`（Windows 侧）探测模式

- 新增 `--probe` 选项（默认 True）：枚举 `serial.tools.list_ports.comports()`，对每个候选：
  - 打开 115200，`reset_input_buffer()`，发 `echo __PROBE__`（带 `\r`），读 1s。
  - 判定目标：收到的响应含 `__PROBE__` 回显 或 含 `console:/`/`#`/`$` 提示符特征。
  - 判定成功 → 选定该 COM 并进入正常转发循环；打印 `[bridge] probe selected COMx (desc)`。
  - 全部失败 → 打印候选列表与失败原因，退出码 1（供调用方定位）。
- 显式 `--port COMx`：跳过探测直连（保持原行为）。
- `--baudrate` / `--listen` / `--listen-port` 参数不变。

### 组件 2：`lc_conn_serial_core.py`（WSL2 侧）新增 `ensure_bridge()`

- 签名：`ensure_bridge(host, port, timeout) -> None`，抛 `SerialError("ENDPOINT_UNREACHABLE", ...)` 于最终失败。
- 流程：
  1. 快速探测 `host:port` TCP 可达（0.5s socket connect）→ 可达直接返回（幂等，不重复起）。
  2. 探测 Windows 命令通道：`/mnt/c/Windows/System32/cmd.exe` 存在；无通道 → 抛错（无法自动起）。
  3. 组装启动命令（经 cmd.exe，后台 detach，不阻塞 WSL 侧）：
     - Windows python：`LC_SERIAL_WIN_PY` 环境变量 > 自动探测 `where python` 输出第一个路径 > 默认兜底。
     - bridge 脚本 Windows 可访问路径：`LC_SERIAL_BRIDGE_WS` 环境变量 > 自动按 skill 目录映射 `\\wsl$\<distro>\<skill 绝对路径>`。
     - 参数：`python <bridge_ws> --probe --listen 127.0.0.1 --listen-port <port>`。
     - 用 `cmd.exe /c start /b ...` 后台启动，避免阻塞。
  4. 轮询 `host:port` 就绪（默认 15s，间隔 0.5s）→ 就绪返回。
  5. 超时 → 抛 `SerialError("ENDPOINT_UNREACHABLE", "自动启动转发器失败...")`。

### 组件 3：`lc_skills_conn_serial.py` 自动启动接入 + 管理子命令

- 各子命令 `connect()` 前统一经 `ensure_bridge()`（`ENDPOINT_UNREACHABLE` 时自动起转发器）。
  - 注意：`exec`/`ip`/`ping` 已在 `_write_lock` 内 connect，须在加锁内完成 ensure（避免并发起多个转发器）。
- 新增子命令：
  - `bridge-status`：查询 TCP 端点 + 返回当前探测到的 COM 信息（转发器启动 stdout 含 COM 选择，WSL 侧暂不可直接读，先返回端点可达性 + 提示）。
  - `bridge-restart`：先探测现有端点，强制重启转发器（kill 旧进程 + 重新拉起）——用于 COM 漂移后人工刷新。
- 新增 `--no-auto-start` 全局开关：关闭自动启动（恢复纯报错行为），供脚本化/CI 使用。

### 组件 4：文档更新

- `SKILL.md`：Preconditions 改为"转发器可自动启动/或已显式启动"；新增"自动启动"小节说明触发与 `--no-auto-start`；错误分类 `ENDPOINT_UNREACHABLE` 处置改为"自动起转发器，失败再人工"。
- `docs/connection-deploy.md`：删除"固定 COM5"假设，改为"COM 口自适应探测；显式指定用 `--port`"；补充自动启动的环境变量说明。

## 错误处理

| 场景 | 行为 |
|------|------|
| 端点本就可达 | `ensure_bridge` 直接返回（幂等） |
| 端点不可达 + 无 cmd.exe 通道 | 抛 `ENDPOINT_UNREACHABLE`，报"无 Windows 命令通道，无法自动启动" |
| Windows python 找不到 | 抛错并提示设置 `LC_SERIAL_WIN_PY` |
| bridge 启动但端口迟迟不就绪（无目标 COM） | 轮询超时抛错，附"无目标 COM 候选"提示 |
| 多 COM 候选 | 取第一个判定成功者；`serial_bridge` 打印全部候选供人工 `--port` 覆盖 |

## 测试策略

1. **Windows 侧探测逻辑**（本机串口环境）：串口桥已起（COM4），跑 `serial_bridge.py --probe` 应能选中 COM4；跑 `--port COM9` 应报打开失败。
2. **WSL 侧 ensure_bridge 幂等**：端点可达时 `ensure_bridge` 不触发启动（实测 0.1s 快速返回）。
3. **自动启动端到端**：模拟端点不可达（换端口）→ 触发 ensure → 拉起 → 轮询就绪。
4. **单元测试**（skill 自测）：core 层 ensure_bridge 分支用 mock 覆盖（无 cmd 通道 / python 探测 / 轮询超时）。

## 实施结果（2026-10-06 实测）

实现过程中 code review 发现并修复了 6 个关键问题（C1 必修 + I1-I5 建议）：

| 编号 | 问题 | 修复 |
|------|------|------|
| C1 | `shell=True` 双引号把 `\\wsl$\` UNC 折叠为 `\wsl$\`（驱动器相对路径）→ 自动启动必然失败 | 改为 WSL interop 直接 exec Windows python（exe 用 `/mnt/c/...`、脚本用 `C:\...` 路径），绕开 cmd.exe 全部解析问题 |
| I1 | `import serial.tools.list_ports` 在模块顶部，缺 pyserial 时友好提示不生效 | 移入 `_probe_console` 局部导入 |
| I2 | `cmd echo %WSL_DISTRO_NAME%` 恒空（WSL 环境变量不透传 Windows） | 改用 `os.environ["WSL_DISTRO_NAME"]` |
| I3 | SerialConn 的 sendall/recv OSError（连接断开）裸抛 traceback | 包装为 `SerialError("SERIAL_SILENT")` |
| I4 | cmd.exe 输出按 locale 解码（中文 Windows 乱码） | 显式 `errors="replace"` |
| I5 | 探测盲写所有 COM 有副作用 | 新增 `--probe-ports` 白名单 |

实测中发现并绕开的三个 WSL↔Windows 交互坑：
1. **cmd /c 引号拼接**：`'"C:\...python.exe"'` 引号包引号 → python 路径被当成命令名 → 改用 interop 直接 exec。
2. **UNC pushd 不可达**：`\\wsl$\` / `\\wsl.localhost\` 从 cmd pushd 均报"找不到路径" → 脚本需在 Windows 本地盘，`_sync_bridge_to_win` 自动同步到 `C:\Users\<user>\AppData\Local\lc-skills\`。
3. **WSL 进程树回收**：经 cmd.exe 起的转发器随 WSL 父进程退出被杀 → interop 直接 exec 的 Windows 进程独立存活（实测父退出后仍存活）。

**端到端验证（8 项全过）**：
- 冷启动自动启动（CLI ping 9750）→ pong（4.1s 含启动）
- 幂等（已跑 0.1s 返回）
- bridge-status 只读不触发 / bridge-restart READY
- exec 经串口回显 / ip 取到设备 192.168.1.19
- --no-auto-start 仅报错不拉起

## 交付物

- `serial_bridge.py`（探测模式 + --probe-ports 白名单）
- `lc_conn_serial_core.py`（ensure_bridge：interop exec + 脚本同步 + 轮询）
- `lc_skills_conn_serial.py`（自动接入 + bridge-status/restart + --no-auto-start）
- `SKILL.md`、`docs/connection-deploy.md` 更新

## 遗留限制

- `bridge-restart` 为幂等确保就绪（端点可达时直接返回），不强制重启已挂起但 TCP 通的桥（COM 漂移且旧桥还活着时需手动杀旧进程）。
- 多实例竞争同一 COM：后起探测打不开被占 COM（PermissionError），转投其他端口前须先停旧转发器。
