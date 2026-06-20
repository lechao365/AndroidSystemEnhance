# Harness Engineering 中期治理与脚本可靠性整改设计

> **日期**：2026-06-20  
> **状态**：已确认设计，待文档审阅 / 待实施  
> **范围**：审视并强化 `engineering/harness/` 目录的治理体系、规则体系、脚本可靠性与 AI 任务可控性，使其更符合 harness engineering 的控制面 / 执行面思想。

---

## 1. 背景与动机

### 1.1 当前基础

`engineering/harness/` 已经具备较完整的工程骨架，主要目录包括：

- `rules/`：规则与约束
- `workflows/`：多步闭环工作流
- `lib/`：bash 公共能力
- `config/`：映射与配置数据
- `templates/`：文档模板契约
- `scripts/`：独立工具脚本

当前体系已经形成若干重要理念：

1. `~/workspace/` 是源码真相源，`patchs/` 是归档镜像
2. 脚本负责机械动作，AI 负责语义理解
3. 重要流程强调先方案、后落盘
4. `script-observability` 已建立较成熟的维测基座

### 1.2 当前主要问题

尽管已有较好基础，当前 harness 仍存在 4 类关键问题：

#### A. 控制平面分散

harness 的上位约束散落于多个位置：

- 顶层导航：`engineering/harness/README.md:5-19`
- 规则索引：`engineering/harness/rules/README.md:7-13`
- 工作流总览：`engineering/harness/workflows/README.md:7-12`
- 各 workflow 的 `WORKFLOW.md`
- 模板约束：`engineering/harness/templates/README.md:12-16`

这导致 AI 或维护者需要拼接多个事实源，才能还原完整行为约束。

#### B. 关键状态缺少证据化

当前 `patchs/rpi5/` 在文档语义中既被描述为归档镜像，也被 revert 流程作为“已知良好基线”使用：

- 验证纪律：`engineering/harness/rules/source-code-modify.md:14-19`
- 恢复语义：`engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md:8-12`

但“已知良好”仍停留在行为纪律层，没有 baseline promotion 的机器可追溯证据。

#### C. 契约之间已有显式冲突

已发现若干可见冲突：

1. PlantUML 规则禁止 UML 块中使用花括号占位符，见  
   `engineering/harness/rules/plantuml.md:21-41`
2. 模板文件仍在 UML 块中使用花括号占位符，见  
   `engineering/harness/templates/module-readme-template.md:20-33`
3. `module-template.md` 的第一段 UML 示例未闭合，见  
   `engineering/harness/templates/module-template.md:33-50`
4. `harness/README.md` 与 `lib/README.md` 对 `harness_bootstrap.sh` 的清单不一致，见  
   `engineering/harness/README.md:26` 与 `engineering/harness/lib/README.md:7-13`

#### D. 脚本 correctness / consistency 存在缺口

已发现以下重点风险：

1. `sync_code_to_patchs.sh` 对 tracked deletion 的表达不足，和“精确镜像 / 含删除对齐”语义存在张力，见  
   `engineering/harness/workflows/README.md:10-11`  
   `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh:163-216`  
   `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh:252-302`
2. `revert_code_from_patchs.sh` 的 non-repo EXTRA 路径存在重复拼接风险，见  
   `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh:303-328`  
   `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh:473-486`
3. `harness_observability.sh` 的 `result:` / `status=` 输出与规则定义不一致，见  
   `engineering/harness/rules/script-observability.md:146-153`  
   `engineering/harness/lib/harness_observability.sh:238-251`  
   `engineering/harness/lib/harness_observability.sh:285-300`

---

## 2. 目标

本次中期治理目标不是重建整个 harness，而是在保持现有主目录结构基本稳定的前提下，建立一个更可靠、可控、可验证的 harness engineering 闭环。

### 2.1 总体目标

1. 建立统一控制总纲，减少规则权威源分散
2. 建立任务准入矩阵，让 AI / 人能稳定命中正确 workflow
3. 明确 `archive / baseline / promotion` 状态模型，避免 `patchs` 语义混用
4. 修复模板 / README / 规则之间的显式冲突
5. 修复 `observability / revert / sync` 的关键 correctness 问题
6. 引入渐进机器化配置与 validator，推动规则从 prose 走向 machine-enforced
7. 建立最小回归夹具，为后续演进提供回归基础

### 2.2 非目标

本轮不做以下事项：

1. 不重命名真实目录 `patchs/`
2. 不大规模重构 `engineering/harness/` 的主目录拓扑
3. 不要求本轮就让所有 workflow 脚本全面切换消费 YAML 配置
4. 不把所有历史文档体系一次性重排
5. 不扩大到 `engineering/loop/` 以外的其他业务体系

---

## 3. 设计原则

### 3.1 控制面与执行面分离

- **控制面**：定义什么允许发生、何时允许发生、由谁允许发生
- **执行面**：保证发生时可验证、可追溯、可失败、可回归

如果只有控制面，没有执行面，规则会退化成口号。  
如果只有执行面，没有控制面，AI 会持续走偏。

### 3.2 保持现有主结构稳定

保留以下主目录的职责边界：

- `rules/`
- `workflows/`
- `lib/`
- `config/`
- `templates/`
- `scripts/`

在此基础上新增总纲、准入矩阵、validator、schema 等，不进行高侵入重组。

### 3.3 渐进机器化

关键映射和控制数据应逐步从 Markdown 解释层迁移到 machine-readable 配置层，但保留 README / Markdown 解释层供人理解。

原则是：

- 人读 README / 规则文档
- 机器读 YAML / JSON / schema
- workflow 和 validator 优先依赖机器层真相源

### 3.4 保守兼容历史命名

保留 `patchs/` 目录名，但在总纲中显式定义术语语义，避免目录名和治理语义混在一起。

### 3.5 先修 correctness，再建平台能力

实施顺序必须是：

1. 先清理显式冲突
2. 先修脚本 correctness 缺陷
3. 再引入 validator 与 schema
4. 最后补回归与证据链

---

## 4. 目标架构

建议将 `engineering/harness/` 理解为一个 6 层治理结构。

### L0：控制总纲层

新增统一总纲文件，负责定义：

1. harness 管什么、不管什么
2. `workspace / patchs / docs / workflow / template / artifact` 对象模型
3. 真相源矩阵
4. Human / AI / Script 角色边界
5. 规则优先级
6. 例外模型
7. 术语表

### L1：政策规则层

保留 `rules/`，但使其更明确承担“稳定、跨流程复用、MUST / MUST NOT”规则角色。

建议逐步为关键规则引入稳定 ID，例如：

- `SRC-001`：`workspace` 是唯一编译真相源
- `SRC-002`：`patchs` 仅允许受控同步
- `DOC-001`：spec/plan 与长期技术文档分层
- `OBS-001`：harness bash 脚本必须通过 bootstrap 接入
- `EVD-001`：未证据化 baseline 不得宣称为恢复真相源

### L2：配置 / Schema 层

保留 `config/` 目录，并将关键配置逐步双轨化：

- Markdown：解释层
- YAML / JSON：机器层
- schema：合法性约束层

第一批机器化对象：

1. `scope-mapping`
2. `doc-sync-mapping`
3. baseline 状态定义
4. 允许文档分类 / 路由规则

### L3：Workflow Contract 层

保留 `workflows/*/WORKFLOW.md`，但逐步统一结构，至少含：

- Trigger
- Preconditions
- Inputs
- Human confirmation gates
- Outputs / artifacts
- Failure / recovery
- Related policy IDs

### L4：Enforcement 层

由 `lib/` + validator 组成，使规则变成可检查约束。

包括：

- `harness_bootstrap.sh`
- `harness_observability.sh`
- 文档校验器
- 配置校验器
- 脚本静态合规校验器

### L5：Evidence 层

新增 baseline / promotion 的证据模型，使“已验证”“可恢复”可追踪。

---

## 5. 控制面设计

### 5.1 控制总纲

建议新增：

- `engineering/harness/CONTROL-CHARTER.md`

建议章节：

1. 目标边界
2. 对象模型
3. 真相源矩阵
4. Human / AI / Script 职责边界
5. 规则优先级
6. 受控例外
7. 术语表

### 5.2 准入矩阵

建议新增：

- `engineering/harness/config/task-admission-matrix.md`

矩阵用于回答一个问题：

> 当前任务应该直接改文件，还是必须先进入某个 workflow？

矩阵至少覆盖：

- workspace 源码修改
- patchs 归档
- patchs 回退
- patchs→文档同步
- commit / push
- harness 脚本改造

字段至少包括：

- 是否允许直接修改
- 必读规则
- 必经 workflow
- 是否先出 plan
- 是否需要用户确认
- 是否需要 evidence

### 5.3 术语与文档分层

总纲中明确以下概念：

- `archive`：一次归档结果
- `candidate baseline`：已完成部分验证、待晋升
- `promoted baseline`：证据完整、可作为恢复真相源
- `promotion`：从 archive/candidate 晋升为 baseline 的受控过程

同时明确文档分层：

1. **过程型文档**：`docs/specs/`、`docs/plans/`
2. **长期资产型文档**：`docs/01-*`、`docs/02-*` 等长期技术设计文档

---

## 6. 执行面设计

### 6.1 observability 契约对齐

`harness_observability.sh` 需要继续作为执行面的统一维测基座，但要修正当前实现与规则契约漂移。

应完成：

1. `log_result()` 在日志文件中真实输出 `result:` 行
2. `harness_status_emit()` 在日志文件中真实输出 `status=` 行
3. artifact 轮转支持目录型 artifact
4. root 查找逻辑尽量避免在 `harness_bootstrap.sh` 与 `harness_init()` 双份漂移

### 6.2 sync 的 deletion 语义补齐

当前 `sync_code_to_patchs.sh` 已覆盖 modified/new 与 prune，但 tracked deletion 语义需要被显式建模，而不是依赖隐式删除。

建议最小模型：

- `manifest.yaml` 中新增 deletion section
- `sync` 通过 `git diff --diff-filter=D` 扫描 tracked deletion
- deletion 不生成 `.diff`，而是作为显式结构化状态被记录
- `revert` 后续据此理解“此文件在 baseline 中应不存在”

### 6.3 revert 的 path / failure model 收敛

`revert_code_from_patchs.sh` 需解决两类问题：

1. non-repo EXTRA 的 `proj` / `rel` 编码歧义
2. 关键扫描失败时不得继续输出部分失真 plan

建议原则：

- 明确 `proj` 和 `rel` 的编码契约
- 关键扫描失败必须 fail-fast
- upstream 缺失策略在 kernel / aosp / extra 场景中一致化

---

## 7. 配置渐进机器化设计

### 7.1 双轨配置

对以下配置建立双轨：

- `scope-mapping.md` + `scope-mapping.yaml`
- `doc-sync-mapping.md` + `doc-sync-mapping.yaml`

其中：

- `.md`：人类阅读解释
- `.yaml`：机器消费真相源
- `schema/*.json`：格式校验

### 7.2 本轮边界

本轮只要求：

1. YAML / schema 存在
2. validator 能校验 YAML / schema
3. workflow / README 优先引用 YAML 为真相源

本轮不要求所有现有 shell 脚本立即切换为直接消费 YAML。

---

## 8. Validator 设计

建议新增三个职责边界明确的校验器。

### 8.1 `validate_harness_docs.sh`

只负责文档 / 契约层静态一致性：

- README 导航链接存在性
- README 文件清单一致性
- template 中 PlantUML 闭合与占位符规则
- workflow contract 必备章节检查
- 关键术语非法旧表述扫描

不负责：

- bash 语义
- YAML/schema 解析
- 运行时功能正确性

### 8.2 `validate_harness_config.sh`

只负责机器层配置合法性：

- YAML 文件存在性
- schema 校验
- `priority` 冲突
- `match` 空值
- docs 路由路径合法性

不负责：

- README 是否解释正确
- 脚本是否消费配置
- workflow 文档完整性

### 8.3 `validate_harness_scripts.sh`

只负责 bash 实现层静态合规：

- 是否统一通过 `harness_bootstrap.sh`
- 是否调用 `harness_init`
- 是否存在裸 `exit`
- 是否存在裸 `/tmp/`
- 是否直接依赖 `_H_*` / `_h_*`
- 是否存在违反 `script-observability` 的裸 `echo`
- 是否出现未登记退出码

不负责：

- README/template 结构问题
- config schema 合法性
- 运行时逻辑正确性

---

## 9. 回归夹具设计

回归测试不应操作真实项目资产，而应使用 sandbox repo-root + fake `patchs/rpi5` 来模拟 workflow 契约对象。

### 9.1 设计原则

1. fixture 中的 `patchs` 是测试专用隔离镜像，不是复用真实 `patchs/`
2. 脚本仍按 `REPO_ROOT/patchs/rpi5` 的真实路径契约执行
3. 每个测试场景都能独立构造 workspace / upstream / patchs / expected 输出

### 9.2 目录命名

为了避免和生产目录语义混淆，fixture 目录按 workflow 全名命名：

```text
engineering/harness/tests/fixtures/
├── observability/
│   └── basic/
├── sync-code-to-patchs/
│   ├── kernel-delete/
│   ├── kernel-modified-new/
│   └── aosp-non-repo/
└── revert-code-from-patchs/
    ├── non-repo-extra/
    ├── upstream-missing/
    └── verify-matrix/
```

### 9.3 场景说明

#### observability/basic

验证：

- `result:` 行
- `status=` 行
- `latest.log`
- file artifact 轮转
- dir artifact 轮转

#### sync-code-to-patchs/kernel-delete

验证：

- upstream 有文件，workspace 删除该文件
- sync 后 manifest 显式表达 deletion 语义

#### sync-code-to-patchs/kernel-modified-new

验证：

- modified
- new tracked
- new untracked
- excluded artifact

#### sync-code-to-patchs/aosp-non-repo

验证：

- AOSP 非 repo 目录的复制与 prune 语义

#### revert-code-from-patchs/non-repo-extra

验证：

- `aosp:build` + `build/foo.txt` 不发生双前缀路径错误

#### revert-code-from-patchs/upstream-missing

验证：

- upstream 缺失时显式失败，不输出失真 plan

#### revert-code-from-patchs/verify-matrix

验证：

- `FIXED`
- `KEPT`
- `RESIDUAL`
- `NEW-DIFF`

---

## 10. 分阶段实施策略

### P0：止住失控点

#### 治理侧

1. 新增控制总纲
2. 新增准入矩阵
3. 明确术语与文档分层
4. 修模板 / README / 规则冲突

#### 脚本侧

1. 修 `observability` 的结构化日志契约漂移
2. 修 `revert` 的 non-repo EXTRA 路径问题与失败传播
3. 修 `sync` 的 deletion 语义缺口

### P1：把关键约束变成可执行治理

1. 引入 baseline / evidence 状态模型
2. 配置双轨化：Markdown + YAML/schema
3. 引入 docs/config/scripts 三类 validator

### P2：补齐回归与 contract 统一

1. 建最小 fixture 回归夹具
2. 为关键规则引入稳定 ID
3. 统一 workflow contract 结构

---

## 11. 错误处理与验证策略

### 11.1 错误处理原则

#### 控制面错误

例如：

- 任务未命中准入矩阵
- workflow 缺少 policy ID
- config 路由冲突
- baseline 状态不明

处理原则：

- 拒绝继续
- 输出修复建议
- 不允许静默 fallback

#### 执行面错误

例如：

- upstream 缺失
- plan 生成失败
- verify 有 residual/new-diff
- deletion 语义不完整

处理原则：

- 保留完整现场
- 输出明确退出码
- 保持可重试

### 11.2 验证分层

#### V1：静态治理验证

- docs validator
- README/规则/workflow/template 一致性

#### V2：脚本行为验证

- sync / revert / observability 功能测试

#### V3：证据链验证

- archive → candidate → promoted 的状态推进

#### V4：任务路由验证

- AI 是否稳定命中正确 rules / workflow / confirmation gate

---

## 12. 完成定义（DoD）

### 12.1 治理 DoD

1. 存在 `CONTROL-CHARTER.md`
2. 存在任务准入矩阵
3. 文档分层被正式定义
4. 关键规则具备 ID 化方案
5. 术语表定义 `patchs / archive / baseline / promotion`
6. 显式冲突清零：
   - PlantUML 规则 vs 模板
   - 模板路径示例 vs 自检规则
   - README 清单漂移

### 12.2 脚本 DoD

1. `harness_observability.sh` 与 `script-observability` 契约一致
2. `revert_code_from_patchs.sh` 修复 non-repo EXTRA 路径问题
3. `sync_code_to_patchs.sh` 补齐 deletion 语义
4. 关键扫描失败不再产生部分失真 plan

### 12.3 验证 DoD

1. docs/config/scripts 三类 validator 可运行
2. 至少一组最小 fixture 能覆盖 observability / sync / revert 核心路径
3. baseline 证据模型已在设计中固定对象与状态语义

---

## 13. 预期结果

实施完成后，`engineering/harness/` 将不再只是规则与脚本的集合，而是一个具备以下特征的受控工程系统：

1. 有统一控制总纲
2. 有任务准入矩阵
3. 有更清晰的规则与 workflow 映射
4. 有关键脚本 correctness 修复
5. 有机器可检查的 validator 入口
6. 有 baseline / evidence 的演进路径
7. 有最小回归夹具支撑后续持续演进
