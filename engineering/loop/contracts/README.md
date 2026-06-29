# Loop Contracts

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop 控制面 machine-readable contract（数据模型 + 失败码枚举）。
- **职责边界**：纯数据定义层，不含逻辑 / transport / IO。
- **上下游依赖**：无上游依赖（最底层），被 `loop/controller` 共享依赖。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 模块清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | import 方式 + 测试命令 | 实际使用时 |
| [关联资源](#关联资源) | controller、设计文档链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口 |
|------------|------|---------|
| `python/loop_contracts/models.py` | 六 dataclass：`StageResult`、`AttemptState`、`LoopSession`、`RuntimeState`、`CheckpointRecord`、`TerminationDecision`；`RuntimeTerminalState` StrEnum；`SessionState`（= `LoopSession` 的 deprecated alias，保留向后兼容） | 被 controller import |
| `python/loop_contracts/failure_codes.py` | `FailureCode` StrEnum（18 项）：NONE / RUN_FAILED / EVIDENCE_FAIL / EVIDENCE_INSUFFICIENT / REPEATED_FAILURE / REGRESSION_DETECTED / DEPLOY_FATAL / SESSION_STATE_ERROR / COMPILE_FAILED / PATCH_REJECTED / BOOT_TIMEOUT_ROLLBACK / DUPLICATE_PATCH / KERNEL_DEAD_NO_SHELL / TRANSPORT_UNRECOVERABLE / ROLLBACK_FAILED / VERIFICATION_REGRESSION / VERIFICATION_STUCK / WALL_CLOCK_BUDGET_EXCEEDED | 被 policy 引用 |
| `python/loop_contracts/__init__.py` | 导出九符号（AttemptState / CheckpointRecord / FailureCode / LoopSession / RuntimeState / RuntimeTerminalState / SessionState / StageResult / TerminationDecision） | import 入口 |
| `python/tests/test_models.py` / `test_failure_codes.py` / `test_runtime_models.py` | 数据模型 / 失败码 / runtime 模型单元测试 | pytest |

## 使用方式

本目录无可执行入口，作为契约库被 import。

### 测试

```bash
PYTHONPATH="engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/contracts/python/tests/ -v
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../controller/` | 消费契约的实现层 |
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | 契约定义章节 |
