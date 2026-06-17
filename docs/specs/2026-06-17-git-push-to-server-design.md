# git-push-to-server 设计

> **版本**：v1（2026-06-17）
> **目标**：提供 `/git-push-to-server` 命令，一键完成"收集 diff → AI 生成中文 type commit message → 单次确认 → 提交并推送"，解决手动写 message 和推送的繁琐。

## 背景与问题

当前项目频繁向 GitHub `origin` 推送改动，但存在两个痛点：

1. **commit message 质量低**：历史提交大量 `update`，丢失语义信息，后续 revert/changelog 难以追溯。
2. **提交流程繁琐**：每次手动 `git add` + 想 message + `git push` 三步，节奏被打断。

本 skill 用 AI 生成规范化 message + 单次确认机制，在保证质量的同时保持速度。

## 设计决策（brainstorming 确认结果）

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 提交粒度 | **单条提交**（`git add -A`） | 痛点是 message 质量而非提交粒度，分组提交复杂度高且违背"快速"初衷 |
| 确认机制 | **单次确认**（message + 分支） | 拦截 AI 误判，挡住"推错分支"，不过度拖慢节奏 |
| 确认交互 | **支持多轮编辑**（y / n / 文字描述修改意见） | AI 工具特性，对话式修改比弹编辑器更顺 |
| diff 来源 | **所有改动**（含 staged + unstaged + untracked） | 与 `git add -A` 一致，一致性最好 |
| 推送分支 | **默认当前分支到 origin** | 可修改，覆盖 90% 场景 |
| type 语言 | **中文** | 可读性优先，放弃 Conventional Commits 工具链兼容性 |
| scope 选取 | **目录+模块**（改动行数最多目录为准） | 机械可判 + 语义清晰，无歧义 |
| 边界：push 失败 | **保留 commit，手动处理** | 自动回退有风险，保留现场让用户判断 |
| 边界：force push | **禁止** | 防止历史覆盖 |
| 边界：--amend | **本期不做** | 与"禁止 force push"冲突，YAGNI |
| 边界：scope 无模块 | **用 `unknown` 兜底** | 避免与真实 `common` 目录冲突 |

## 架构：脚本+AI 混合（与现有 skill 一致）

```
/git-push-to-server [参数]
   │
   ▼
[collect_diff.sh]      收集 git status + diff + 分支信息，格式化输出
   │
   ▼
[AI]                   读 collect 输出，按规范生成中文 type message
   │                   (diff 过大时基于 --stat + 文件摘要)
   ▼
[AI]                   展示 type/scope/subject/body + 分支 → 等待确认
   │
   ├─ y ──────────────→ [commit_and_push.sh]  git add -A + commit -F + push → 完成
   ├─ n ──────────────→ 取消，不 commit
   └─ 编辑意见 ───────→ AI 修改 message → 重新展示（多轮）
```

**分工原则**（与 `sync-code-to-patchs` 完全一致）：
- **脚本做机械工作**：diff 收集、git add/commit/push 执行
- **AI 做语义工作**：理解 diff、生成 message、多轮编辑交互

## 文件结构

```
skills/git-push-to-server/
├── SKILL.md                  # AI 工作流：message 规范 + 确认流程
├── collect_diff.sh           # 模式1：收集 diff + 分支信息，格式化输出
└── commit_and_push.sh        # 模式2：git add -A + commit -F + push
.opencode/commands/
└── git-push-to-server.md     # 中转壳子
```

**为什么拆两个脚本**：collect（读操作，可能输出超大）与 commit（写操作，紧凑）职责清晰，独立维护互不干扰。

**为什么 message 走 `--message-file` 不走 `-m`**：message 含多行 body + 特殊字符，`-m` 转义易出错；由 AI 写临时文件、脚本用 `git commit -F` 读取最稳。

## collect_diff.sh 接口

```bash
bash skills/git-push-to-server/collect_diff.sh [--stat-only]
```

**行为**：
- 无参数：输出当前分支、远程、git status、`--stat`、完整 diff
- `--stat-only`：只输出分支 + status + `--stat`，不输出 diff 正文（快速预览场景）
- 无改动：输出 `nothing to commit` 并退出码 1（AI 见此停止流程）

**输出格式**：
```
========== GIT PUSH CONTEXT ==========
当前分支: main
远程:     origin (git@github.com:lechao365/AndroidSystemEnhance.git)

========== 改动文件 (git status) ==========
 M skills/sync-code-to-patchs/SKILL.md
 M rules/source-code-priority.md
?? docs/specs/xxx.md

========== 改动统计 (--stat) ==========
 skills/.../SKILL.md  | 71 +++++++---
 rules/...            | 12 +--
 2 files changed, 60 insertions(+), 23 deletions(-)

========== DIFF 内容 ==========
<完整 diff>
======================================
```

**diff 过大处理**（>50 文件或 >5000 行）：自动降级为 `--stat` + 每个文件前 20 行 diff 摘要，末尾追加提示：
```
⚠ diff 已截断（超过阈值），AI 基于 stat + 文件摘要生成 message
```

## commit_and_push.sh 接口

```bash
bash skills/git-push-to-server/commit_and_push.sh \
    --message-file <path> \      # message 文本（git commit -F 读取）
    [--branch <branch>] \        # 默认当前分支
    [--remote origin] \          # 默认 origin
    [--no-push]                  # 只 commit 不 push
```

**行为**：
1. 校验 `--message-file` 存在且非空
2. `git add -A`
3. `git commit -F <message-file>`
4. 非 `--no-push` 时：`git push <remote> <branch>`
5. push 失败：**保留 commit，报错退出**（用户手动处理，如 `git push --force-with-lease` 或 `git pull --rebase`）
6. 输出结果摘要（commit hash、推送结果）

**强制约束**：
- 禁止 force push（脚本不提供该能力）
- push 失败不自动 `git reset`（保留现场）

## commit message 规范

### 格式

```
<中文type>(<scope>): <subject>

<body bullet 列表>
```

### 中文 type 词表

| type | 含义 | 典型场景 |
|------|------|---------|
| 新增 | 新功能/新特性 | 新增 lcview 字段、新增 iod 模块 |
| 修复 | bug 修复 | 修复打点崩溃 |
| 重构 | 不改行为的结构调整 | 脚本结构调整 |
| 文档 | 文档类改动 | specs、README、设计文档 |
| 杂项 | 工具/配置/脚本类 | skill、command、rules |
| 构建 | 构建系统改动 | mk_rpi5_full_image.sh、Android.bp |

### scope 词表（目录+模块）

| 目录 | 模块识别规则 | scope |
|------|------------|-------|
| `kernel/` 下 `vendor/lechao/LcView/**` | 路径含 `LcView` | `kernel-lcview` |
| `kernel/` 下 `vendor/lechao/LcIod/**` | 路径含 `LcIod` | `kernel-lciod` |
| `kernel/` 其他 | 无明确模块 | `kernel-unknown` |
| `aosp/` 下涉及 lcview/lciod | grep 文件名/路径 | `aosp-lcview` / `aosp-lciod` |
| `aosp/` 其他 | 无明确模块 | `aosp-unknown` |
| `docs/` | 固定 | `docs` |
| `skills/` | 固定 | `skills` |
| `rules/` | 固定 | `rules` |
| `scripts/` | 固定 | `scripts` |
| `.opencode/` | 固定 | `tooling` |
| 未命中 | 兜底 | `misc` |

### 选取规则

- **scope**：改动行数最多的目录 + 模块
- **body**：bullet 列每个**有改动的目录**及摘要；`docs` 无改动则不列（避免无信息条目）
- **subject**：精炼描述主要改动，中文

### 示例

```
新增(skills): sync-code-to-patchs 支持删除对齐

- skills: 新增 sync_prune 函数实现 patchs 删除对齐
- rules: 更新 source-code-priority.md 镜像规则说明
```

## 确认界面（AI 展示）

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

**交互**：
- `y` → 调 `commit_and_push.sh` 执行 commit + push
- `n` → 取消，不 commit
- 文字描述修改意见（如 `scope 改成 tooling，body 第二条删掉`）→ AI 改完**重新展示**，再次确认（可多轮）

## 参数清单

| 参数 | 说明 |
|------|------|
| 无参数 | 完整流程：collect → 生成 → 确认 → commit + push |
| `--dry-run` | 只 collect + 生成 message 展示，不 commit 不 push |
| `--no-push` | 确认后只 commit 不 push |
| `--branch <b>` | 指定推送分支（默认当前分支） |
| `--remote <r>` | 指定远程（默认 origin） |

## 边界处理汇总

| 场景 | 处理 |
|------|------|
| 无改动 | collect_diff.sh 输出 `nothing to commit` + 退出码 1，AI 停止流程 |
| diff 过大（>50 文件或 >5000 行） | collect 自动降级为 --stat + 每文件前 20 行摘要 |
| push 失败（网络/权限/non-fast-forward） | 保留 commit，报错退出，用户手动处理（不自动回退） |
| AI 生成失败 | 停下提示用户手动写 message |
| force push | 禁止（脚本不提供该能力） |
| 排除项（node_modules 等） | 依赖 `.gitignore`，脚本不重复造轮子 |

## 验证方式

| 场景 | 验证方法 |
|------|---------|
| message 质量 | 用当前工作区真实改动跑 `--dry-run`，人工检查 type/scope/subject/body |
| 无改动 | `git stash` 后跑，确认 collect 报错退出 |
| 大 diff | 临时改 60 个文件跑，确认降级为摘要输出 |
| push 失败处理 | 断网或推到只读分支，确认 commit 保留不回退 |
| 多轮编辑 | 确认界面给修改意见，确认 AI 改完重新展示 |

## 不做的事（YAGNI）

- **不做 hunk 级分组提交**（违背"快速"初衷，复杂度高）
- **不做 `--amend`**（与"禁止 force push"冲突）
- **不做 force push**（防止历史覆盖）
- **不做 PR 创建**（仅负责 commit + push，PR 由用户或独立工具处理）
- **不重复 .gitignore 逻辑**（依赖 git 自身规则）
