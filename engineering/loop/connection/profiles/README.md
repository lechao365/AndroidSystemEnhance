# Connection Profiles

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：provider/device 配置语义承载层（描述「如何理解这台板子」）。
- **职责边界**：只承载设备语义（prompt marker / boot marker / panic marker / line ending / timeout / reboot loop 阈值 / rule 参数 / workflow override），**不承载** provider 运行配置（COM 口 / baudrate / listen address 由 provider 自身管理）。
- **上下游依赖**：被 `loop/connection/providers/*` 与 `loop/workflows` 消费。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 子目录清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 配置优先级 | 实际使用时 |
| [关联资源](#关联资源) | workflow、设计文档链接 | 深入理解时 |

## 目录说明

| 子目录 | 职责 | 关键入口 |
|-------|------|---------|
| `devices/` | 按设备组织 profile，当前仅 `rp5/` | 被 `le.sh --device-profile` / workflow 引用 |

## 使用方式

本目录无可执行入口，作为配置承载层。

**配置优先级**（后者覆盖前者）：

1. provider 默认配置
2. 设备 profile（如 `devices/rp5/default.json`）
3. workflow override

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../../workflows/lcview-adb-run/` | 消费 rp5/default.json + rp5/adb.json |
| 设计文档 | `docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md` | profile 设计 |
