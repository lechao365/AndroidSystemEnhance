---
name: git-push-to-server
description: 收集 diff → AI 生成中文 type commit message → 单次确认（支持多轮编辑）→ 提交并推送到 origin。
---

# git-push-to-server

一键完成"收集 diff → AI 生成规范化 commit message → 单次确认 → 提交并推送"，解决手动写 message 和推送的繁琐。

**核心语义**：脚本做机械工作（diff 收集、git add/commit/push），AI 做语义工作（理解 diff、生成 message、多轮编辑交互）。

## 工作流

### 1. 收集 diff（脚本）

```bash
bash engineering/harness/workflows/git-push-to-server/collect_diff.sh              # 完整输出（status + stat + diff）
bash engineering/harness/workflows/git-push-to-server/collect_diff.sh --stat-only  # 仅 status + stat，跳过 diff 正文
```

脚本输出当前分支、远程、git status、改动统计、diff 正文。无改动时输出 `nothing to commit` 并退出码 4（无操作，非错误），AI 见此**停止流程**。

**空仓库/全 untracked 场景**：脚本无论 HEAD 是否存在，都会收集 `git ls-files --others --exclude-standard` 作为 untracked 列表，并输出每个新文件的完整内容（或大 diff 降级时输出前 20 行）。因此新仓初始化场景下 AI 也能拿到足够上下文生成 commit message。空仓库时 stat 段会输出简化版并标注"空仓库，无 upstream base"。

**大 diff 降级**（>50 文件或 >5000 行）：脚本自动改为输出 `--stat` + 每个文件前 20 行摘要，末尾提示"diff 已截断"。AI 基于摘要生成 message。

### 2. 生成 message（AI，严格按规范）

读取 collect 输出，按下列规范生成 message。

#### 格式

```
<中文type>(<scope>): <subject>

<body bullet 列表>
```

#### 中文 type 词表

| type | 含义 | 典型场景 |
|------|------|---------|
| 新增 | 新功能/新特性 | 新增 lcview 字段、新增 iod 模块 |
| 修复 | bug 修复 | 修复打点崩溃 |
| 重构 | 不改行为的结构调整 | 脚本结构调整 |
| 文档 | 文档类改动 | specs、README、设计文档 |
| 杂项 | 工具/配置/脚本类 | skill、command、rules |
| 构建 | 构建系统改动 | mk_rpi5_full_image.sh、Android.bp |

#### scope 词表（目录+模块，改动行数最多目录为准）

> 完整映射表已抽出至独立配置，新增目录只改配置不动 workflow。

详见 [scope 映射表](../../config/scope-mapping.md)

#### 选取规则

- **type**：按改动主体语义选（新功能→新增、修 bug→修复、结构调整→重构、文档→文档、工具脚本→杂项、构建系统→构建）
- **scope**：改动行数最多的目录 + 模块
- **subject**：精炼描述主要改动，中文，不加句号
- **body**：bullet 列每个**有改动的目录**及摘要；`docs` 无改动则不列（避免无信息条目）

#### 示例

```
新增(skills): sync-code-to-patchs 支持删除对齐

- skills: 新增 sync_prune 函数实现 patchs 删除对齐
- rules: 更新 source-code-modify.md 镜像规则说明
```

### 3. 单次确认（AI 展示，支持多轮编辑）

#### 强制约束（不可跳过）

无论用户在何时、以何种措辞表达"提交"或"推送"意图（如"同意提交""可以提交""提交吧"），AI 都**必须**：

1. 先完成 collect diff（第 1 步）
2. 生成 commit message（第 2 步）
3. 展示完整 message 预览（本步）
4. **等待用户对该 message 的显式确认**（`y` / `n` / 修改意见）

**禁止**将用户在 collect 之前的任何"同意"视为对后续 message 的确认。
**禁止**跳过第 3 步直接调用 `commit_and_push.sh`。

#### 展示格式

（**只展示 type/scope/subject/body + 分支，不重复完整 message**）：

```
────────── 提交预览 ──────────
type:    新增
scope:   skills
subject: sync-code-to-patchs 支持删除对齐

body:
  - skills: 新增 sync_prune 函数实现删除对齐
  - rules: 更新镜像规则说明

分支: main → origin/main
──────────────────────────────
确认？(y 确认 / n 取消 / 或说明要改的地方)
```

#### 交互分支

| 用户输入 | AI 行为 |
|----------|---------|
| `y` | 调 `commit_and_push.sh` 执行 commit + push |
| `n` | 取消，不 commit |
| 文字描述修改意见（如 `scope 改成 tooling，body 第二条删掉`） | AI 按意见改 message，**重新展示**，再次确认（可多轮） |

### 4. 执行提交推送（脚本）

用户确认后，AI 将最终 message 写入临时文件，调脚本：

```bash
bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh \
    --message-file <临时文件> \
    [--branch <分支>] \
    [--remote origin] \
    [--no-push]
```

脚本行为：`git add -A` → `git commit -F` → `git push <remote> <branch>`。

**push 失败处理**：脚本保留 commit 不回退（退出码 2），提示用户手动处理（`git push` 重试 / `git pull --rebase` / `git reset --soft HEAD~1` 回退）。**禁止 force push**。

## 参数清单

| 参数 | 说明 |
|------|------|
| 无参数 | 完整流程：collect → 生成 → 确认 → commit + push |
| `--dry-run` | 只 collect + 生成 message 展示，不 commit 不 push |
| `--no-push` | 确认后只 commit 不 push |
| `--branch <b>` | 指定推送分支（默认当前分支） |
| `--remote <r>` | 指定远程（默认 origin） |

> 注：`--dry-run` 由 AI 在工作流层处理（collect 后不进入 commit 步骤）；`--no-push` / `--branch` / `--remote` 透传给 `commit_and_push.sh`。

## 边界处理

| 场景 | 处理 |
|------|------|
| 无改动 | collect_diff.sh 输出 `nothing to commit` + 退出码 4（无操作），AI 停止流程 |
| diff 过大（>50 文件或 >5000 行） | collect 自动降级为 --stat + 每文件前 20 行摘要 |
| push 失败 | 保留 commit，脚本退出码 2，提示手动处理（不自动回退） |
| AI 生成失败 | 停下提示用户手动写 message |
| force push | 禁止（脚本不提供该能力） |
| 排除项（node_modules 等） | 依赖 `.gitignore`，脚本不重复造轮子 |

## 不做的事（YAGNI）

- 不做 hunk 级分组提交（违背"快速"初衷）
- 不做 `--amend`（与"禁止 force push"冲突）
- 不做 force push（防止历史覆盖）
- 不做 PR 创建（仅负责 commit + push）
