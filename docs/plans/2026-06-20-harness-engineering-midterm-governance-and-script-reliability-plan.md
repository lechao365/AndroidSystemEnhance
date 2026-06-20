# Harness Engineering 中期治理与脚本可靠性整改 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `engineering/harness/` 从“规则 + 脚本集合”升级为具备控制总纲、准入矩阵、证据模型、自动校验路径与关键脚本 correctness 修复的中期治理闭环。

**Architecture:** 保持现有 `rules/`、`workflows/`、`lib/`、`config/`、`templates/` 主结构不变，在其上新增控制总纲、准入矩阵与校验机制；同时优先修复 `sync/revert/observability` 的高风险 correctness 问题，再推进 config 渐进机器化与 validator 落地。

**Tech Stack:** Bash, Markdown, YAML/JSON（渐进引入）, git, shellcheck 风格约束, harness observability lib

**Spec:** `docs/specs/2026-06-20-harness-engineering-midterm-governance-and-script-reliability-design.md`

---

## 文件结构与职责映射

### 新增文件
- `engineering/harness/CONTROL-CHARTER.md`
- `engineering/harness/config/task-admission-matrix.md`
- `engineering/harness/config/scope-mapping.yaml`
- `engineering/harness/config/doc-sync-mapping.yaml`
- `engineering/harness/config/schema/scope-mapping.schema.json`
- `engineering/harness/config/schema/doc-sync-mapping.schema.json`
- `engineering/harness/config/baseline-status.md`
- `engineering/harness/config/baseline-evidence-template.md`
- `engineering/harness/scripts/validate_harness_docs.sh`
- `engineering/harness/scripts/validate_harness_config.sh`
- `engineering/harness/scripts/validate_harness_scripts.sh`
- `engineering/harness/tests/test_harness_observability.sh`
- `engineering/harness/tests/test_sync_code_to_patchs.sh`
- `engineering/harness/tests/test_revert_code_from_patchs.sh`

### 新增测试夹具目录
- `engineering/harness/tests/fixtures/observability/basic/`
- `engineering/harness/tests/fixtures/sync-code-to-patchs/kernel-delete/`
- `engineering/harness/tests/fixtures/sync-code-to-patchs/kernel-modified-new/`
- `engineering/harness/tests/fixtures/sync-code-to-patchs/aosp-non-repo/`
- `engineering/harness/tests/fixtures/revert-code-from-patchs/non-repo-extra/`
- `engineering/harness/tests/fixtures/revert-code-from-patchs/upstream-missing/`
- `engineering/harness/tests/fixtures/revert-code-from-patchs/verify-matrix/`

### 修改文件
- `engineering/harness/README.md`
- `engineering/harness/rules/README.md`
- `engineering/harness/config/README.md`
- `engineering/harness/lib/README.md`
- `engineering/harness/workflows/README.md`
- `engineering/harness/rules/source-code-modify.md`
- `engineering/harness/rules/script-observability.md`
- `engineering/harness/rules/plantuml.md`
- `engineering/harness/templates/module-template.md`
- `engineering/harness/templates/module-readme-template.md`
- `engineering/harness/workflows/git-push-to-server/WORKFLOW.md`
- `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md`
- `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`
- `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md`
- `engineering/harness/lib/harness_bootstrap.sh`
- `engineering/harness/lib/harness_observability.sh`
- `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh`
- `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`

---

## Validator 职责边界

### `validate_harness_docs.sh`
只负责文档/契约层静态一致性：
- README 导航链接存在性
- 顶层 README 与子目录 README 文件清单一致性
- template 中 PlantUML 闭合与占位符规则
- workflow contract 必备章节/头部完整性
- 关键术语非法旧表述扫描

不负责：
- bash 代码语义
- YAML/schema 解析
- 脚本运行时行为

### `validate_harness_config.sh`
只负责机器层配置合法性：
- `scope-mapping.yaml` / `doc-sync-mapping.yaml` 存在性
- schema 校验
- `priority` 冲突
- `match` 空值
- docs 路径合法性

不负责：
- README 解释层正确性
- bash 是否消费配置
- workflow 文档引用完整性

### `validate_harness_scripts.sh`
只负责 bash 实现层静态合规：
- 是否统一经 `harness_bootstrap.sh`
- 是否调用 `harness_init`
- 是否出现裸 `exit`
- 是否出现裸 `/tmp/`
- 是否直接依赖 `_H_*` / `_h_*`
- 是否有裸 `echo` 违反 `script-observability`
- 是否出现未登记的新退出码

不负责：
- README/template 问题
- YAML/schema 合法性
- 运行时功能正确性

---

## 测试夹具组织约定

### observability
`engineering/harness/tests/fixtures/observability/basic/`

建议结构：
```text
observability/basic/
├── repo-root/
│   ├── AGENTS.md
│   └── engineering/output/log/
└── script-under-test.sh
```

覆盖：
- `result:` 行
- `status=` 行
- `latest.log`
- file artifact 轮转
- dir artifact 轮转

### sync-code-to-patchs
#### `engineering/harness/tests/fixtures/sync-code-to-patchs/kernel-delete/`
覆盖 kernel tracked deletion

#### `engineering/harness/tests/fixtures/sync-code-to-patchs/kernel-modified-new/`
覆盖 modified / new tracked / new untracked / excluded artifact

#### `engineering/harness/tests/fixtures/sync-code-to-patchs/aosp-non-repo/`
覆盖 AOSP 非 repo 目录复制与 prune

建议结构：
```text
sync-code-to-patchs/
├── kernel-delete/
│   ├── upstream/
│   ├── workspace/
│   └── expected/
├── kernel-modified-new/
│   ├── upstream/
│   ├── workspace/
│   └── expected/
└── aosp-non-repo/
    ├── aosp/
    ├── patchs/
    └── expected/
```

### revert-code-from-patchs
#### `engineering/harness/tests/fixtures/revert-code-from-patchs/non-repo-extra/`
覆盖 `aosp:build` + `build/foo.txt` 双前缀问题

#### `engineering/harness/tests/fixtures/revert-code-from-patchs/upstream-missing/`
覆盖 upstream 缺失必须显式失败

#### `engineering/harness/tests/fixtures/revert-code-from-patchs/verify-matrix/`
覆盖 `FIXED / KEPT / RESIDUAL / NEW-DIFF`

建议结构：
```text
revert-code-from-patchs/
├── non-repo-extra/
│   ├── workspace/
│   ├── patchs/
│   ├── plan-input.tsv
│   └── expected/
├── upstream-missing/
│   ├── workspace/
│   └── expected/
└── verify-matrix/
    ├── workspace-before/
    ├── workspace-after/
    ├── patchs/
    ├── plan.tsv
    └── expected-verify.tsv
```

---

## Task 1: 建立控制总纲与任务准入矩阵

**Files:**
- Create: `engineering/harness/CONTROL-CHARTER.md`
- Create: `engineering/harness/config/task-admission-matrix.md`
- Modify: `engineering/harness/README.md`
- Modify: `engineering/harness/rules/README.md`

- [ ] **Step 1: 编写控制总纲初稿**

定义以下章节：
```md
# Harness Control Charter

## 1. 目标边界
## 2. 对象模型
## 3. 真相源矩阵
## 4. Human / AI / Script 职责边界
## 5. 规则优先级
## 6. 受控例外
## 7. 术语表
```

必须显式定义：
- `workspace`
- `patchs`
- `archive`
- `candidate baseline`
- `promoted baseline`
- `promotion`
- `artifact`
- `workflow contract`

- [ ] **Step 2: 编写任务准入矩阵**

表结构：
```md
| 任务类型 | 允许直接修改 | 必读规则 | 必经 workflow | 是否先出 plan | 是否需用户确认 | 是否需 evidence |
|----------|--------------|----------|---------------|---------------|----------------|-----------------|
```

至少覆盖：
- workspace 源码修改
- patchs 归档
- patchs 回退
- patchs→文档
- harness bash 改造

- [ ] **Step 3: 更新顶层导航**

在 `engineering/harness/README.md` 加入：
- 控制总纲入口
- 准入矩阵入口
- config 机器层说明
- validator 入口

- [ ] **Step 4: 更新 rules 索引**

在 `engineering/harness/rules/README.md` 补充规则 ID 化说明。

- [ ] **Step 5: 手工检查引用完整性**

Run:
```bash
rg -n "CONTROL-CHARTER|task-admission-matrix|规则 ID|准入矩阵" engineering/harness
```

Expected:
- 新入口已被引用
- 无遗漏导航

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/CONTROL-CHARTER.md engineering/harness/config/task-admission-matrix.md engineering/harness/README.md engineering/harness/rules/README.md
git commit -m "文档(harness): 增加控制总纲与任务准入矩阵"
```

---

## Task 2: 清理模板/规则/README 的显式冲突

**Files:**
- Modify: `engineering/harness/templates/module-template.md`
- Modify: `engineering/harness/templates/module-readme-template.md`
- Modify: `engineering/harness/rules/plantuml.md`
- Modify: `engineering/harness/lib/README.md`
- Modify: `engineering/harness/workflows/README.md`

- [ ] **Step 1: 修复 `module-template.md` 的 PlantUML 闭合与占位符问题**

重点处理：
- `engineering/harness/templates/module-template.md:33-50` 补 `@enduml`
- UML 块中的 `{}` 占位符替换为 `<>`
- 路径示例避免触发文档自检冲突

- [ ] **Step 2: 修复 `module-readme-template.md` 的 UML 花括号冲突**

重点处理所有 PlantUML 代码块内的 `{{...}}` 占位符，替换为 `<...>` 形式。

- [ ] **Step 3: 修复 README 漂移**

补全 `engineering/harness/lib/README.md` 对 `harness_bootstrap.sh` 的说明。

- [ ] **Step 4: 核对 workflow README 的删除语义**

根据脚本最终能力，修正 `engineering/harness/workflows/README.md` 中“含删除对齐”的表述。

- [ ] **Step 5: 跑静态扫描**

Run:
```bash
rg -n "@startuml|@enduml|{{|~/workspace|harness_bootstrap" engineering/harness/templates engineering/harness/lib/README.md engineering/harness/workflows/README.md
```

Expected:
- UML 闭合
- UML 块内无花括号占位符
- README 清单一致

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/templates/module-template.md engineering/harness/templates/module-readme-template.md engineering/harness/rules/plantuml.md engineering/harness/lib/README.md engineering/harness/workflows/README.md
git commit -m "修复(harness): 清理模板与规则显式冲突"
```

---

## Task 3: 修复 observability 公共库与规则契约漂移

**Files:**
- Modify: `engineering/harness/lib/harness_bootstrap.sh`
- Modify: `engineering/harness/lib/harness_observability.sh`
- Modify: `engineering/harness/rules/script-observability.md`
- Test: `engineering/harness/tests/test_harness_observability.sh`

- [ ] **Step 1: 编写 observability 失败测试**

至少覆盖：
- `log_result` 输出 `^result:` 行
- `harness_status_emit` 输出 `^status=` 行
- artifact 目录轮转可删除目录型 artifact
- root 查找逻辑不重复漂移

- [ ] **Step 2: 运行测试确认失败**

Run:
```bash
bash engineering/harness/tests/test_harness_observability.sh
```

Expected:
- 失败于 `result:` / `status=` 断言
- 如有目录轮转测试，也应暴露 `rm -f` 问题

- [ ] **Step 3: 最小修改公共库**

关键点：
- `log_result()` 直写 `result:` 行
- `harness_status_emit()` 直写 `status=` 行
- `_h_rotate_artifacts()` 支持目录删除
- `harness_init()` 优先复用 `REPO_ROOT`

- [ ] **Step 4: 对齐规则文档**

更新 `engineering/harness/rules/script-observability.md` 的日志格式说明。

- [ ] **Step 5: 重新运行测试**

Run:
```bash
bash engineering/harness/tests/test_harness_observability.sh
```

Expected:
- PASS
- `latest.log` 可 grep 到 `^result:` 与 `^status=`

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/lib/harness_bootstrap.sh engineering/harness/lib/harness_observability.sh engineering/harness/rules/script-observability.md engineering/harness/tests/test_harness_observability.sh
git commit -m "修复(observability): 对齐结构化日志契约"
```

---

## Task 4: 修复 revert 的 non-repo EXTRA 路径 bug 与失败传播

**Files:**
- Modify: `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`
- Modify: `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`
- Test: `engineering/harness/tests/test_revert_code_from_patchs.sh`

- [ ] **Step 1: 编写 non-repo EXTRA 路径失败测试**

覆盖：
- `proj=aosp:build`
- `rel=build/foo.txt`
- apply 应删除 `$AOSP_WS/build/foo.txt`，而不是 `$AOSP_WS/build/build/foo.txt`

- [ ] **Step 2: 编写扫描失败传播测试**

覆盖：
- upstream base 缺失
- 关键扫描失败不得生成部分成功 plan

- [ ] **Step 3: 最小修改 revert 实现**

关键点：
- 统一 `proj` / `rel` 编码
- 修 `do_revert_extra()` 路径拼接
- `gen_plan()` / `gen_plan_silent()` 聚合子扫描返回码
- upstream 缺失策略一致化

- [ ] **Step 4: 更新 workflow 契约**

补充：
- non-repo 项编码规则
- 关键扫描失败的退出语义
- baseline 证据依赖

- [ ] **Step 5: 重新运行 revert 测试**

Run:
```bash
bash engineering/harness/tests/test_revert_code_from_patchs.sh
```

Expected:
- PASS
- non-repo EXTRA 删除命中正确路径
- upstream 缺失时显式失败

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md engineering/harness/tests/test_revert_code_from_patchs.sh
git commit -m "修复(revert): 修正非repo路径与失败传播"
```

---

## Task 5: 修复 sync 的删除语义与 README 契约失真

**Files:**
- Modify: `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh`
- Modify: `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md`
- Modify: `engineering/harness/workflows/README.md`
- Test: `engineering/harness/tests/test_sync_code_to_patchs.sh`

- [ ] **Step 1: 编写 tracked deletion 失败测试**

场景：
- upstream 存在文件
- workspace 删除该文件
- sync 后 patchs 必须显式表达删除语义

推荐 manifest 结构：
```yaml
deletions:
  kernel:
    - source: rpi5-kernel-build/common/drivers/foo.c
```

- [ ] **Step 2: 运行测试确认现状失败**

Run:
```bash
bash engineering/harness/tests/test_sync_code_to_patchs.sh
```

Expected:
- 当前实现无法表达 tracked deletion
- README/WORKFLOW 声明与行为不一致

- [ ] **Step 3: 实现最小 deletion 模型**

关键点：
- 扫描 `git diff --diff-filter=D`
- 在 `manifest.yaml` 加 deletion section
- 删除类不生成 `.diff`，仅进入 manifest 结构
- 为 revert 后续消费保留明确语义

- [ ] **Step 4: 更新 workflow 文档**

同步修正：
- `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md`
- `engineering/harness/workflows/README.md`

- [ ] **Step 5: 重新运行 sync 测试**

Run:
```bash
bash engineering/harness/tests/test_sync_code_to_patchs.sh
```

Expected:
- PASS
- tracked deletion 被显式记录
- patch tree / manifest / 状态输出一致

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md engineering/harness/workflows/README.md engineering/harness/tests/test_sync_code_to_patchs.sh
git commit -m "修复(sync): 补齐删除语义与镜像契约"
```

---

## Task 6: 引入 archive / baseline / promotion 证据模型

**Files:**
- Modify: `engineering/harness/rules/source-code-modify.md`
- Modify: `engineering/harness/CONTROL-CHARTER.md`
- Modify: `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`
- Create: `engineering/harness/config/baseline-status.md`
- Create: `engineering/harness/config/baseline-evidence-template.md`

- [ ] **Step 1: 在总纲定义状态模型**

定义：
```md
archive -> candidate baseline -> promoted baseline
```

每个状态列明：
- 来源
- 最低证据要求
- 是否允许作为 revert 真相源

- [ ] **Step 2: 在源码改动规则中补证据字段**

将“验证通过”具体化为：
- build
- package
- board verify
- operator
- timestamp

- [ ] **Step 3: 新增 evidence 模板**

模板字段：
```md
- baseline_id:
- source_branch:
- source_commit:
- sync_manifest:
- build_result:
- package_result:
- board_verify:
- approved_by:
- approved_at:
```

- [ ] **Step 4: 更新 revert workflow 的基线语义**

限制“已知良好基线”仅适用于 promoted baseline。

- [ ] **Step 5: 术语一致性扫描**

Run:
```bash
rg -n "archive|baseline|promotion|已知良好基线|验证通过" engineering/harness
```

Expected:
- 术语一致
- 无冲突旧表述

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/rules/source-code-modify.md engineering/harness/CONTROL-CHARTER.md engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md engineering/harness/config/baseline-status.md engineering/harness/config/baseline-evidence-template.md
git commit -m "规则(harness): 引入baseline证据模型"
```

---

## Task 7: 推进 config 渐进机器化

**Files:**
- Create: `engineering/harness/config/scope-mapping.yaml`
- Create: `engineering/harness/config/doc-sync-mapping.yaml`
- Create: `engineering/harness/config/schema/scope-mapping.schema.json`
- Create: `engineering/harness/config/schema/doc-sync-mapping.schema.json`
- Modify: `engineering/harness/config/README.md`
- Modify: `engineering/harness/workflows/git-push-to-server/WORKFLOW.md`
- Modify: `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md`
- Test: `engineering/harness/scripts/validate_harness_config.sh`

- [ ] **Step 1: 编写 YAML 初稿**

`scope-mapping.yaml`：
```yaml
version: 1
rules:
  - match: "patchs/rpi5/kernel/**"
    scope: "kernel"
    priority: 100
```

`doc-sync-mapping.yaml`：
```yaml
version: 1
routes:
  - match: "**/LcView/**"
    docs:
      - "docs/01-打点增强"
    priority: 100
```

- [ ] **Step 2: 编写 schema**

至少校验：
- version
- rules/routes
- priority
- match
- docs 字段类型

- [ ] **Step 3: 更新 config README**

明确：
- Markdown 是解释层
- YAML/schema 是机器层
- 本阶段不强制脚本全面消费 YAML

- [ ] **Step 4: 更新 workflow 契约引用**

让以下 workflow 文档优先引用 YAML/config schema：
- `engineering/harness/workflows/git-push-to-server/WORKFLOW.md`
- `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md`

- [ ] **Step 5: 编写并运行 config 校验器**

Run:
```bash
bash engineering/harness/scripts/validate_harness_config.sh
```

Expected:
- PASS
- YAML/schema 一致
- priority / match 无冲突

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/config/scope-mapping.yaml engineering/harness/config/doc-sync-mapping.yaml engineering/harness/config/schema engineering/harness/config/README.md engineering/harness/workflows/git-push-to-server/WORKFLOW.md engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md engineering/harness/scripts/validate_harness_config.sh
git commit -m "重构(harness): 配置映射渐进机器化"
```

---

## Task 8: 落地 validators

**Files:**
- Create: `engineering/harness/scripts/validate_harness_docs.sh`
- Create: `engineering/harness/scripts/validate_harness_config.sh`
- Create: `engineering/harness/scripts/validate_harness_scripts.sh`
- Modify: `engineering/harness/scripts/README.md`
- Modify: `engineering/harness/README.md`

- [ ] **Step 1: 实现文档校验器**

覆盖：
- README 链接
- README 清单
- template PlantUML 合法性
- workflow contract 基础结构
- 基线术语非法旧表述

- [ ] **Step 2: 实现脚本校验器**

覆盖：
- bootstrap
- harness_init
- 裸 exit
- 裸 /tmp/
- 私有 API 依赖
- 裸 echo
- 非法退出码

- [ ] **Step 3: 更新 scripts README**

加入 validator 用途与调用方式。

- [ ] **Step 4: 运行 validators**

Run:
```bash
bash engineering/harness/scripts/validate_harness_docs.sh
bash engineering/harness/scripts/validate_harness_config.sh
bash engineering/harness/scripts/validate_harness_scripts.sh
```

Expected:
- PASS
- 或列出剩余治理项并清零

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/scripts/validate_harness_docs.sh engineering/harness/scripts/validate_harness_config.sh engineering/harness/scripts/validate_harness_scripts.sh engineering/harness/scripts/README.md engineering/harness/README.md
git commit -m "新增(harness): 文档配置脚本校验器"
```

---

## Task 9: 建最小回归夹具

**Files:**
- Create: `engineering/harness/tests/fixtures/...`
- Create: `engineering/harness/tests/test_sync_code_to_patchs.sh`
- Create: `engineering/harness/tests/test_revert_code_from_patchs.sh`
- Create: `engineering/harness/tests/test_harness_observability.sh`

- [ ] **Step 1: 建 observability fixture**

使用：
- `engineering/harness/tests/fixtures/observability/basic/`

断言：
- `result:` / `status=`
- latest.log
- artifact 轮转

- [ ] **Step 2: 建 sync fixtures**

使用：
- `engineering/harness/tests/fixtures/sync-code-to-patchs/kernel-delete/`
- `engineering/harness/tests/fixtures/sync-code-to-patchs/kernel-modified-new/`
- `engineering/harness/tests/fixtures/sync-code-to-patchs/aosp-non-repo/`

断言：
- modified / new / deletion / prune / excluded artifact

- [ ] **Step 3: 建 revert fixtures**

使用：
- `engineering/harness/tests/fixtures/revert-code-from-patchs/non-repo-extra/`
- `engineering/harness/tests/fixtures/revert-code-from-patchs/upstream-missing/`
- `engineering/harness/tests/fixtures/revert-code-from-patchs/verify-matrix/`

断言：
- path correctness
- upstream missing fail-fast
- verify 四分类

- [ ] **Step 4: 统一测试入口**

Run:
```bash
bash engineering/harness/tests/test_harness_observability.sh && \
bash engineering/harness/tests/test_sync_code_to_patchs.sh && \
bash engineering/harness/tests/test_revert_code_from_patchs.sh
```

Expected:
- 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/tests
git commit -m "测试(harness): 增加workflow与observability回归夹具"
```

---

## Task 10: 统一 workflow contract 结构与规则 ID 引用

**Files:**
- Modify: `engineering/harness/workflows/git-push-to-server/WORKFLOW.md`
- Modify: `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md`
- Modify: `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`
- Modify: `engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md`
- Modify: `engineering/harness/rules/*.md`

- [ ] **Step 1: 定义稳定规则 ID**

至少包含：
```md
- SRC-001: workspace 是唯一编译真相源
- SRC-002: patchs 仅允许受控同步
- OBS-001: engineering bash 脚本必须通过 bootstrap 接入
- DOC-001: spec/plan 与长期技术文档分层
- EVD-001: 未证据化 baseline 不得宣称为恢复真相源
```

- [ ] **Step 2: 统一 workflow 章节结构**

每个 `WORKFLOW.md` 至少含：
- Trigger
- Preconditions
- Inputs
- Human confirmation gates
- Outputs / artifacts
- Failure / recovery
- Related policy IDs

- [ ] **Step 3: 更新 docs validator**

将 workflow 结构检查纳入 `validate_harness_docs.sh`。

- [ ] **Step 4: 运行校验**

Run:
```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

Expected:
- 所有 workflow contract 结构完整
- 已引用规则 ID

- [ ] **Step 5: Commit**

```bash
git add engineering/harness/workflows engineering/harness/rules engineering/harness/scripts/validate_harness_docs.sh
git commit -m "重构(harness): 统一workflow契约与规则引用"
```

---

## 并行执行建议

### 治理线
- Task 1
- Task 2
- Task 6
- Task 7
- Task 8
- Task 10

### 脚本线
- Task 3
- Task 4
- Task 5
- Task 9

依赖关系：
- Task 9 依赖 Task 3/4/5 行为模型稳定
- Task 10 建议在 Task 1/6/7 后执行

---

## 验收命令总表

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
bash engineering/harness/scripts/validate_harness_config.sh
bash engineering/harness/scripts/validate_harness_scripts.sh
bash engineering/harness/tests/test_harness_observability.sh
bash engineering/harness/tests/test_sync_code_to_patchs.sh
bash engineering/harness/tests/test_revert_code_from_patchs.sh
```

Expected:
- 全部退出码为 0
- 不再存在模板/README/规则显式冲突
- `sync/revert/observability` 关键 correctness 问题可复现、可测试、可回归
