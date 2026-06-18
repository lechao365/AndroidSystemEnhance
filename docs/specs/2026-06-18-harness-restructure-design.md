# Harness 工程目录重构设计

> **日期**：2026-06-18
> **状态**：已确认，待实施
> **范围**：将分散在根目录的 `rules/`、`skills/`、`scripts/`、`templates/` 重构进 `engineering/harness/`，分离框架逻辑与可变数据配置

---

## 1. 背景与动机

当前项目的 harness engineering 资产（规则、工作流、脚本、模板）散落在根目录的 4 个独立目录中：

```
AndroidSystemEnhance/
├── rules/       (4 规则 + 1 映射配置混杂)
├── skills/      (3 个 workflow，每个含 SKILL.md + .sh)
├── scripts/     (1 个独立构建脚本)
├── templates/   (2 个文档骨架)
└── engineering/harness/  (已存在，空目录 ← 目标位置)
```

**存在的问题**：

1. **目录分散**：harness 相关资产与业务目录（`00-环境准备/`、`01-打点增强/`、`02-IO增强/`、`patchs/`、`docs/`）平铺在根目录，职责边界模糊
2. **命名歧义**：`skills/` 下的内容其实是 "脚本+AI" 协作的完整**工作流**（workflow），并非 opencode 语义下的 skill，命名误导
3. **框架与数据耦合**：`rules/doc-sync-mapping.md` 是纯数据配置（patchs→文档目录映射表），与 4 个纯规则约束混在一起；新增特性需修改"规则目录"中的"配置文件"，违反"框架稳定、数据可变"原则

**目标**：

- 统一收纳到 `engineering/harness/` 自包含工具箱
- `skills/` 改名为 `workflows/`，`SKILL.md` 改名为 `WORKFLOW.md`
- 将可变数据配置（映射表、scope 表）抽到独立的 `config/` 目录，新增模块只改 config 不动框架

---

## 2. 目标目录结构

```
engineering/harness/
├── workflows/                          # 原 skills/，SKILL.md → WORKFLOW.md
│   ├── sync-code-to-patchs/
│   │   ├── WORKFLOW.md                 # 原 SKILL.md
│   │   └── sync_code_to_patchs.sh
│   ├── git-push-to-server/
│   │   ├── WORKFLOW.md                 # 原 SKILL.md
│   │   ├── collect_diff.sh
│   │   └── commit_and_push.sh
│   └── sync-patchs-to-doc/
│       ├── WORKFLOW.md                 # 原 SKILL.md
│       └── sync_patchs_to_doc.sh
├── rules/                              # 4 个纯规则（不可违反的硬约束）
│   ├── source-code-priority.md
│   ├── parallel-strategy.md
│   ├── doc-paths.md
│   └── plantuml.md
├── config/                             # 可变数据配置（新增模块只改这里）
│   ├── doc-sync-mapping.md             # 原 rules/doc-sync-mapping.md
│   └── scope-mapping.md                # 新抽取：git-push 目录→scope 映射
├── scripts/                            # 独立构建脚本
│   └── mk_rpi5_full_image.sh
└── templates/                          # 文档骨架（只读契约）
    ├── module-template.md
    └── module-readme-template.md
```

### 2.1 子目录职责与改动频率

| 子目录 | 职责 | 改动频率 |
|--------|------|---------|
| `workflows/` | 框架逻辑（AI+脚本协作流程） | 低（框架稳定） |
| `rules/` | 约束规则（不可违反的硬约束） | 低 |
| `config/` | 可变数据（映射表、scope 表） | **高（新增模块只改这里）** |
| `scripts/` | 独立构建脚本 | 中 |
| `templates/` | 文档骨架（只读契约） | 极低 |

### 2.2 设计原则：框架与数据分离

- **框架**（workflows/rules/scripts/templates）：描述"怎么做"，稳定，新增模块不动
- **数据**（config）：描述"改什么"，可变，新增模块只改这里
- 后续新增 `03-*/04-*` 特性时，只需在 `config/doc-sync-mapping.md` 追加映射行、在 `config/scope-mapping.md` 追加 scope 行，无需碰 workflow 逻辑

---

## 3. 文件迁移清单

所有移动使用 `git mv` 保留历史追踪。

### 3.1 skills/ → engineering/harness/workflows/（3 个 workflow，7 文件）

| 源路径 | 目标路径 | 备注 |
|--------|---------|------|
| `skills/sync-code-to-patchs/SKILL.md` | `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md` | 改名 |
| `skills/sync-code-to-patchs/sync_code_to_patchs.sh` | `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh` | 同目录 |
| `skills/git-push-to-server/SKILL.md` | `engineering/harness/workflows/git-push-to-server/WORKFLOW.md` | 改名 |
| `skills/git-push-to-server/collect_diff.sh` | `engineering/harness/workflows/git-push-to-server/collect_diff.sh` | 同目录 |
| `skills/git-push-to-server/commit_and_push.sh` | `engineering/harness/workflows/git-push-to-server/commit_and_push.sh` | 同目录 |
| `skills/sync-patchs-to-doc/SKILL.md` | `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md` | 改名 |
| `skills/sync-patchs-to-doc/sync_patchs_to_doc.sh` | `engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh` | 同目录 |

### 3.2 rules/ → engineering/harness/rules/（4 个规则）

| 源路径 | 目标路径 |
|--------|---------|
| `rules/source-code-priority.md` | `engineering/harness/rules/source-code-priority.md` |
| `rules/parallel-strategy.md` | `engineering/harness/rules/parallel-strategy.md` |
| `rules/doc-paths.md` | `engineering/harness/rules/doc-paths.md` |
| `rules/plantuml.md` | `engineering/harness/rules/plantuml.md` |

### 3.3 rules/ → engineering/harness/config/（1 个配置）

| 源路径 | 目标路径 |
|--------|---------|
| `rules/doc-sync-mapping.md` | `engineering/harness/config/doc-sync-mapping.md` |

### 3.4 新建文件

| 目标路径 | 内容来源 |
|---------|---------|
| `engineering/harness/config/scope-mapping.md` | 从 `skills/git-push-to-server/SKILL.md` L48-62 的 scope 词表抽取，路径更新为新 harness 路径 |

### 3.5 scripts/ & templates/（平移，3 文件）

| 源路径 | 目标路径 |
|--------|---------|
| `scripts/mk_rpi5_full_image.sh` | `engineering/harness/scripts/mk_rpi5_full_image.sh` |
| `templates/module-template.md` | `engineering/harness/templates/module-template.md` |
| `templates/module-readme-template.md` | `engineering/harness/templates/module-readme-template.md` |

### 3.6 迁移后清理

删除 4 个已空的旧根目录：`skills/`、`rules/`、`scripts/`、`templates/`（`git mv` 后自动清空，无需额外 rmdir）。

**总计**：15 个 `git mv` + 1 个新建文件。

---

## 4. 引用更新清单

### 4.1 AGENTS.md（4 处）

| 行号 | 旧引用 | 新引用 |
|------|--------|--------|
| L6 | `rules/source-code-priority.md` | `engineering/harness/rules/source-code-priority.md` |
| L11 | `rules/parallel-strategy.md` | `engineering/harness/rules/parallel-strategy.md` |
| L15 | `rules/plantuml.md` | `engineering/harness/rules/plantuml.md` |
| L24 | `rules/doc-paths.md` | `engineering/harness/rules/doc-paths.md` |

### 4.2 .opencode/commands/*.md（3 文件 × 2 处）

| 文件 | 行号 | 旧 | 新 |
|------|------|----|----|
| `sync-code-to-patchs.md` | L5 | `bash skills/sync-code-to-patchs/sync_code_to_patchs.sh` | `bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh` |
| `sync-code-to-patchs.md` | L8 | `@skills/sync-code-to-patchs/SKILL.md` | `@engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md` |
| `git-push-to-server.md` | L5 | `bash skills/git-push-to-server/collect_diff.sh` | `bash engineering/harness/workflows/git-push-to-server/collect_diff.sh` |
| `git-push-to-server.md` | L8 | `@skills/git-push-to-server/SKILL.md` | `@engineering/harness/workflows/git-push-to-server/WORKFLOW.md` |
| `sync-patchs-to-doc.md` | L5 | `bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh` | `bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh` |
| `sync-patchs-to-doc.md` | L8 | `@skills/sync-patchs-to-doc/SKILL.md` | `@engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md` |

### 4.3 脚本文件头部注释（4 个 .sh）

每个脚本头部有"规则详见"和"用法"两处自路径引用，外加 `--help` 输出：

| 脚本 | 行号 | 旧 | 新 |
|------|------|----|----|
| `sync_code_to_patchs.sh` | L6 | `skills/sync-code-to-patchs/SKILL.md` | `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md` |
| `sync_code_to_patchs.sh` | L7, L81 | `bash skills/sync-code-to-patchs/sync_code_to_patchs.sh` | `bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh` |
| `collect_diff.sh` | L6 | `skills/git-push-to-server/SKILL.md` | `engineering/harness/workflows/git-push-to-server/WORKFLOW.md` |
| `collect_diff.sh` | L7, L40 | `bash skills/git-push-to-server/collect_diff.sh` | `bash engineering/harness/workflows/git-push-to-server/collect_diff.sh` |
| `commit_and_push.sh` | L6 | `skills/git-push-to-server/SKILL.md` | `engineering/harness/workflows/git-push-to-server/WORKFLOW.md` |
| `commit_and_push.sh` | L7, L47 | `bash skills/git-push-to-server/commit_and_push.sh` | `bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh` |
| `sync_patchs_to_doc.sh` | L6 | `skills/sync-patchs-to-doc/SKILL.md` | `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md` |
| `sync_patchs_to_doc.sh` | L7, L37 | `bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh` | `bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh` |
| `sync_patchs_to_doc.sh` | L212 | `rules/doc-sync-mapping.md` | `engineering/harness/config/doc-sync-mapping.md` |

### 4.4 WORKFLOW.md 内部引用（3 文件）

**相对路径层级说明**：`engineering/harness/workflows/xxx/WORKFLOW.md` → 同层 `../rules/` 上溯到 `workflows/`，再上溯 `../../rules/` 到 `harness/`。原 `skills/xxx/SKILL.md` 引用 `../../rules/`（上两级到项目根），新 `workflows/xxx/WORKFLOW.md` 引用 `../../rules/`（上两级到 harness 根）。层级恰好相同，**指向 rules 的相对路径无需改**。

#### sync-code-to-patchs/WORKFLOW.md

| 行号 | 旧 | 新 | 说明 |
|------|----|----|------|
| L19 | `../../rules/source-code-priority.md` | 不变 | 层级巧合相同 |
| L24-26 | `bash skills/sync-code-to-patchs/sync_code_to_patchs.sh`（3 处） | `bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh` | 用法示例 |

#### git-push-to-server/WORKFLOW.md

| 行号 | 旧 | 新 | 说明 |
|------|----|----|------|
| L17-18 | `bash skills/git-push-to-server/collect_diff.sh`（2 处） | `bash engineering/harness/workflows/git-push-to-server/collect_diff.sh` | 用法示例 |
| L48-62 | scope 词表（整块表格） | **删除，替换为引用**：`详见 [scope 映射表](../../config/scope-mapping.md)` | 数据抽到 config |
| L112 | `bash skills/git-push-to-server/commit_and_push.sh` | `bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh` | 用法示例 |

#### sync-patchs-to-doc/WORKFLOW.md

| 行号 | 旧 | 新 | 说明 |
|------|----|----|------|
| L10 | `templates/*.md` | `engineering/harness/templates/*.md` | 概念引用补全路径 |
| L23-25 | `bash skills/sync-patchs-to-doc/sync_patchs_to_doc.sh`（3 处） | `bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh` | 用法示例 |
| L32 | `../../rules/doc-sync-mapping.md` | `../../config/doc-sync-mapping.md` | **改 rules→config** |
| L108 | `rules/plantuml.md` | `engineering/harness/rules/plantuml.md` | 概念引用补全 |
| L129 | `rules/plantuml.md` | `engineering/harness/rules/plantuml.md` | 概念引用补全 |
| L150 | `rules/plantuml.md` | `engineering/harness/rules/plantuml.md` | 概念引用补全 |
| L173 | `rules/doc-sync-mapping.md` | `engineering/harness/config/doc-sync-mapping.md` | **改 rules→config** |
| L174 | `templates/*.md` | `engineering/harness/templates/*.md` | 概念引用补全 |

### 4.5 config/doc-sync-mapping.md（迁后内容修订）

| 行号 | 旧 | 新 | 说明 |
|------|----|----|------|
| L4 | `无需修改 skills/ 工作流` | `无需修改 workflows/ 工作流` | 提及目录名 |

### 4.6 新建 config/scope-mapping.md 内容

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

> 注：`kernel/`、`aosp/` 行保留不变（它们指 patchs 内路径，不受本次迁移影响）。

### 4.7 不修改的文件

- `docs/specs/2026-06-17-*.md`（历史 spec，保持历史快照）
- `docs/plans/2026-06-17-*.md`（历史 plan，保持历史快照）
- `01-打点增强/01.03-*.md` L1000（历史变更记录中提及 `templates/module-template.md`）
- `scripts/mk_rpi5_full_image.sh` 内容（仅 git mv，内部无 harness 自引用——它指向 `~/workspace/`）

---

## 5. 验证方案

### 5.1 静态验证（必做）

| 检查项 | 方法 | 期望结果 |
|--------|------|---------|
| **旧路径残留扫描** | `rg "bash skills/\|@skills/\|SKILL\.md" --glob '!.opencode/commands/**'` 在 commands 和 workflows 下 | 0 命中 |
| **commands 引用可达** | 读取 `.opencode/commands/*.md`，确认 `@engineering/harness/workflows/.../WORKFLOW.md` 和 `bash engineering/harness/workflows/.../*.sh` 路径对应文件存在 | 全部存在 |
| **WORKFLOW.md 内部链接** | 读取 3 个 WORKFLOW.md，确认 `../../rules/*.md`、`../../config/*.md`、`../../templates/*.md` 指向的文件实际存在 | 全部存在 |
| **AGENTS.md 链接** | 读取 AGENTS.md，确认 4 处 `engineering/harness/rules/*.md` 文件存在 | 全部存在 |
| **脚本可执行权限** | `ls -l engineering/harness/workflows/*/*.sh engineering/harness/scripts/*.sh` | 所有 .sh 保持 `-rwxrwxrwx` |

### 5.2 功能验证（端到端演练）

#### 5.2.1 git-push-to-server（最关键，本次会直接用到）

```bash
# 1. 帮助文档路径正确
bash engineering/harness/workflows/git-push-to-server/collect_diff.sh --help
# 期望：Usage 行显示新路径

# 2. dry-run 全流程（collect + AI 生成 message + 展示，不 commit）
#    通过 .opencode 命令触发：/git-push-to-server --dry-run
#    期望：collect_diff.sh 正常输出 diff，AI 能 @ 到 WORKFLOW.md
```

#### 5.2.2 sync-code-to-patchs

```bash
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --check-only
# 期望：Usage 行新路径，正常输出检查结果（OK/MISS/SKIP/PRUNE）
```

#### 5.2.3 sync-patchs-to-doc

```bash
bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only
# 期望：Usage 行新路径，正常输出或提示无变动
```

### 5.3 验证执行时机

迁移改动用**同一个 commit** 完成（保持原子性），验证在 commit 前、commit 后各跑一次：

| 时机 | 动作 |
|------|------|
| **commit 前** | 5.1 全部静态检查 + 5.2 三个 `--help`/`--check-only` |
| **commit 后** | 通过 `/git-push-to-server` 实际提交本次迁移（顺便验证 git-push workflow 自身） |

> **闭环洞察**：本次迁移的 commit 本身就是 git-push-to-server workflow 的最佳实战验证。用迁移改动来验证迁移后的工具，闭环优雅。

### 5.4 回滚策略

若验证失败：
- `git checkout -- .` 回滚所有未提交改动
- `git clean -fd engineering/harness/` 清理新建目录
- 因使用 `git mv`，git 历史完整保留，回滚无损失

### 5.5 不验证的内容

- `mk_rpi5_full_image.sh`：依赖 `~/workspace/` 编译环境，本次只验证路径迁移（无内部自引用），不跑实际编译
- 历史文档中的旧路径：按第 4.7 节决策不动

---

## 6. 执行顺序

按依赖关系排序，原子单 commit 完成：

| 步骤 | 动作 | 依赖 |
|------|------|------|
| **1. 建目录** | `mkdir -p engineering/harness/{workflows,rules,config,scripts,templates}` | 无 |
| **2. git mv 文件** | 按 3.1-3.5 清单，15 个 `git mv`（含 SKILL.md→WORKFLOW.md 改名） | 步骤1 |
| **3. 新建 scope-mapping.md** | 在 `engineering/harness/config/` 下新建，内容见 4.6 | 步骤2 |
| **4. 改引用（8 文件）** | AGENTS.md + 3 commands + 4 scripts，按 4.1-4.3 清单 | 步骤2 |
| **5. 改 WORKFLOW.md（3 文件）+ config/doc-sync-mapping.md** | 按 4.4-4.5 清单 | 步骤2 |
| **6. 静态验证** | 跑 5.1 全部检查 | 步骤3-5 |
| **7. commit** | 通过 `/git-push-to-server` 实际提交（兼做 5.2 功能验证） | 步骤6 |

### 6.1 并行执行策略

符合项目 `parallel-strategy.md`，将改动拆为 3 个无重叠文件集，分派子 agent 并行：

| 子 agent | 负责文件集 | 步骤 |
|----------|-----------|------|
| **Agent-1：目录迁移** | 15 个 git mv + mkdir | 步骤 1-2 |
| **Agent-2：引用更新 A** | AGENTS.md + 3 commands + 4 scripts（步骤4） | 依赖 Agent-1 完成 |
| **Agent-3：引用更新 B** | 3 WORKFLOW.md + config/doc-sync-mapping.md + 新建 scope-mapping.md（步骤3、5） | 依赖 Agent-1 完成 |

> Agent-1 必须先完成（串行），Agent-2/Agent-3 并行（文件集不重叠）。最后主会话跑步骤6验证 + 步骤7 commit。

---

## 7. 风险点与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| **相对路径层级误判** | WORKFLOW.md 中 `../../rules/` 指向错位 | 5.1 静态验证的"WORKFLOW.md 内部链接可达性"检查兜底 |
| **scope 词表抽取遗漏** | git-push 生成 message 时 scope 缺失 | scope-mapping.md 新建后，WORKFLOW.md 保留判定逻辑（只删数据表），逻辑+数据双校验 |
| **git mv 后 Windows 大小写问题** | WSL 环境下 SKILL.md→WORKFLOW.md 改名可能因大小写不敏感失效 | 用两步 mv：先 `git mv SKILL.md SKILL.md.tmp`，再 `git mv SKILL.md.tmp WORKFLOW.md`（执行时验证） |
| **遗漏引用点** | 命令调用断链 | 5.1 的精确残留扫描兜底（`rg "bash skills/\|@skills/\|SKILL\.md"`） |
| **`.opencode/commands` 的 `@` 引用语法** | opencode 可能对 `@` 路径有特殊处理 | 5.2.1 的 `/git-push-to-server --dry-run` 实测验证 |

---

## 8. 不做的事（YAGNI）

- 不写迁移脚本（一次性工程，ROI 低）
- 不重构历史文档（docs/specs/、docs/plans/）中的旧路径引用
- 不改 `mk_rpi5_full_image.sh` 内容（无自引用）
- 不动 `engineering/loop/`（本次不涉及）
- 不新增 `config/README.md`（保持最小化）
- 不改变命令文件名（`.opencode/commands/` 下文件名保持，用户输入习惯不变）

---

## 9. 影响评估

### 9.1 用户可见变化

| 维度 | 变化 |
|------|------|
| **命令调用** | `/sync-code-to-patchs`、`/git-push-to-server`、`/sync-patchs-to-doc` 命令名不变，行为不变 |
| **目录结构** | 根目录从 13 个条目精简为 9 个（减少 4 个散落目录） |
| **新增模块流程** | 新增 `03-*` 特性时，只改 `engineering/harness/config/doc-sync-mapping.md` + `scope-mapping.md`，不碰 workflow |

### 9.2 框架/数据分离收益

| 场景 | 重构前 | 重构后 |
|------|--------|--------|
| 新增 patchs→文档映射 | 改 `rules/doc-sync-mapping.md`（规则目录） | 改 `config/doc-sync-mapping.md`（配置目录） |
| 新增工程目录 scope | 改 `skills/git-push-to-server/SKILL.md`（workflow 文件） | 改 `config/scope-mapping.md`（配置目录） |
| 新增 workflow | 加 `skills/xxx/SKILL.md` | 加 `workflows/xxx/WORKFLOW.md` |

---

## 附录 A：相对路径层级推导

以 `sync-patchs-to-doc/WORKFLOW.md` 为例（位于 `engineering/harness/workflows/sync-patchs-to-doc/`）：

```
engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md   ← 起点
../                          → engineering/harness/workflows/
../../                       → engineering/harness/             ← harness 根
../../rules/xxx.md           → engineering/harness/rules/xxx.md ✅
../../config/xxx.md          → engineering/harness/config/xxx.md ✅
../../templates/xxx.md       → engineering/harness/templates/xxx.md ✅
```

原 `skills/sync-patchs-to-doc/SKILL.md` 的 `../../rules/` 上溯到项目根的 `rules/`，层级恰好与新结构上溯到 harness 根的 `rules/` 相同。这是巧合，但简化了迁移（rules 相对引用无需改）。

指向 `config/` 和 `templates/` 的引用则需显式更新（原 `../../rules/doc-sync-mapping.md` → 新 `../../config/doc-sync-mapping.md`）。
