---
name: rp5-serial-provider
description: rp5-serial provider 工作流
---

# rp5-serial Provider Workflow

## 拓扑

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

## Windows Host 启动方式

当前阶段：**前台运行**

```bash
python -m rp5_serial.host.server --config <host-config>
```

后续补充：

- NSSM / WinSW 服务托管，实现开机自启与崩溃重启
- health check 端点

Host 启动后职责：

1. 独占打开 COM 口
2. 维护 session 状态与 writer lease
3. 对外提供本地 TCP 协议端点（JSON Lines）
4. 持续落盘串口 transcript（`transcript_path`，每行带 ISO 时间戳）
5. 记录轻量运行日志（启停 / 串口状态 / reconnect / attach / lease 变化）

## WSL2 Client 三模式

### monitor

- 只读观察，可多人并发
- 不持有 writer，不能发送输入
- 用途：启动日志观察、automation 旁路观察

### interactive

- 人工独占写入
- 申请 writer lease，失败则拒绝
- 用途：近似 MobaXterm 的串口交互

### automation

- workflow 独占写入
- 申请 writer lease，busy 直接失败（无排队）
- 用途：为 `boot-failure-debug-loop` 等业务闭环提供编排接口

## 单 writer 约束

- 读通道共享，写通道独占
- 已有 writer 时，新 `writer.acquire` 返回 `WRITER_BUSY`
- 无排队、无自动抢占
- writer 主动 release 或会话关闭后，其他请求可立即申请

## MVP 限制

- 仅支持 `input.send_line`（自动追加 `\n`），不支持原始字节发送
- 单 writer，无排队，无 TTL 回收
- Host 前台运行，不含服务托管
- 不实现 `expect.wait`，由 client 侧自行轮询输出缓冲
- 不含 ADB
- transcript 持续落盘（`output/host-log/rp5-serial-transcript.log`），status 接口返回 `transcript_path`

## bash 入口与日志

WSL2 bash 入口统一复用 harness observability：

- 入口命名：`loop_rp5_serial_status.sh` / `loop_rp5_serial_monitor.sh` / `loop_rp5_serial_interactive.sh` / `loop_rp5_serial_automation.sh`
- 日志 script-name：`loop-rp5-serial-status` / `loop-rp5-serial-monitor` / `loop-rp5-serial-interactive` / `loop-rp5-serial-automation`
- 落点：`engineering/output/log/loop-rp5-serial-*/`
