---
name: sync-code-to-patchs
description: workspace 源码改动归档到 patchs/rpi5，并自动更新 README 文件映射表。
---

# sync-code-to-patchs

将 `~/workspace/` 编译源码树的定制改动**全量镜像**到 `patchs/rpi5/`，并一键更新 README 文件映射表。

**核心语义**：`patchs/rpi5/` 是 workspace 定制改动的**精确镜像**。
workspace 删除的文件，patchs 同步删除（默认全量镜像，含删除对齐），确保可一键精确切回 aosp/kernel。

## 工作流

### 1. 前置检查（必须）

- 确认改动发生在 `~/workspace/`，而非直接改 `patchs/`
- `patchs/` 是单向归档目录，严禁手动修改（`others/` 除外）
- 详见 [源码改动优先级](../../rules/source-code-priority.md)

### 2. 执行同步

```bash
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh              # 全量镜像同步（默认含删除）
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --check-only  # 仅检查，不执行（STALE 仅报告将删除项）
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --no-prune    # 仅添加/更新，不删除对齐
```

脚本自动完成：扫描 workspace → 归档到 patchs → 清理空 diff → 删除对齐（workspace 已无的 patchs 文件）→ 重生成 manifest.yaml。

### 3. 检查输出（必须）

脚本输出五种状态：

| 标记 | 含义 | 处理 |
|------|------|------|
| `OK` | 已同步/验证 | 正常 |
| `MISS` | workspace 有改动但 patchs 缺失 | 去掉 `--check-only` 重新执行 |
| `SKIP` | 编译产物已跳过 | 正常（排除规则生效） |
| `PRUNE` | patchs 文件在 workspace 已不存在 / modified 已恢复原样 | 已自动删除对齐（全量镜像），正常 |
| `STALE` | （仅 `--no-prune` 时出现）陈旧文件未删除 | 按需去掉 `--no-prune` 重新执行以删除对齐 |

### 4. 同步 README.md（一键自动，直接落盘）

脚本已自动生成/更新 `patchs/rpi5/manifest.yaml`（结构映射的真相）。
README.md 的文件映射表由 AI 基于 manifest 维护，**一键自动更新，无需用户确认**。

**归档结果判定（前置）**：

| 脚本情形 | 是否更新 README |
|----------|----------------|
| 输出含 `MISS`（workspace 改动未归档） | ❌ 停下报告，等待用户处理，不更新 README |
| `--check-only` 仅检查模式 | ❌ 仅报告检查结果（含将清理的空 diff），不更新 README |
| `PRUNE`（删除对齐）/ 空diff清理 | ✅ 正常，继续更新 README |
| 归档成功（非 check-only 且无 MISS） | ✅ 执行下面的 README 更新 |

> 注：`STALE` 仅在 `--no-prune` 模式出现；`--check-only` 模式下空 diff 和待删文件均以 `PRUNE`（将清理/将删除）报告。正常全量镜像执行时，陈旧文件已被 PRUNE 自动删除，不再是停止条件。

**README 更新流程（归档成功后自动执行）**：

1. 读取 `patchs/rpi5/manifest.yaml`，与 README.md 当前文件映射表对比，识别新增/删除文件
2. 对新增文件读取对应 diff/源码，生成"改动要点"描述
3. **已删除文件**（manifest 中已消失，对应 workspace 删除/恢复原样）的行**直接移除**，不保留历史
4. 按"文件映射表格式"规范更新 README.md 并**直接落盘**
5. 输出更新摘要（新增 N 条 / 删除 M 条 / 修改要点 K 条）

**约束**：
- patch 路径与 workspace 源码路径严格来自 `manifest.yaml`，AI 不得臆造
- 现有 README 中已有的"改动要点/说明"必须迁移到新表格对应行，不得丢失
- 概述章节、"目录结构"章节、回写命令章节保持不变，仅更新文件映射表

## 归档规则速查

| 目录 | 内容 | 方式 |
|------|------|------|
| `kernel/modified/` | 对上游已有文件的改动 | `.diff` 文件 |
| `kernel/new/` | 全部新增文件 | 完整复制 |
| `aosp/modified/` | 对上游已有文件的改动 | `.diff` 文件 |
| `aosp/new/` | 全部新增文件 | 完整复制 |
| `others/` | 独立程序 | 直接在 others/ 维护 |

**modified vs new 判定**：文件在 upstream base 中已存在且被修改 → modified；不存在（新增）→ new。

**非 repo 目录**（如 `vendor/lechao`，workspace 中无 git 仓库的独立目录）：
- 用 `find -type f | cp` 全量复制到 `aosp/new/` 下，保持相对路径
- 永不进 modified/（无 git 无法生成 diff）
- 文件删除时由 Step 3 删除对齐处理

**排除规则**：编译产物（`*.o` `*.ko` `Image` `*.dtb`）、构建缓存（`out/` `prebuilts/`）、上游未改动文件均不归档。

详细规则与路径映射见脚本内注释和历史设计文档。

## README.md 格式规范（AI 维护目标）

README.md 包含概述、目录结构、文件映射表、回写命令等章节。
**一键更新时仅修改"文件映射表"章节**，其余章节（含"目录结构"）保持不变：

| 章节 | 内容 | 来源 | 一键更新时 |
|------|------|------|-----------|
| 概述 + 包含的特性 | 高层语义描述 | AI/人工 | 不变 |
| 目录结构 | 树状结构图 | 历史/人工 | 不变（保留） |
| 文件映射表 | 分组表格 | AI 从 manifest 渲染前两列 + 读取 diff 填描述 | ✅ 唯一更新对象 |
| 回写命令 | 静态部署命令 | 不变 | 不变 |

### 文件映射表格式

按 `kernel/modified`、`kernel/new`、`aosp/modified`、`aosp/new`、`others` 分组，
每个文件一行，列：`patch 路径 | workspace 源码路径 | 改动要点`。
patch 路径与 workspace 源码路径严格来自 manifest.yaml，AI 不得臆造。
改动要点为空时留空，待后续补充。

### 首次迁移

首次按本格式重写 README.md 时，现有 README 中已有的"改动要点/说明"
必须迁移到新表格对应行，不得丢失。
