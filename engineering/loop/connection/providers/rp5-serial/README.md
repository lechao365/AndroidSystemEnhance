# rp5-serial Provider

> **关联设计**：`docs/specs/2026-06-19-loop-engineering-design.md`
> **关联计划**：`docs/plans/2026-06-19-rp5-serial-host-client-mvp.md`
> **关联协议**：`engineering/loop/connection/protocol/rp5_serial_protocol.md`

## 目标

- **Windows Host 独占物理串口**：唯一拥有 COM 口，避免多进程抢占与透传不稳定。
- **WSL2 Client 三模式接入**：
  - `monitor`：只读观察，可并发
  - `interactive`：人工独占写入，近似 MobaXterm 体验
  - `automation`：workflow 独占写入，供业务闭环调用
- **单 writer，无排队**：同一时刻仅允许一个 writer（interactive 或 automation）。
- **仅支持 `send_line`**：MVP 不支持原始字节发送。

## 运行边界

- **Windows Host**：
  - 先前台运行（`python -m rp5_serial.host.server`），后续补 NSSM / WinSW 服务托管
  - 自身仅提供轻量维测日志，不强制套用 harness 完整脚本维测框架
  - 不负责故障判定 / panic 识别 / 规则引擎，只做串口托管 + 数据转发 + session/lease 管理
- **WSL2 Client**：
  - bash 入口复用 harness observability（`harness_bootstrap.sh` / `harness_observability.sh`）
  - 日志落点：`engineering/harness/log/loop-rp5-serial-*/`
  - 不直接打开物理串口，通过逻辑会话访问 Host

## 目录结构

```text
rp5-serial/
├── README.md          本文件
├── WORKFLOW.md        provider 工作流与运行方式
├── bin/               WSL2 bash 入口脚本
│   ├── loop_rp5_serial_status.sh
│   ├── loop_rp5_serial_monitor.sh
│   ├── loop_rp5_serial_interactive.sh
│   └── loop_rp5_serial_automation.sh
└── python/            Python package 根
    ├── rp5_serial/
    │   ├── host/             Windows Host
    │   │   ├── server.py     TCP server + 串口读线程
    │   │   ├── handler.py    协议分发 + stream 推送
    │   │   ├── serial_runtime.py  串口 I/O + session/lease
    │   │   └── logging_utils.py   轻量日志
    │   ├── client/           WSL2 Client
    │   │   ├── status.py     状态查询
    │   │   ├── monitor.py    只读订阅
    │   │   ├── interactive.py 交互终端
    │   │   └── automation.py   workflow API
    │   └── shared/           host/client 共享
    │       ├── models.py     数据模型
    │       ├── errors.py     错误码
    │       └── codec.py      JSON Lines 编解码
    └── tests/                单元 + 流程测试
```

## 快速使用

### Windows Host 前台启动

```bash
# 在 Windows Python 环境下
cd <repo-root>/engineering/loop/connection/providers/rp5-serial
PYTHONPATH=python python -m rp5_serial.host.server --port COM3 --baudrate 115200 --listen-port 9700
```

参数：
- `--port`：物理串口名（COM3 / /dev/ttyUSB0）
- `--baudrate`：波特率（默认 115200）
- `--listen-host`：TCP 监听地址（默认 0.0.0.0）
- `--listen-port`：TCP 监听端口（默认 9700）

### WSL2 Client 使用

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

## MVP 限制

- 仅 `send_line`，不支持原始字节发送
- 单 writer，无排队，无 TTL 回收
- Host 前台运行，不含服务托管（NSSM / WinSW 后续补）
- 不含 boot-failure workflow
- 不含 ADB
- 不含激进恢复动作（L3/L4）

## 参考实现

详见 `WORKFLOW.md`。
