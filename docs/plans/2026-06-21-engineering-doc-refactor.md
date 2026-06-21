# Engineering 文档重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 engineering/ 下全部文档，统一 README/rules 模板，消除 SSOT 违规，统一命名，减少冗余文档。

**Architecture:** T4 模板形态（单一核心 5 节 + 专化扩展块）。5 个阶段串行推进，每阶段产出可独立验证。每阶段完成后运行 `validate_harness_docs.sh`。

**Spec:** [docs/specs/2026-06-21-engineering-doc-refactor-design.md](../specs/2026-06-21-engineering-doc-refactor-design.md)

**Tech Stack:** Markdown 文档；git mv/rm；bash 校验脚本。

---

## 关键执行原则（所有阶段通用）

1. **文档重构 ≠ 纯格式调整**：每个 README 重写 Task 的第一步必须是「读取该目录下所有文件（代码/配置/子目录）与现有 README」，基于新材料重构——补齐缺失内容、删除失效内容。
2. **模板权威**：`harness/templates/engineering-readme-template.md`（阶段 1 产出）是所有 engineering 下 README 的约束源。核心 5 节必选，扩展块按需。
3. **每阶段验证**：完成后运行 `bash engineering/harness/scripts/validate_harness_docs.sh`，退出码 0 方可进入下一阶段。
4. **commit 粒度**：每个 Task 一次 commit，message 用项目惯用格式（`重构(docs): ...` / `新增(docs): ...` / `杂项(docs): ...`），scope 参考config/scope-mapping.yaml。

---

## 阶段总览

| 阶段 | 内容 | 依赖 | 分片文件 |
|------|------|------|---------|
| 1-2 | 模板新建 + reference/ 建立 + build-reference 迁移 | 无 | [phase-1-2](./2026-06-21-engineering-doc-refactor-phase-1-2.md) |
| 3-4 | CONTROL-CHARTER 融入 + rp5-serial 融合 + 协议重命名 | 阶段 1-2 | [phase-3-4](./2026-06-21-engineering-doc-refactor-phase-3-4.md) |
| 5A | loop/README 精简 + 中型 README 前 6 个 | 阶段 1-4 | [phase-5a](./2026-06-21-engineering-doc-refactor-phase-5a.md) |
| 5B | 中型 README 后 6 个 + 轻型 README 11 份 | 阶段 1-4 + 5A | [phase-5b](./2026-06-21-engineering-doc-refactor-phase-5b.md) |

执行顺序：1-2 → 3-4 → 5A → 5B。阶段 3-4 与 5A 之间无文件冲突时可部分并行（见各分片文件的「前置依赖」说明）。

---

## 各分片详细任务

详细 Task/Step 见上述 4 个分片文件。每个分片文件独立可读，含完整的 Files 清单与步骤。
