# rp5-serial Host/Client 协议

> **状态**：MVP 骨架，随 provider 实现逐步细化
> **关联**：`engineering/loop/connection/providers/rp5-serial/`

## 传输层

- **传输方式**：本地 TCP
- **消息格式**：JSON Lines（每行一个 JSON 对象，以 `\n` 结尾）
- **编码**：UTF-8
- **方向**：
  - client → host：请求
  - host → client：响应 + 订阅流事件

## 操作列表

| 操作 | 说明 |
|------|------|
| `session.open` | 打开一次 provider 会话，声明模式（monitor/interactive/automation） |
| `session.close` | 关闭会话，释放相关资源 |
| `session.status` | 查询 host/serial/session/writer 当前状态 |
| `stream.subscribe` | 订阅设备输出流，仅读取，不持有 writer |
| `writer.acquire` | 申请写入权；同一时刻仅允许一个 writer，无排队 |
| `writer.release` | 释放写入权 |
| `input.send_line` | 发送一行文本到设备（自动追加 `\n`）；必须持有 writer |

## 统一响应结构

所有响应采用统一形状：

```json
{
  "ok": true,
  "code": "OK",
  "message": "ok",
  "data": {}
}
```

- `ok`：布尔，标识请求是否成功
- `code`：机器可读的错误码（见下表）
- `message`：人类可读的说明
- `data`：载荷对象，失败时为 `{}`

## 错误码

| 错误码 | 含义 |
|--------|------|
| `OK` | 成功 |
| `HOST_NOT_READY` | Host 尚未就绪（未启动 / 未完成初始化） |
| `SERIAL_NOT_AVAILABLE` | 串口不可用（被占用 / 不存在 / 打开失败） |
| `SESSION_NOT_FOUND` | 会话不存在或已关闭 |
| `WRITER_BUSY` | 写入权已被其他 writer 占用 |
| `INVALID_MODE` | 会话模式非法 |
| `INVALID_REQUEST` | 请求格式或参数非法 |

## 单 writer 约束

- 同一时刻只允许一个 writer（`interactive` 或 `automation`）。
- 已有 writer 时，新的 `writer.acquire` 请求返回 `WRITER_BUSY`，**无排队**。
- `monitor` 模式可并发观察，不持有 writer，不能发送输入。
- writer 释放后，其他请求可立即申请。

## MVP 限制

- 仅支持 `input.send_line`，不支持原始字节发送（`input.send` 暂不实现）。
- `writer.acquire` 无排队、无 TTL 回收（后续迭代补充）。
- 不实现 `expect.wait`（等待特定 pattern），由 client 侧自行轮询输出缓冲。
