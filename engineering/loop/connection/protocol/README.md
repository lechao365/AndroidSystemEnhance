# Protocol

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位
- **是什么**：host 与 client 之间的协议契约文档承载层
- **职责边界**：只承载协议定义（传输层 / 操作列表 / 响应结构 / 错误码 / 跨 provider 复用契约），不承载 provider 实现（编解码在 `providers/<provider>/python/`）
- **上下游依赖**：被 `providers/rp5-serial/` 与 `providers/adb/` 遵循，无上游依赖

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 协议文档清单 | 了解结构时 |
| [使用方式](#使用方式) | 无可执行入口，仅查阅 | 实际使用时 |
| [关联资源](#关联资源) | 设计文档、provider 链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 被谁引用 |
|------|------|---------|
| [`rp5-serial-protocol.md`](./rp5-serial-protocol.md) | rp5-serial host/client 协议定义：JSON Lines 传输、操作列表、统一响应、错误码 | `providers/rp5-serial/` 遵循 |

## 使用方式

本目录无可执行入口，仅作为协议文档承载层。查阅协议请读 [`rp5-serial-protocol.md`](./rp5-serial-protocol.md)。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | 协议设计章节 |
| 关联 workflow | [`../providers/rp5-serial/`](../providers/rp5-serial/) | 遵循本协议的 provider 实现 |
