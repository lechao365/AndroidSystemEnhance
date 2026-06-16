---
name: sync-code-to-patchs
description: Use when workspace 源码改动需归档到 patchs/rpi5。
---

# sync-code-to-patchs

将 `~/workspace/` 编译源码树的改动单向归档到 `patchs/rpi5/`。

## 触发场景

- 用户在 `~/workspace/` 完成源码改动，需要归档到 patchs
- 用户提到"同步 patchs""归档补丁""提交改动"
- 执行提交前，需要先归档 patchs

## 工作流

### 1. 前置检查（必须）

- 确认改动发生在 `~/workspace/`，而非直接改 `patchs/`
- `patchs/` 是单向归档目录，严禁手动修改（`others/` 除外）
- 详见 [源码改动优先级](../../rules/source-code-priority.md)

### 2. 执行同步

```bash
bash skills/sync-code-to-patchs/sync_code_to_patchs.sh              # 同步归档
bash skills/sync-code-to-patchs/sync_code_to_patchs.sh --check-only  # 仅检查，不执行
```

脚本自动完成：扫描 workspace → 归档到 patchs → 验证完整性 → 陈旧文件检查。

### 3. 检查输出（必须）

脚本输出四种状态：

| 标记 | 含义 | 处理 |
|------|------|------|
| `OK` | 已同步/验证 | 正常 |
| `MISS` | workspace 有改动但 patchs 缺失 | 去掉 `--check-only` 重新执行 |
| `SKIP` | 编译产物已跳过 | 正常（排除规则生效） |
| `STALE` | patchs 有但 workspace 已无 | 手动清理 patchs 中的陈旧文件 |

### 4. 同步 README.md（AI 驱动，方案先行）

脚本已自动生成/更新 `patchs/rpi5/manifest.yaml`（结构映射的真相）。
README.md 的文件映射表由 AI 基于维护，流程：

1. 读取 manifest.yaml，与 README.md 当前文件列表对比，识别新增/删除文件
2. 对新增文件读取对应 diff/源码，生成"改动要点"描述
3. 输出 README 更新方案（具体改哪些行、改什么内容）
4. 用户确认后落盘 README.md

**强制约束**：方案先行，未经确认禁止直接写 README.md。

## 归档规则速查

| 目录 | 内容 | 方式 |
|------|------|------|
| `kernel/modified/` | 对上游已有文件的改动 | `.diff` 文件 |
| `kernel/new/` | 全部新增文件 | 完整复制 |
| `aosp/modified/` | 对上游已有文件的改动 | `.diff` 文件 |
| `aosp/new/` | 全部新增文件 | 完整复制 |
| `others/` | 独立程序 | 直接在 others/ 维护 |

**modified vs new 判定**：文件在 upstream base 中已存在且被修改 → modified；不存在（新增）→ new。

**排除规则**：编译产物（`*.o` `*.ko` `Image` `*.dtb`）、构建缓存（`out/` `prebuilts/`）、上游未改动文件均不归档。

详细规则与路径映射见脚本内注释和历史设计文档。

## README.md 格式规范（AI 维护目标）

README.md 包含三段，**禁止目录树**（与映射表重复）：

| 章节 | 内容 | 来源 |
|------|------|------|
| 概述 + 包含的特性 | 高层语义描述 | AI/人工 |
| 文件映射表 | 分组表格 | AI 从 manifest 渲染前两列 + 读取 diff 填描述 |
| 回写命令 | 静态部署命令 | 不变 |

### 文件映射表格式

按 `kernel/modified`、`kernel/new`、`aosp/modified`、`aosp/new`、`others` 分组，
每个文件一行，列：`patch 路径 | workspace 源码路径 | 改动要点`。
patch 路径与 workspace 源码路径严格来自 manifest.yaml，AI 不得臆造。
改动要点为空时留空，待后续补充。

### 首次迁移

首次按本格式重写 README.md 时，现有 README 中已有的"改动要点/说明"
必须迁移到新表格对应行，不得丢失。
