# Harness 工程目录重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `rules/`、`skills/`、`scripts/`、`templates/` 重构进 `engineering/harness/`，重命名 skills→workflows，分离框架与数据配置。

**Architecture:** 方案 A——`git mv` 保留历史 + 集中改引用 + 抽取数据配置到 config/。原子单 commit，迁移完成后用 `/git-push-to-server` 自验证。

**Tech Stack:** bash/git mv/edit 工具，零依赖。

**Spec:** `docs/specs/2026-06-18-harness-restructure-design.md`

---

## File Structure

迁移后的目标结构（spec 第 2 节）：

```
engineering/harness/
├── workflows/{sync-code-to-patchs,git-push-to-server,sync-patchs-to-doc}/
├── rules/       (4 个纯规则)
├── config/      (doc-sync-mapping.md + scope-mapping.md[新])
├── scripts/     (mk_rpi5_full_image.sh)
└── templates/   (2 个文档模板)
```

**本计划共 7 个任务**：
- Task 1：建目录 + 15 个 git mv（含 SKILL.md→WORKFLOW.md 改名）
- Task 2：新建 config/scope-mapping.md
- Task 3：改 AGENTS.md + 3 个 commands（4 文件）
- Task 4：改 4 个脚本头部注释（4 文件）
- Task 5：改 3 个 WORKFLOW.md（含 scope 词表抽取）
- Task 6：改 config/doc-sync-mapping.md
- Task 7：静态验证 + 功能验证

> Task 1 必须先完成（串行），Task 2-6 可并行（文件集不重叠），Task 7 最后执行。

---

## Task 1: 建目录 + git mv 文件迁移

**Files:**
- Create dir: `engineering/harness/{workflows,rules,config,scripts,templates}`
- Move: 15 个文件（见下方命令）

- [ ] **Step 1: 建 harness 子目录**

```bash
mkdir -p engineering/harness/workflows/sync-code-to-patchs \
         engineering/harness/workflows/git-push-to-server \
         engineering/harness/workflows/sync-patchs-to-doc \
         engineering/harness/rules \
         engineering/harness/config \
         engineering/harness/scripts \
         engineering/harness/templates
```

验证：`ls -d engineering/harness/*/` 期望输出 7 个子目录。

- [ ] **Step 2: git mv workflows（7 文件，含 SKILL.md→WORKFLOW.md 改名）**

注意 WSL/Windows 大小写不敏感问题，SKILL.md→WORKFLOW.md 用两步 mv（先改临时名再改目标名）规避：

```bash
# sync-code-to-patchs
git mv skills/sync-code-to-patchs/sync_code_to_patchs.sh engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh
git mv skills/sync-code-to-patchs/SKILL.md skills/sync-code-to-patchs/SKILL.md.tmp
git mv skills/sync-code-to-patchs/SKILL.md.tmp engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md

# git-push-to-server
git mv skills/git-push-to-server/collect_diff.sh engineering/harness/workflows/git-push-to-server/collect_diff.sh
git mv skills/git-push-to-server/commit_and_push.sh engineering/harness/workflows/git-push-to-server/commit_and_push.sh
git mv skills/git-push-to-server/SKILL.md skills/git-push-to-server/SKILL.md.tmp
git mv skills/git-push-to-server/SKILL.md.tmp engineering/harness/workflows/git-push-to-server/WORKFLOW.md

# sync-patchs-to-doc
git mv skills/sync-patchs-to-doc/sync_patchs_to_doc.sh engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh
git mv skills/sync-patchs-to-doc/SKILL.md skills/sync-patchs-to-doc/SKILL.md.tmp
git mv skills/sync-patchs-to-doc/SKILL.md.tmp engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md
```

- [ ] **Step 3: git mv rules（4 规则 + 1 配置）**

```bash
git mv rules/source-code-priority.md engineering/harness/rules/source-code-priority.md
git mv rules/parallel-strategy.md engineering/harness/rules/parallel-strategy.md
git mv rules/doc-paths.md engineering/harness/rules/doc-paths.md
git mv rules/plantuml.md engineering/harness/rules/plantuml.md
# doc-sync-mapping.md 是配置，迁到 config/
git mv rules/doc-sync-mapping.md engineering/harness/config/doc-sync-mapping.md
```

- [ ] **Step 4: git mv scripts + templates（3 文件）**

```bash
git mv scripts/mk_rpi5_full_image.sh engineering/harness/scripts/mk_rpi5_full_image.sh
git mv templates/module-template.md engineering/harness/templates/module-template.md
git mv templates/module-readme-template.md engineering/harness/templates/module-readme-template.md
```

- [ ] **Step 5: 清理空旧目录 + 验证**

```bash
# git mv 后旧目录应已空，清理残留空目录
rmdir skills/sync-code-to-patchs skills/git-push-to-server skills/sync-patchs-to-doc 2>/dev/null
rmdir skills rules scripts templates 2>/dev/null
# git status 应显示纯 rename，无 untracked
git status --short
```

期望：`git status --short` 全部为 `R`（rename）行，共 15 条，无 `??` 或 `D` 行。

- [ ] **Step 6: 验证脚本可执行权限保留**

```bash
ls -l engineering/harness/workflows/*/*.sh engineering/harness/scripts/*.sh
```

期望：所有 .sh 为 `-rwxrwxrwx`（与原状态一致）。

---

## Task 2: 新建 config/scope-mapping.md

**Files:**
- Create: `engineering/harness/config/scope-mapping.md`

内容来源：从 `git-push-to-server/WORKFLOW.md` L48-62 抽取 scope 词表，更新路径为新 harness 路径。

- [ ] **Step 1: 创建 scope-mapping.md**

文件内容：

```markdown
# Git Commit Scope 映射

> **用途**：`git-push-to-server` workflow 依据本表，按改动目录识别 scope。
> 新增工程目录时只需更新本文件，无需修改 workflow。

## scope 判定规则

按改动行数最多目录为准，自上而下首条命中即归属：

| 目录特征 | 模块识别规则 | scope |
|---------|------------|-------|
| `kernel/` 下 `vendor/lechao/LcView/**` | 路径含 `LcView` | `kernel-lcview` |
| `kernel/` 下 `vendor/lechao/LcIod/**` | 路径含 `LcIod` | `kernel-lciod` |
| `kernel/` 其他 | 无明确模块 | `kernel-unknown` |
| `aosp/` 下涉及 lcview/lciod | grep 文件名/路径 | `aosp-lcview` / `aosp-lciod` |
| `aosp/` 其他 | 无明确模块 | `aosp-unknown` |
| `engineering/harness/workflows/` | 固定 | `workflows` |
| `engineering/harness/rules/` | 固定 | `rules` |
| `engineering/harness/config/` | 固定 | `config` |
| `engineering/harness/scripts/` | 固定 | `scripts` |
| `engineering/harness/templates/` | 固定 | `templates` |
| `docs/` | 固定 | `docs` |
| `.opencode/` | 固定 | `tooling` |
| 未命中 | 兜底 | `misc` |
```

> 注：`kernel/`、`aosp/` 行保留原样（指 patchs 内路径，不受本次迁移影响）。原 `skills/`→`workflows`，新增 `config/`、`templates/` 行。

- [ ] **Step 2: 验证文件存在且内容完整**

```bash
test -f engineering/harness/config/scope-mapping.md && echo OK
```

期望：输出 `OK`。

---

## Task 3: 改 AGENTS.md + 3 个 commands（引用更新）

**Files:**
- Modify: `AGENTS.md`（4 处 rules 路径）
- Modify: `.opencode/commands/sync-code-to-patchs.md`（2 处）
- Modify: `.opencode/commands/git-push-to-server.md`（2 处）
- Modify: `.opencode/commands/sync-patchs-to-doc.md`（2 处）

- [ ] **Step 1: 改 AGENTS.md（4 处）**

将以下 4 行中的 `rules/` 前缀全部加 `engineering/harness/`：

| 行号 | 旧 | 新 |
|------|----|----|
| L6 | `[rules/source-code-priority.md](rules/source-code-priority.md)` | `[engineering/harness/rules/source-code-priority.md](engineering/harness/rules/source-code-priority.md)` |
| L11 | `[rules/parallel-strategy.md](rules/parallel-strategy.md)` | `[engineering/harness/rules/parallel-strategy.md](engineering/harness/rules/parallel-strategy.md)` |
| L15 | `[rules/plantuml.md](rules/plantuml.md)` | `[engineering/harness/rules/plantuml.md](engineering/harness/rules/plantuml.md)` |
| L24 | `[rules/doc-paths.md](rules/doc-paths.md)` | `[engineering/harness/rules/doc-paths.md](engineering/harness/rules/doc-paths.md)` |

- [ ] **Step 2: 改 .opencode/commands/sync-code-to-patchs.md**

```
旧 L5: !`bash skills/sync-code-to-patchs/sync_code_to_patchs.sh $ARGUMENTS`
新 L5: !`bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh $ARGUMENTS`

旧 L8: @skills/sync-code-to-patchs/SKILL.md
新 L8: @engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md
```

- [ ] **Step 3: 改 .opencode/commands/git-push-to-server.md**

```
旧 L5: !`bash skills/git-push-to-server/collect_diff.sh`
新 L5: !`bash engineering/harness/workflows/git-push-to-server/collect_diff.sh`

旧 L8: @skills/git-push-to-server/SKILL.md
新 L8: @engineering/harness/workflows/git-push-to-server/WORKFLOW.md
```

- [ ] **Step 4: 改 .opencode/commands/sync-patchs-to-doc.md**

```
旧 L5: !`bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh $ARGUMENTS`
新 L5: !`bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh $ARGUMENTS`

旧 L8: @skills/sync-patchs-to-doc/SKILL.md
新 L8: @engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md
```

- [ ] **Step 5: 验证无残留**

```bash
grep -rn "skills/\|SKILL\.md" AGENTS.md .opencode/commands/
```

期望：0 命中（无输出）。

---

## Task 4: 改 4 个脚本头部注释（引用更新）

**Files:**
- Modify: `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh`（L6, L7, L81）
- Modify: `engineering/harness/workflows/git-push-to-server/collect_diff.sh`（L6, L7, L40）
- Modify: `engineering/harness/workflows/git-push-to-server/commit_and_push.sh`（L6, L7, L47）
- Modify: `engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh`（L6, L7, L37, L212）

- [ ] **Step 1: 改 sync_code_to_patchs.sh**

```
旧 L6: # 规则详见: skills/sync-code-to-patchs/SKILL.md
新 L6: # 规则详见: engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md

旧 L7: # 用法:    bash skills/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]
新 L7: # 用法:    bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]

旧 L81:             echo "Usage: bash skills/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]"
新 L81:             echo "Usage: bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]"
```

- [ ] **Step 2: 改 collect_diff.sh**

```
旧 L6: # 规则详见: skills/git-push-to-server/SKILL.md
新 L6: # 规则详见: engineering/harness/workflows/git-push-to-server/WORKFLOW.md

旧 L7: # 用法:    bash skills/git-push-to-server/collect_diff.sh [--stat-only]
新 L7: # 用法:    bash engineering/harness/workflows/git-push-to-server/collect_diff.sh [--stat-only]

旧 L40:             echo "Usage: bash skills/git-push-to-server/collect_diff.sh [--stat-only]"
新 L40:             echo "Usage: bash engineering/harness/workflows/git-push-to-server/collect_diff.sh [--stat-only]"
```

- [ ] **Step 3: 改 commit_and_push.sh**

```
旧 L6: # 规则详见: skills/git-push-to-server/SKILL.md
新 L6: # 规则详见: engineering/harness/workflows/git-push-to-server/WORKFLOW.md

旧 L7: # 用法:    bash skills/git-push-to-server/commit_and_push.sh \
新 L7: # 用法:    bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh \

旧 L47:             echo "Usage: bash skills/git-push-to-server/commit_and_push.sh --message-file <path> [--branch <b>] [--remote origin] [--no-push]"
新 L47:             echo "Usage: bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh --message-file <path> [--branch <b>] [--remote origin] [--no-push]"
```

- [ ] **Step 4: 改 sync_patchs_to_doc.sh**

```
旧 L6: # 规则详见: skills/sync-patchs-to-doc/SKILL.md
新 L6: # 规则详见: engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md

旧 L7: # 用法:    bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only] [--full-diff]
新 L7: # 用法:    bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only] [--full-diff]

旧 L37:             echo "Usage: bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only] [--full-diff]"
新 L37:             echo "Usage: bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only] [--full-diff]"

旧 L212:   ② 依据 rules/doc-sync-mapping.md 将变动分发到对应文档目录（01/02）
新 L212:   ② 依据 engineering/harness/config/doc-sync-mapping.md 将变动分发到对应文档目录（01/02）
```

- [ ] **Step 5: 验证脚本内无残留旧路径**

```bash
grep -rn "skills/\|SKILL\.md\|rules/doc-sync-mapping" engineering/harness/workflows/*/*.sh
```

期望：0 命中。

---

## Task 5: 改 3 个 WORKFLOW.md（引用 + scope 抽取）

**Files:**
- Modify: `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md`
- Modify: `engineering/harness/workflows/git-push-to-server/WORKFLOW.md`
- Modify: `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md`

> **相对路径说明**：`workflows/xxx/WORKFLOW.md` 的 `../../rules/` 上溯两级到 `harness/`（与原 `skills/xxx/SKILL.md` 上溯两级到项目根层级相同），故指向 rules 的相对引用无需改。仅需改指向 config/templates 的引用 + 用法示例的绝对路径。

- [ ] **Step 1: 改 sync-code-to-patchs/WORKFLOW.md（3 处用法示例）**

```
旧 L24: bash skills/sync-code-to-patchs/sync_code_to_patchs.sh              # 全量镜像同步（默认含删除）
新 L24: bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh              # 全量镜像同步（默认含删除）

旧 L25: bash skills/sync-code-to-patchs/sync_code_to_patchs.sh --check-only  # 仅检查，不执行（STALE 仅报告将删除项）
新 L25: bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --check-only  # 仅检查，不执行（STALE 仅报告将删除项）

旧 L26: bash skills/sync-code-to-patchs/sync_code_to_patchs.sh --no-prune    # 仅添加/更新，不删除对齐
新 L26: bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --no-prune    # 仅添加/更新，不删除对齐
```

> L19 `../../rules/source-code-priority.md` 不变（层级巧合相同）。

- [ ] **Step 2: 改 git-push-to-server/WORKFLOW.md（用法示例 + scope 词表抽取）**

用法示例（L17-18, L112）：

```
旧 L17: bash skills/git-push-to-server/collect_diff.sh              # 完整输出（status + stat + diff）
新 L17: bash engineering/harness/workflows/git-push-to-server/collect_diff.sh              # 完整输出（status + stat + diff）

旧 L18: bash skills/git-push-to-server/collect_diff.sh --stat-only  # 仅 status + stat，跳过 diff 正文
新 L18: bash engineering/harness/workflows/git-push-to-server/collect_diff.sh --stat-only  # 仅 status + stat，跳过 diff 正文

旧 L112: bash skills/git-push-to-server/commit_and_push.sh \
新 L112: bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh \
```

scope 词表抽取（L48-62）：将整个 `#### scope 词表` 小节（含表格 L48-62）替换为引用：

```
旧 L48-62:
#### scope 词表（目录+模块，改动行数最多目录为准）

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

新 L48-62:
#### scope 词表（目录+模块，改动行数最多目录为准）

> 完整映射表已抽出至独立配置，新增目录只改配置不动 workflow。

详见 [scope 映射表](../../config/scope-mapping.yaml)
```

- [ ] **Step 3: 改 sync-patchs-to-doc/WORKFLOW.md（8 处引用）**

```
旧 L10: **核心理念**：`templates/*.md` 是**只读契约**...
新 L10: **核心理念**：`engineering/harness/templates/*.md` 是**只读契约**...

旧 L23: bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh              # 生成变动报告
新 L23: bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh              # 生成变动报告

旧 L24: bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh --full-diff  # 报告 + 完整 diff 正文（AI 零往返）
新 L24: bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh --full-diff  # 报告 + 完整 diff 正文（AI 零往返）

旧 L25: bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only  # 仅检查，不输出提示
新 L25: bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only  # 仅检查，不输出提示

旧 L32: - **精确匹配**：...必须依据 [patchs→文档映射规则](../../rules/doc-sync-mapping.md)：
新 L32: - **精确匹配**：...必须依据 [patchs→文档映射规则](../../config/doc-sync-mapping.yaml)：

旧 L108: | `UPDATE-图` | 重画 PlantUML（遵守 `rules/plantuml.md`）|
新 L108: | `UPDATE-图` | 重画 PlantUML（遵守 `engineering/harness/rules/plantuml.md`）|

旧 L129: │  据: 结构体新增字段（⚠PlantUML须遵守 rules/plantuml.md）
新 L129: │  据: 结构体新增字段（⚠PlantUML须遵守 engineering/harness/rules/plantuml.md）

旧 L150: - PlantUML 改动后必须过 `rules/plantuml.md` 约束
新 L150: - PlantUML 改动后必须过 `engineering/harness/rules/plantuml.md` 约束

旧 L173: | 映射驱动 | 依据 `rules/doc-sync-mapping.md` 分发 |
新 L173: | 映射驱动 | 依据 `engineering/harness/config/doc-sync-mapping.md` 分发 |

旧 L174: | 模板只读 | `templates/*.md` 不可擅改；冲突需确认（`TEMPLATE-CONFLICT`）|
新 L174: | 模板只读 | `engineering/harness/templates/*.md` 不可擅改；冲突需确认（`TEMPLATE-CONFLICT`）|
```

- [ ] **Step 4: 验证 WORKFLOW.md 内无残留旧路径**

```bash
grep -rn "skills/\|SKILL\.md\|^[^/]rules/\| rules/" engineering/harness/workflows/*/WORKFLOW.md | grep -v "engineering/harness"
```

期望：0 命中（即所有 rules/ 引用都已加 `engineering/harness/` 前缀，或保持 `../../rules/` 相对路径不变）。

---

## Task 6: 改 config/doc-sync-mapping.md

**Files:**
- Modify: `engineering/harness/config/doc-sync-mapping.md`（L4）

- [ ] **Step 1: 改 L4 目录名提及**

```
旧 L4: > 新增特性时只需更新本文件，无需修改 `skills/` 工作流。
新 L4: > 新增特性时只需更新本文件，无需修改 `workflows/` 工作流。
```

- [ ] **Step 2: 验证**

```bash
grep "skills/" engineering/harness/config/doc-sync-mapping.md
```

期望：0 命中。

---

## Task 7: 静态验证 + 功能验证

**Files:** 无文件改动，纯验证。

- [ ] **Step 1: 旧路径残留全局扫描**

扫描除历史文档（docs/specs/、docs/plans/、01-*/02-*/）外的所有文件：

```bash
grep -rn "skills/\|SKILL\.md" \
  --include="*.md" --include="*.sh" \
  AGENTS.md .opencode/ engineering/ \
  | grep -v "docs/specs/\|docs/plans/\|历史"
```

期望：0 命中。

- [ ] **Step 2: commands 引用可达性验证**

```bash
# 验证 @ 引用的 WORKFLOW.md 文件存在
test -f engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md && echo "sync-code OK"
test -f engineering/harness/workflows/git-push-to-server/WORKFLOW.md && echo "git-push OK"
test -f engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md && echo "sync-patchs OK"
# 验证 bash 引用的 .sh 文件存在
test -x engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh && echo "sync-code sh OK"
test -x engineering/harness/workflows/git-push-to-server/collect_diff.sh && echo "collect sh OK"
test -x engineering/harness/workflows/git-push-to-server/commit_and_push.sh && echo "commit sh OK"
test -x engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh && echo "sync-patchs sh OK"
```

期望：7 行 OK 输出。

- [ ] **Step 3: WORKFLOW.md 内部相对链接可达性**

```bash
# 从 workflows/sync-patchs-to-doc/ 视角验证
test -f engineering/harness/config/doc-sync-mapping.md && echo "config/doc-sync OK"
test -f engineering/harness/config/scope-mapping.md && echo "config/scope OK"
test -f engineering/harness/rules/plantuml.md && echo "rules/plantuml OK"
test -f engineering/harness/templates/module-template.md && echo "templates/module OK"
```

期望：4 行 OK 输出。

- [ ] **Step 4: AGENTS.md 链接可达性**

```bash
test -f engineering/harness/rules/source-code-priority.md && echo "L6 OK"
test -f engineering/harness/rules/parallel-strategy.md && echo "L11 OK"
test -f engineering/harness/rules/plantuml.md && echo "L15 OK"
test -f engineering/harness/rules/doc-paths.md && echo "L24 OK"
```

期望：4 行 OK 输出。

- [ ] **Step 5: 功能验证——脚本 --help 路径正确**

```bash
bash engineering/harness/workflows/git-push-to-server/collect_diff.sh --help 2>&1 | grep "engineering/harness"
```

期望：输出含 `engineering/harness/workflows/git-push-to-server/collect_diff.sh` 的 Usage 行。

- [ ] **Step 6: 功能验证——sync-code-to-patchs --check-only**

```bash
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --check-only 2>&1 | tail -5
```

期望：正常执行（输出 OK/MISS/SKIP/PRUNE 状态行或无改动提示），Usage 行（若有）显示新路径。

- [ ] **Step 7: 功能验证——sync-patchs-to-doc --check-only**

```bash
bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only 2>&1 | tail -5
```

期望：正常执行或提示无变动，Usage 行显示新路径。

- [ ] **Step 8: git status 最终确认**

```bash
git status --short
```

期望：显示所有 R（rename）和 M（modified）改动，无遗漏的 untracked 或意外删除。

- [ ] **Step 9: 移交用户用 /git-push-to-server 提交**

告知用户：静态与功能验证全部通过，请执行 `/git-push-to-server` 命令提交本次迁移（兼做 git-push workflow 自身端到端验证）。

---

## Self-Review

### 1. Spec coverage 核对

| Spec 章节 | 对应 Task | ✓ |
|-----------|----------|---|
| §2 目标目录结构 | Task 1 (建目录) | ✓ |
| §3.1-3.5 文件迁移 | Task 1 (git mv) | ✓ |
| §3.4 新建 scope-mapping | Task 2 | ✓ |
| §4.1 AGENTS.md | Task 3 Step 1 | ✓ |
| §4.2 commands | Task 3 Step 2-4 | ✓ |
| §4.3 脚本注释 | Task 4 | ✓ |
| §4.4 WORKFLOW.md | Task 5 | ✓ |
| §4.5 config/doc-sync-mapping | Task 6 | ✓ |
| §4.6 scope-mapping 内容 | Task 2 | ✓ |
| §5 验证方案 | Task 7 | ✓ |
| §6 执行顺序 | Task 1→(2-6 并行)→7 | ✓ |

无遗漏。

### 2. Placeholder scan

无 TBD/TODO/FIXME。所有代码块均为可直接执行的完整命令或精确的旧/新文本对照。

### 3. Type/name consistency

- `WORKFLOW.md` 全文一致（非 SKILL.md）
- `workflows/` 全文一致（非 skills/）
- `engineering/harness/config/scope-mapping.md` 全文一致
- 相对路径 `../../rules/`、`../../config/`、`../../templates/` 层级推导在 spec 附录 A 验证

无矛盾。
