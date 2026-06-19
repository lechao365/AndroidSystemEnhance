# Protocol

存放 host 与 client 之间的协议契约文档。

## 范围

本目录只承载**协议定义**，不承载 provider 实现：

- 传输层与消息格式
- 操作列表与语义
- 统一响应结构与错误码
- 跨 provider 复用的契约约定

具体 provider（如 rp5-serial）的编解码实现位于 `connection/providers/<provider>/python/` 下，遵循本目录的协议文档。

## 当前文档

| 文档 | 说明 |
|------|------|
| [rp5_serial_protocol.md](./rp5_serial_protocol.md) | rp5-serial host/client 协议定义 |
