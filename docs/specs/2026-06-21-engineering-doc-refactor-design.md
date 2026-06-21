# Engineering 文档重构设计

> **创建日期**：2026-06-21
> **状态**：待用户 review
> **前置阅读**：本文件是最终设计定稿，所有决策点均已与用户逐轮确认。

---

## 一、目标与原则

当前项目 engineering/ 下文档存在三大问题：

1. **README 格式千奇百怪**：29 份 README 长度从 3 行到 235 行不等，章节结构无统一约束。
2. **SSOT 违规**：同一份说明在多个文档重复（Windows .bat 注意事项在两处复制、loop/README 与 loop/WORKFLOW 大量重叠、harness↔loop 边界在两处各写一遍）。
3. **命名风格混乱**：全大写连字符（`CONTROL-CHARTER.md`）、下划线小写（`rp5_serial_protocol.md`）、连字符小写（`module-template.md`）三种风格混用。

本重构遵循以下原则：

| 原则 | 含义 |
|------|------|
| **减少文档数量** | 消除可融入 README 的独立文档（CONTROL-CHARTER、rp5-serial/WORKFLOW） |
| **统一命名** | 全小写连字符（除 README/WORKFLOW 固定入口、rules 按语义命名外） |
| **单一事实源** | 跨目录重复内容提取到一处，其余改为链接引用 |
| **模板约束** | 新增 2 份模板，固化 README 与 rules 的章节结构 |
| **解决 README 膨胀** | AI 三层读取（L0 大纲表 / L1 导航 / L2 内容精准读） |

---

## 二、决策记录

以下决策均已经与用户逐轮确认，为设计基准：

| 编号 | 决策点 | 最终选择 | 备选 |
|------|--------|---------|------|
| D-WF | WORKFLOW.md 去留 | harness/workflows/*（4 份）、loop/WORKFLOW.md、loop/workflows/lcview-adb-run/WORKFLOW.md **保留**；rp5-serial/WORKFLOW.md **融入 README** | 全保留 / 全融入 / 折中 |
| D-CC | CONTROL-CHARTER.md 去留 | **C1 融入 harness/README.md** | C2 重命名 / C3 保留 |
| D-NN | 文件命名统一规则 | **N1 全小写连字符** | N2 下划线 / N3 不统一 |
| D-R1 | rp5-serial README 承载 | **R1 按模板分章节** | R2 仅导航 |
| D-TM | README 模板形态 | **T4 单一核心模板 + 专化扩展块** | T1 单一 / T2 分档 / T3 模式示例 |
| D-CC-CORE | 核心模板必选章节 | **A1 认同 5 节**（定位/大纲/目录说明/使用方式/关联资源） | — |
| D-CC-LIGHT | 占位 README 最小实现 | **B1 严格统一**（占位也按核心模板写齐 5 节） | B2 放宽 |
| D-CC-TOC | 大纲表粒度 | **C1 强制列全部章节**（核心+扩展） | C2 仅核心 |
| D-CC-RES | 关联资源类型枚举 | **D1 固定枚举** | D2 自由填写 |
| D-BAT | .bat 注意事项去重 | **D3 留在 harness/scripts/README，loop/scripts 改链接引用** | D1 lib / D2 新 rule |
| D-RT | rules-template 形态 | **RT4 build-reference 移出 rules/**，rules/ 只留约束类，单一约束模板 | RT1/RT2/RT3 |
| D-RT2 | rules-template 附录章节 | **R-T2 5 节核心 + 允许附录** | R-T1 严格 5 节 |
| D-REF | reference/ 初始内容 | **仅迁 build-reference.md** | — |

---

## 三、文档受众分类

本次重构的核心方法论：**区分"给人看的"与"给工具看的"文档**。

| 受众 | 文档 | 处置原则 |
|------|------|---------|
| **工具（AI/脚本/validator）** | `workflows/*/WORKFLOW.md`（被 `.opencode/commands/*.md` `@` 注入）、`rules/*.md`（被 AGENTS.md 加载）、`config/*.yaml`（机器读取）、`templates/*.md`（被 sync-patchs-to-doc 消费） | 保留独立文件，命名稳定，结构化字段（front matter/RID）满足工具消费 |
| **人** | README.md（导航/理解/上手）、docs/specs/、docs/plans/、业务文档 | 按模板组织，精炼可读 |
| **两者** | rules、templates（人也要理解为什么这样约束） | 工具消费字段 + 人读正文分层 |

**工具消费事实核查**（已完成）：

| 文档 | 工具消费方 | 消费方式 |
|------|----------|---------|
| `harness/workflows/*/WORKFLOW.md`（4 份） | `.opencode/commands/{git-push-to-server,revert-code-from-patchs,sync-code-to-patchs,sync-patchs-to-doc}.md` | `@path/WORKFLOW.md` 注入 AI 上下文 |
| `loop/WORKFLOW.md` | `.opencode/commands/le.md` | `@engineering/loop/WORKFLOW.md` 注入 |
| `workflows/*/WORKFLOW.md` | `validate_harness_docs.sh` Step4 | 校验 front matter（name/description） |
| `rules/*.md` | `AGENTS.md` | 声明为"改动前必须加载" |
| `templates/*.md` | `sync-patchs-to-doc` workflow | 作为只读契约约束 AI 生成文档结构 |
| `config/*.yaml` | 多个 workflow 脚本 | 机器读取的配置数据 |
| 所有 `README.md` | `validate_harness_docs.sh` Step1/2 | 校验链接可达性 + 文件清单一致性 |

---

## 四、新增模板

### 4.1 engineering-readme-template.md

**存放位置**：`harness/templates/engineering-readme-template.md`

**模板形态**：T4（单一核心模板 + 专化扩展块）

#### 4.1.1 核心骨架（所有 engineering 下 README 必遵的 5 节）

````markdown
# {目录名}

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：{一句话}
- **职责边界**：做什么 / 不做什么
- **上下游依赖**：{依赖谁 / 被谁依赖}（无依赖时写"无"）

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 子目录/文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 快速开始、入口、参数 | 实际使用时 |
| [关联资源](#关联资源) | 设计文档、规则、workflow 链接 | 深入理解时 |
| ...（已启用的扩展块也在此列出） | ... | ... |

## 目录说明

| 子目录/文件 | 职责 | 关键入口/被谁引用 |
|------------|------|------------------|
| `xxx/` | {一句话} | {脚本/配置/规则链接} |

> 子目录自身的细节见其 `README.md`，本表只给一句话索引。

## 使用方式

> 无可执行入口的目录（如纯文档/配置承载层）写：
> "本目录无可执行入口，仅作为 {X} 的承载层。"

### 快速开始

（最小可运行示例，含前置条件 + 命令）

### 入口清单

| 入口 | 作用 | 调用方式 |
|------|------|---------|
| {脚本/命令} | {一句话} | {示例} |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/xxx.md` | {一句话} |
| 关联规则 | `rules/xxx.md`（RID-NNN） | {何时加载} |
| 关联 workflow | `workflows/xxx/` | {触发场景} |
| 关联配置 | `config/xxx.yaml` | {作用} |
````

**章节约束力**：
- 5 个必选章节所有 README 必遵，即使内容仅一行（B1 决策）
- 每个章节有最小必填字段
- 大纲表强制列出全部章节，含已启用的扩展块（C1 决策）
- 关联资源类型固定枚举：设计文档/规则/workflow/配置（D1 决策）

#### 4.1.2 扩展块（按目录类型选配）

以下扩展块在核心 5 节之外按需追加，追加后必须同步登记到大纲表：

| 扩展块 | 适用目录 | 来源/参考 | 内容 |
|--------|---------|----------|------|
| **快速导航** | harness/README、engineering/README、loop/README | harness/README 现有模式 | "我要做的事 → 先读哪里"表 |
| **控制总纲** | 仅 harness/README | 原 CONTROL-CHARTER.md 融入 | 目标边界 / 对象模型 / 真相源矩阵 / 职责边界 / 优先级链 |
| **边界与依赖** | engineering/README、harness/README、loop/connection/README | engineering/README 现有 | 单向依赖、能力归属判定 |
| **公共 API 速查** | harness/lib/README、harness/README | harness/README 现有 | 函数清单 + 用法 + 公私边界 |
| **运行流程** | rp5-serial/README（原 WORKFLOW 融入）、loop/README | rp5-serial/WORKFLOW.md | 拓扑 / 启动方式 / 使用方式 |
| **字段速查** | harness/config/README | config/README 现有 | YAML 字段说明 |
| **何时更新** | 有子目录且子目录易变动的目录 | config/README 现有 | 触发条件 + 操作清单 |
| **README 同步约定** | harness/README、engineering/README | harness/README 现有 | 文件变更 → README 更新清单 |
| **设计原则** | loop/connection/README | connection/README 现有 | 分层 / 契约原则 |

### 4.2 rules-template.md

**存放位置**：`harness/templates/rules-template.md`

**模板形态**：R-T2（5 节核心 + 允许附录）

````markdown
# {规则名称}

> **规则 ID**：`{PREFIX-NNN}`（多条逐行列出）
> - `{PREFIX-NNN}`：{一句话约束说明}

## 适用范围与加载时机

- **适用对象**：{受约束的文件/操作类型}
- **加载时机**：{何时必须加载本规则}

## 强制要求（MUST）

1. **MUST** {行为} —— {原因/目的}
2. **MUST** {行为} —— {原因/目的}
...

## 禁止行为（MUST NOT）

1. **MUST NOT** {行为} —— {原因}
2. **MUST NOT** {行为} —— {原因}
...

## 例外清单

> 无例外时写："无例外。违反本规则的任何行为都需先更新本规则。"

| 场景 | 允许的行为 | 不允许的行为 |
|------|----------|------------|
| {场景} | {行为} | {行为} |

---

## 附录：{标题}

> 以下为参考性内容（API / 配置格式 / 模式选择等），不属于强制约束。

（自由组织）
````

**章节约束力**：
- 前 5 节必选（规则 ID / 适用范围 / MUST / MUST NOT / 例外清单）
- 例外清单必选，即使写"无例外"——强制作者显式声明边界
- 附录章节允许（R-T2 决策），位于例外清单之后，必须标注"参考性内容，不属于强制约束"

**与现有规则的差异校准**：

| 现有规则 | 模板外的章节 | 按 template 的处置 |
|---------|------------|------------------|
| script-observability.md | 错误捕获模式选择（模式 A/B） | 归入附录 |
| script-observability.md | API 参考（函数清单） | 归入附录 |
| path-management.md | 路径工具 API（shell/python/bat） | 归入附录 |
| path-management.md | 配置文件格式 | 归入附录 |
| source-code-modify.md | 改动规则（操作流程表） | 融入 MUST |
| doc-paths.md | 路径映射表 | 融入 MUST |
| parallel-strategy.md | 拆分原则 / 禁止并行的场景 | 融入 MUST / MUST NOT |

---

## 五、文件级处置全量清单

### 5.1 新建

| 文件 | 说明 |
|------|------|
| `harness/templates/engineering-readme-template.md` | §4.1 核心 5 节 + 扩展块说明 |
| `harness/templates/rules-template.md` | §4.2 核心 5 节 + 附录 |
| `harness/reference/`（目录） | 参考文档承载层 |
| `harness/reference/README.md` | 按 engineering-readme-template 写齐核心 5 节 |

`harness/reference/README.md` 预期内容：

```markdown
# Reference

## 定位
- 是什么：harness 工程参考文档承载层，存放命令模板、操作指南等非约束性参考资料
- 职责边界：承载"正确做法参考"，不承载"强制约束规则"（约束规则在 ../rules/）
- 上下游依赖：被 AGENTS.md、rules/README 引用

## 大纲
（按核心模板大纲表格式，列出 5 节）

## 目录说明
| 文件 | 类型 | 被谁引用 |
|------|------|---------|
| build-reference.md | RPI5 编译命令参考 | AGENTS.md（编译时加载）|

## 使用方式
本目录无可执行入口，按需读取参考资料。

## 关联资源
| 类型 | 路径 | 说明 |
|------|------|------|
| 关联配置 | ../config/harness-paths.conf | 编译路径定义 |
| 关联脚本 | ../scripts/mk_rpi5_full_image.sh | build-reference 的源提取 |
```

### 5.2 移动

| 源 | 目标 | 说明 |
|----|------|------|
| `harness/rules/build-reference.md` | `harness/reference/build-reference.md` | RT4 重新定性为参考文档；RID `BLD-001~008` 保留不变 |

### 5.3 删除（内容融入 README）

| 文件 | 融入目标 | 说明 |
|------|---------|------|
| `harness/CONTROL-CHARTER.md` | `harness/README.md` 新增「控制总纲」章节 | C1 决策 |
| `loop/connection/providers/rp5-serial/WORKFLOW.md` | `rp5-serial/README.md` 新增「运行流程」章节 | R1 决策 |

### 5.4 重命名

| 源 | 目标 | 说明 |
|----|------|------|
| `loop/connection/protocol/rp5_serial_protocol.md` | `loop/connection/protocol/rp5-serial-protocol.md` | N1 全小写连字符 |

### 5.5 保留不变

| 文件 | 理由 |
|------|------|
| `harness/workflows/{git-push-to-server,sync-code-to-patchs,revert-code-from-patchs,sync-patchs-to-doc}/WORKFLOW.md`（4 份） | 规则约束 + 被 `.opencode/commands/*.md` `@` 消费 + validator 校验 |
| `loop/WORKFLOW.md` | 被 `.opencode/commands/le.md` `@` 消费 |
| `loop/workflows/lcview-adb-run/WORKFLOW.md` | 与 harness/workflows 保持一致性 |
| `rules/{source-code-modify,doc-paths,plantuml,parallel-strategy,script-observability,path-management}.md`（6 份） | 约束类规则；后续按 rules-template 校准结构 |
| `harness/templates/{module-template,module-readme-template,diagnosis-report-template}.md`（3 份） | 已有模板，不动 |

### 5.6 按模板重构（29 份 README.md）

见 §六「README 重构分档」。

---

## 六、README 重构分档

29 份 README 按当前行数与章节复杂度分三档，每档有不同的重构动作：

### 6.1 重型（导航 + 约束 + 流程）

| 目录 | 当前行数 | 核心动作 |
|------|---------|---------|
| `harness/README.md` | 87 → ~200 | 融入 CONTROL-CHARTER 为「控制总纲」扩展块；保留快速导航 / API 速查 / README 同步约定；更新所有 CONTROL-CHARTER 链接为 `#控制总纲` 锚点 |
| `loop/README.md` | 235 → ~80 | 精简：仅留定位 / 大纲 / 目录说明 / 快速开始 / 关联资源；以下内容迁回 `loop/WORKFLOW.md`：架构图、core 模块清单、断言类型、serial_context 字段表、串口 transcript、/le 失败诊断约束、run_on 执行平面、system.network_adbd 场景细节、features.lcview 场景细节 |

### 6.2 中型（导航 + 文件清单 + 专化内容）

| 目录 | 当前行数 | 核心动作 |
|------|---------|---------|
| `engineering/README.md` | 42 | 按核心 5 节校准；保留「边界与依赖」扩展块（单向依赖、能力归属判定） |
| `harness/config/README.md` | 72 | 按核心 5 节校准；保留「字段速查」「任务准入矩阵」「何时更新」 |
| `harness/lib/README.md` | 55 | 按核心 5 节校准；保留「公共 API 速查」 |
| `harness/scripts/README.md` | 80 | 按核心 5 节校准；保留 Windows .bat 注意事项作为 SSOT（D3） |
| `harness/templates/README.md` | 17 | 按核心 5 节校准；新增 2 份模板登记 |
| `harness/workflows/README.md` | 28 | 按核心 5 节校准；保留「结构约定」 |
| `loop/scripts/README.md` | 187 → ~60 | 按核心 5 节校准；.bat 注意事项改为链接引用 harness/scripts/README.md（D3） |
| `loop/connection/README.md` | 17 | 按核心 5 节校准；保留「设计原则」 |
| `loop/connection/providers/rp5-serial/README.md` | 118 → ~150 | 融入 WORKFLOW.md 内容为「运行流程」扩展块；移除"详见 WORKFLOW.md"指针 |
| `loop/connection/protocol/README.md` | 20 | 按核心 5 节校准；链接更新为 `rp5-serial-protocol.md` |
| `patchs/rpi5/README.md` | 230 | 按核心 5 节校准；保留文件映射表（由 sync-code-to-patchs 维护） |
| `engineering/output/README.md` | 36 | 按核心 5 节校准 |

### 6.3 轻型（极简导航，严格按 B1 写齐 5 节）

| 目录 | 当前行数 | 核心动作 |
|------|---------|---------|
| `loop/controller/README.md` | 3 | 严格按核心 5 节写齐（B1） |
| `loop/contracts/README.md` | 3 | 同上 |
| `loop/workflows/README.md` | 3 | 同上 |
| `loop/workflows/lcview-adb-run/README.md` | 5 | 按核心 5 节校准；保留指向 WORKFLOW.md 的关联资源 |
| `harness/workflows/{git-push-to-server,sync-code-to-patchs,revert-code-from-patchs,sync-patchs-to-doc}/README.md`（4 份） | 3 | 按核心 5 节校准；保留指向 WORKFLOW.md 的关联资源 |
| `loop/connection/profiles/README.md` | 29 | 按核心 5 节校准 |
| `loop/connection/profiles/devices/rp5/README.md` | 16 | 按核心 5 节校准 |
| `loop/connection/providers/adb/README.md` | 23 | 按核心 5 节校准 |
| `harness/reference/README.md`（新建） | — | 按 §5.1 预期内容 |

---

## 七、AI 三层读取机制（解决 README 膨胀）

本机制写入 `engineering-readme-template.md` 的「大纲」章节规范中。

### 7.1 三层定义

| 层 | 范围 | 行数预算 | AI 读取策略 |
|----|------|---------|-----------|
| **L0 大纲层** | README 顶部「大纲」表 + AI 读取指引 | ~20 行 | AI 注入后先读此表，判断需要哪些章节 |
| **L1 导航层** | 定位 + 目录说明 | ~30 行 | 找到正确入口 |
| **L2 内容层** | 控制总纲 / API 速查 / 运行流程 / 快速开始等 | 无上限 | 按 L0 指引精准读取相关章节 |

### 7.2 模板约束

`engineering-readme-template.md` 在「大纲」章节强制要求：

1. 大纲表必须列出**全部章节**（核心 5 节 + 已启用扩展块）
2. 每行三列：章节标题 / 内容摘要 / 何时读取
3. 大纲表上方嵌入显式 AI 读取指引（见 §4.1.1 模板正文）
4. "何时读取"列给出**明确判断条件**，例如：
   - "涉及优先级裁决、真相源判定时" → 控制总纲
   - "改 bash 脚本时" → 关联 rules/script-observability.md
   - "首次进入本目录" → 定位 + 目录说明

### 7.3 效果

即使 harness/README.md 膨胀到 200 行，AI 实际只需读 L0（20 行）+ L2 某章节（20-30 行），而非全量 200 行。

---

## 八、去重与单一事实源

| 重复内容 | 当前位置 | 处置 |
|---------|---------|------|
| Windows .bat 注意事项（~50 行） | harness/scripts/README.md + loop/scripts/README.md | 保留在 harness/scripts/README.md（SSOT）；loop/scripts/README.md 改为链接引用（D3） |
| harness↔loop 边界（单向依赖、能力归属） | engineering/README.md + harness/README.md | 在 engineering/README.md 单一承载；harness/README 与 loop/README 链接引用 |
| loop 架构 / core 模块 / serial_context / 诊断约束 | loop/README.md + loop/WORKFLOW.md | 迁回 loop/WORKFLOW.md（SSOT）；loop/README.md 仅留导航 + 快速开始 |
| 优先级链引用 CONTROL-CHARTER | rules/README.md、config/README.md、harness/README.md | CONTROL-CHARTER 融入 harness/README.md#控制总纲；其余改为锚点链接 |

---

## 九、命名统一规则

| 类型 | 规则 | 示例 |
|------|------|------|
| 目录入口 | `README.md`（大写固定） | 全部目录 |
| 工作流契约 | `WORKFLOW.md`（大写固定） | 仅 `workflows/*/` 和被 `@` 的目录 |
| 规则文档 | 按语义命名，小写连字符 | `source-code-modify.md` |
| 参考文档 | 按语义命名，小写连字符 | `build-reference.md` |
| 协议/设计文档 | 小写连字符 | `rp5-serial-protocol.md` |
| 模板文档 | `xxx-template.md`，小写连字符 | `engineering-readme-template.md` |

---

## 十、同步影响项

| 受影响项 | 改动内容 |
|---------|---------|
| `AGENTS.md` | "RPI5 编译参考"路径从 `rules/build-reference.md` → `reference/build-reference.md` |
| `harness/README.md` | 快速导航的"编译 RPI5"行更新路径；新增「控制总纲」章节（原 CONTROL-CHARTER 全文） |
| `harness/rules/README.md` | 文件说明表删除 BLD 行；BLD 迁移说明加入 reference/README 索引；优先级链引用改为 `../README.md#控制总纲` |
| `harness/config/README.md` | 优先级链引用更新（如有引用 CONTROL-CHARTER） |
| `harness/templates/README.md` | 文件清单新增 engineering-readme-template.md 与 rules-template.md |
| `harness/scripts/validate_harness_docs.sh` | Step3 扫描目标移除 CONTROL-CHARTER 特例（README 扫描已覆盖）；扫描范围确认包含 reference/ 目录 |
| `loop/connection/protocol/README.md` | 链接 `rp5_serial_protocol.md` → `rp5-serial-protocol.md` |
| `loop/connection/providers/rp5-serial/README.md` | 融入 WORKFLOW 内容；移除"详见 WORKFLOW.md"指针 |
| `loop/scripts/README.md` | .bat 注意事项改为链接引用 harness/scripts/README.md |
| `.opencode/commands/*.md`（5 份） | **无需改动**（保留的 WORKFLOW.md 路径不变） |

---

## 十一、实施阶段

| 阶段 | 内容 | 文件数 | 前置依赖 |
|------|------|--------|---------|
| **1. 模板与 reference/ 建立** | 新建 engineering-readme-template.md、rules-template.md、reference/ 目录 + reference/README.md | 3 新建 + 1 新目录 | 无 |
| **2. build-reference 迁移** | git mv rules/build-reference.md → reference/；同步 AGENTS.md / rules/README / reference/README / harness/README 快速导航 | 1 移动 + 4 更新 | 阶段 1 |
| **3. CONTROL-CHARTER 融入** | harness/README.md 重写（融入 CHARTER 全文为「控制总纲」章节）；删除 CONTROL-CHARTER.md；更新 validator；更新 rules/README、config/README 的链接 | 1 重写 + 1 删除 + 3 更新 | 阶段 2 |
| **4. rp5-serial 融合 + 协议重命名** | rp5-serial/README.md 重写（融入 WORKFLOW 为「运行流程」）；删除 rp5-serial/WORKFLOW.md；重命名 rp5_serial_protocol.md → rp5-serial-protocol.md；更新 protocol/README 链接 | 1 重写 + 1 删除 + 1 重命名 + 1 更新 | 阶段 1 |
| **5. README 全量校准 + 去重** | loop/README.md 精简去重（内容迁回 loop/WORKFLOW.md）；中型/轻型 README 按模板校准；loop/scripts .bat 改链接引用；engineering/README 边界 SSOT 化 | ~15 重构 | 阶段 1-4 |

每阶段完成后应运行 `validate_harness_docs.sh` 验证链接一致性。

---

## 十二、遗留风险与后续计划细化点

以下在 writing-plans 阶段需进一步细化：

1. **validate_harness_docs.sh 扫描范围**：Step1/2 默认扫 harness 全目录，应已覆盖 reference/；Step3 扫描目标需确认是否含 reference/（当前实现扫描 `$HARNESS_DIR/templates` 与 `$HARNESS_DIR` 下所有 .md，应已覆盖）。
2. **loop/README.md 与 loop/WORKFLOW.md 的内容切分边界**：需逐章节判断归属（架构图、core 模块清单、断言类型、serial_context、诊断约束、run_on 执行平面、system.network_adbd 场景、features.lcview 场景）。原则：README 留导航 + 快速开始，流程细节留 WORKFLOW.md。
3. **CONTROL-CHARTER 融入后的锚点命名**：「控制总纲」章节锚点需稳定，供 rules/README、config/README 引用。
4. **rules/ 6 份规则按 rules-template 校准**：本 spec 只定义模板，校准工作可在阶段 5 之后单独排期，不阻塞主重构。
