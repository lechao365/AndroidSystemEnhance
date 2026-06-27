# Raspberry Pi 5 Profile

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：Raspberry Pi 5 设备 profile 集（serial + adb 双 transport 语义）。
- **职责边界**：描述 rp5 的设备语义（prompt / boot / panic marker / 串口参数），不含运行配置。
- **上下游依赖**：被 rp5-serial provider 与 adb provider 消费。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | profile 文件清单与字段语义 | 了解结构时 |
| [使用方式](#使用方式) | 被谁引用 + 串口参数 | 实际使用时 |
| [关联资源](#关联资源) | workflow、设计文档链接 | 深入理解时 |

## 目录说明

| 文件 | transport | 用途 | 关键字段 |
|------|-----------|------|---------|
| `default.json` | serial | boot / bootstrap / fallback | `prompt_markers`（console 提示符）、`boot_markers`（启动阶段标志）、`reboot_markers`、`panic_markers`、`line_ending=\n` |
| `adb.json` | adb | feature suite 与 adb shell 验收 | `boot_markers=[sys.boot_completed=1]`、`panic_markers`、`default_capture_timeout=10s`、`default_recent_limit=400` |

## 使用方式

本目录无可执行入口，作为 profile 文件被引用：

- `le.sh --device-profile .../rp5/default.json`
- `le.sh runtime --device-profile .../rp5/default.json`（runtime engine 自动驱动）

串口参数：baudrate 115200，8N1。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 runtime | `../../../controller/README.md` | runtime engine 消费本目录 profile |
| 设计文档 | `docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md` | profile 设计 |
