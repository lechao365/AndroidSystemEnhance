# Connection 域

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位
- **是什么**：loop engineering 连接基础设施（跨 provider 协议契约 + provider/device 配置语义 + 具体 provider 实现）
- **职责边界**：定义契约与语义，不含业务 case
- **上下游依赖**：被 `loop/core` 与 `loop/workflows` 消费，依赖 `loop/contracts`

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 子目录清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 无可执行入口，指向子 README | 实际使用时 |
| [设计原则](#设计原则) | 协议/实现分离、profile/运行配置分离、provider 自治 | 深入理解架构时 |
| [关联资源](#关联资源) | 设计文档、workflow 链接 | 深入理解时 |

## 目录说明

| 子目录 | 职责 | 关键入口/被谁引用 |
|-------|------|------------------|
| [`protocol/`](./protocol/) | 跨 provider 协议文档，不绑实现 | `rp5-serial-protocol.md` 被 providers 遵循 |
| [`profiles/`](./profiles/) | provider/device 配置语义，描述「如何理解这台板子」 | 被 providers/* 与 workflows 消费 |
| [`providers/`](./providers/) | 具体 provider 实现：`rp5-serial/`（串口）、`adb/`（网络 ADB） | 被 `loop/core` 与 `loop/workflows` 消费 |

## 使用方式

本目录无可执行入口，仅作为连接能力承载层。快速开始见：
- [`providers/rp5-serial/README.md`](./providers/rp5-serial/README.md)（Host 启动 + Client 三模式）
- [`providers/adb/README.md`](./providers/adb/README.md)（adb transport）

## 设计原则
1. **协议与实现分离**：`protocol/` 定义 host/client 契约，provider 实现遵循但不内嵌协议定义。
2. **profile 与运行配置分离**：`profiles/` 描述设备语义（prompt marker / boot marker / timeout 等），provider 自身只保留最小运行配置。
3. **provider 自治**：每个 provider 同仓管理 host/client/shared/tests，运行位置可不同但代码集中。
4. **Runtime 边界**：connection providers 只负责传输与数据转发，不包含任何业务编排逻辑。所有 verify → decide → analyze → patch → compile → deploy → rerun 编排均由 runtime 引擎（`loop/controller/runtime/`）驱动。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | connection 域设计 |
| 关联 runtime | `../controller/README.md` | runtime engine 消费 rp5-serial bootstrap + adb feature |
