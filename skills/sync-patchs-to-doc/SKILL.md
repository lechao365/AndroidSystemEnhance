---
name: sync-patchs-to-doc
description: Use when patchs/rpi5 变动需同步技术文档。
---

# sync-patchs-to-doc

当 `patchs/rpi5/` 发生变动后，生成结构化变动报告，由 AI 据此增量更新技术文档。

## 触发场景

- 用户执行完归档（sync-code-to-patchs）后
- 用户提到"更新文档""文档同步""patchs 变了"
- `patchs/rpi5/` 相对 git HEAD 有未同步到文档的变动

## 工作流

### 1. 生成变动报告

```bash
bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh              # 生成变动报告
bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only  # 仅检查，不输出提示
```

脚本基于 git HEAD 对比 `patchs/rpi5/`，按目录分组（`kernel/modified`、`kernel/new`、`aosp/modified`、`aosp/new`、`others`）输出变动类型（A/M/D/R）和行数统计。

### 2. AI 分析影响范围（自主判断）

- 读取变动报告，判断影响的文档范围（`01-打点增强/`、`02-IO增强/` 等）
- 不依赖预定义映射表，必要时可读取全量代码以准确理解变更

### 3. 方案先行（强制）

AI **必须先输出文档更新方案**（具体改哪些文件、改什么内容），**经用户确认后方可落盘**。禁止未经确认直接修改文档。

### 4. 增量落盘

用户确认后，仅更新受影响的文档内容，不做全量重写。

## 约束

| 约束 | 说明 |
|------|------|
| 方案先行 | 必须先输出方案，确认后落盘 |
| 增量更新 | 仅更新受影响内容，不全量重写 |
| 自主判断 | AI 自行判断影响范围，不依赖映射表 |
| 保持一致 | 更新后文档与 patchs 实际状态一致 |

## 不涉及的文档

`patchs/rpi5/README.md` 文件映射表的更新仍走 sync-code-to-patchs 末尾提示，不纳入本流程。
