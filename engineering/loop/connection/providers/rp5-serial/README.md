# rp5-serial Provider

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：Windows Host 独占物理串口 + WSL2 Client 三模式接入的 rp5-serial provider
- **职责边界**：串口托管 + 数据转发 + session/lease 管理；不负责故障判定 / panic 识别 / 规则引擎
- **上下游依赖**：依赖 harness observability（bash 入口）；被 loop/workflows 消费

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 子目录/文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | Host 启动、Client 入口、参数速查 | 实际使用时 |
| [关联资源](#关联资源) | 设计文档、协议、实施计划 | 深入理解时 |
| [运行流程](#运行流程) | 拓扑、Host/Client 职责、单 writer 约束 | 理解运行机制时 |
| [MVP 限制](#mvp-限制) | 当前版本不包含的功能 | 评估能力边界时 |

## 目录说明

```text
rp5-serial/
├── README.md          本文件
├── bin/               WSL2 bash 入口脚本
│   ├── loop_rp5_serial_status.sh
│   ├── loop_rp5_serial_monitor.sh
│   ├── loop_rp5_serial_interactive.sh
│   └── loop_rp5_serial_automation.sh
└── python/            Python package 根
    ├── rp5_serial/
    │   ├── __init__.py
    │   ├── transport.py       TCP 连接与 JSON Lines 帧收发
    │   ├── host/              Windows Host
    │   │   ├── server.py      TCP server + 串口读线程
    │   │   ├── handler.py     协议分发 + stream 推送
    │   │   ├── serial_runtime.py  串口 I/O + session/lease
    │   │   └── logging_utils.py   轻量日志
    │   ├── client/            WSL2 Client
    │   │   ├── status.py      状态查询
    │   │   ├── monitor.py     只读订阅
    │   │   ├── interactive.py 交互终端
    │   │   └── automation.py  workflow API
    │   └── shared/            host/client 共享
    │       ├── models.py      数据模型
    │       ├── errors.py      错误码
    │       └── codec.py       JSON Lines 编解码
    └── tests/                 单元 + 流程测试
```

> 协议定义见 [../protocol/rp5-serial-protocol.md](../protocol/rp5-serial-protocol.md)。

## 使用方式

### 快速开始

**Windows Host 前台启动**（推荐方式：双击或 CMD 运行一键脚本）

```bat
REM 默认参数: COM5 / 115200 / 9700
engineering\loop\scripts\start_rp5_serial_host.bat

REM 自定义 COM 口
engineering\loop\scripts\start_rp5_serial_host.bat COM3

REM 全参数自定义
engineering\loop\scripts\start_rp5_serial_host.bat COM3 9600 9800
```

**手动方式**（备用）：

```bat
cd <repo-root>\engineering\loop\connection\providers\rp5-serial
set PYTHONPATH=python
python -m rp5_serial.host.server --port COM5 --baudrate 115200 --listen-port 9700
```

Host 参数：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--port` | 物理串口名（COM5 / COM3 / /dev/ttyUSB0） | 无（必须指定） |
| `--baudrate` | 波特率 | 115200 |
| `--listen-host` | TCP 监听地址 | 0.0.0.0 |
| `--listen-port` | TCP 监听端口 | 9700 |
| `--log-dir` | Host 轻量日志目录 | harness 配置路径 |

**WSL2 Client 使用**：

```bash
# 查询状态
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh --host <windows-ip> --port 9700

# 只读监控
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh --host <windows-ip> --port 9700

# 交互终端（获取 writer）
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh --host <windows-ip> --port 9700

# automation 单行发送
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh --host <windows-ip> --port 9700 --send "logcat -d"
```

> `--host` 默认 127.0.0.1。WSL2 连接 Windows Host 时需用 Windows 宿主机 IP。

### 入口清单

| 入口 | 作用 | 调用方式 |
|------|------|---------|
| `loop_rp5_serial_status.sh` | 状态查询（serial/port/writer 信息） | `bash .../bin/loop_rp5_serial_status.sh --host <ip> --port 9700` |
| `loop_rp5_serial_monitor.sh` | 只读监控（订阅 stream） | `bash .../bin/loop_rp5_serial_monitor.sh --host <ip> --port 9700` |
| `loop_rp5_serial_interactive.sh` | 交互终端（独占 writer） | `bash .../bin/loop_rp5_serial_interactive.sh --host <ip> --port 9700` |
| `loop_rp5_serial_automation.sh` | 自动化发送（独占 writer） | `bash .../bin/loop_rp5_serial_automation.sh --host <ip> --port 9700 --send "..."` |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | loop engineering 设计 |
| 关联计划 | `docs/plans/2026-06-19-rp5-serial-host-client-mvp.md` | MVP 实施计划 |
| 关联协议 | [../protocol/rp5-serial-protocol.md](../protocol/rp5-serial-protocol.md) | host/client JSON Lines 协议定义 |

## 运行流程

### 拓扑

```text
RPi5 UART
  -> Windows COM
  -> rp5-serial host (独占物理串口)
      -> raw stream / 本地日志 / transcript
      -> session 状态 / writer lease
      -> 协议端点 (本地 TCP, JSON Lines)
  -> WSL2 rp5-serial client
      -> monitor        (只读)
      -> interactive    (人工独占写)
      -> automation     (workflow 独占写)
```

### Host 启动与职责

当前阶段 Host 为**前台运行**（命令见「使用方式 > 快速开始」），后续补充 NSSM / WinSW 服务托管与 health check 端点。

Host 启动后职责：

1. 独占打开 COM 口
2. 维护 session 状态与 writer lease
3. 对外提供本地 TCP 协议端点（JSON Lines）
4. 持续落盘串口 transcript（`transcript_path`，每行带 ISO 时间戳）
5. 记录轻量运行日志（启停 / 串口状态 / reconnect / attach / lease 变化）

### Client 三模式详解

#### monitor

- 只读观察，可多人并发
- 不持有 writer，不能发送输入
- 用途：启动日志观察、automation 旁路观察

#### interactive

- 人工独占写入
- 申请 writer lease，失败则拒绝
- 用途：近似 MobaXterm 的串口交互

#### automation

- workflow 独占写入
- 申请 writer lease，busy 直接失败（无排队）
- 用途：为 `boot-failure-debug-loop` 等业务闭环提供编排接口

### 单 writer 约束

- 读通道共享，写通道独占
- 已有 writer 时，新 `writer.acquire` 返回 `WRITER_BUSY`
- 无排队、无自动抢占
- writer 主动 release 或会话关闭后，其他请求可立即申请

### bash 入口与日志规范

WSL2 bash 入口统一复用 harness observability（`harness_bootstrap.sh` / `harness_observability.sh`）：

- 入口命名：`loop_rp5_serial_status.sh` / `loop_rp5_serial_monitor.sh` / `loop_rp5_serial_interactive.sh` / `loop_rp5_serial_automation.sh`
- 日志 script-name：`loop-rp5-serial-status` / `loop-rp5-serial-monitor` / `loop-rp5-serial-interactive` / `loop-rp5-serial-automation`
- 落点：`engineering/output/log/loop-rp5-serial-*/`

## MVP 限制

- 仅支持 `input.send_line`（自动追加 `\n`），不支持原始字节发送
- 单 writer，无排队，无 TTL 回收
- Host 前台运行，不含服务托管（NSSM / WinSW 后续补）
- 不实现 `expect.wait`，由 client 侧自行轮询输出缓冲
- transcript 持续落盘（`output/host-log/rp5-serial-transcript.log`），status 接口返回 `transcript_path`
- 不含 ADB
- 不含激进恢复动作（L3/L4）
- 不含 boot-failure workflow
