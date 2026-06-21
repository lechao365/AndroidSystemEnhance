# adb Provider

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop engineering 的 `transport=adb` live transport provider。
- **职责边界**：提供 adb connect/disconnect/shell（带 exit code 解析）/root-su0 提权/pull/logcat 多 buffer/reboot+wait-for-device/runtime context。
- **上下游依赖**：依赖 `loop/core`（BaseTransport 契约），被 `loop/workflows/lcview-adb-run` 消费。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 模块清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 公开 API + 测试命令 | 实际使用时 |
| [关联资源](#关联资源) | workflow、设计文档链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口 |
|------------|------|---------|
| `python/loop_adb/client.py` | `AdbClient`：adb CLI 子进程封装；`AdbCommandResult`/`AdbShellResult` 统一返回结构；解析 `__LE_EXIT_CODE__` 标记拿设备端真实 exit code | 被 `AdbTransport` 调用 |
| `python/loop_adb/transport.py` | `AdbTransport`：实现 `BaseTransport` 契约（acquire/send/capture/reboot/pull/describe_runtime_context） | 被 loop core 调用 |
| `python/loop_adb/__init__.py` | 导出 `AdbClient` / `AdbCommandError` / `AdbCommandResult` / `AdbShellResult` / `AdbTransport` | import 入口 |
| `python/tests/` | `test_client.py` + `test_transport.py` | pytest |

## 使用方式

本目录无可独立 CLI 入口，作为 transport 库被 workflow import。

### 公开 API

| 类 | 说明 |
|----|------|
| `AdbClient` | adb 子进程封装：`connect` / `disconnect` / `wait_for_device` / `shell` / `root` / `pull` / `reboot` / `logcat` |
| `AdbTransport` | `BaseTransport` 实现，供 loop core 以 acquire/send/capture/reboot 语义调用 |
| `AdbCommandResult` | adb 子进程统一返回（argv / exit_code / stdout / stderr） |
| `AdbShellResult` | adb shell 解析结果（含 `__LE_EXIT_CODE__` 提取的真实 exit code） |

### 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" \
  python3 -m pytest engineering/loop/connection/providers/adb/python/tests/ -v
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../../workflows/lcview-adb-run/` | adb feature suite 消费本 provider |
| 设计文档 | `docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md` | adb provider 设计 |
