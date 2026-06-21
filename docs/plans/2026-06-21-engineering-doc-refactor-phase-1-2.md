## 阶段 1：模板与 reference/ 建立

> **依赖**：无（本阶段为整个重构的基础）。
> **产出**：2 份新模板 + reference/ 承载层及其 README。
> **验证基准**：每 Task 完成后运行 `bash engineering/harness/scripts/validate_harness_docs.sh`，预期 `verdict=PASS`（允许既有告警，但新增文件不得引入新告警）。

---

### Task 1：创建 engineering-readme-template.md

**Files:**
- Create: `engineering/harness/templates/engineering-readme-template.md`

- [ ] **Step 1：创建模板文件，写入完整正文**

将以下完整内容写入 `engineering/harness/templates/engineering-readme-template.md`：

````markdown
# Engineering README 模板

> 本模板定义 engineering/ 下所有 README.md 的章节结构。新增或重构 README 时，
> 复制「核心 5 节骨架」并按需追加「扩展块」。模板为只读契约，被 `sync-patchs-to-doc`
> workflow 消费，改动需用户确认。

---

## AI 三层读取机制

engineering/ 下 README 可能膨胀到 200+ 行。为避免 AI 全量解析，强制采用三层读取：

| 层 | 范围 | 行数预算 | AI 读取策略 |
|----|------|---------|-----------|
| **L0 大纲层** | README 顶部「大纲」表 + AI 读取指引 | ~20 行 | AI 注入后先读此表，判断需要哪些章节 |
| **L1 导航层** | 定位 + 目录说明 | ~30 行 | 找到正确入口 |
| **L2 内容层** | 控制总纲 / API 速查 / 运行流程 / 快速开始等 | 无上限 | 按 L0 指引精准读取相关章节 |

效果：即使 harness/README.md 膨胀到 200 行，AI 实际只需读 L0（20 行）+ L2 某章节（20-30 行），而非全量 200 行。

大纲表约束：
1. 大纲表必须列出**全部章节**（核心 5 节 + 已启用扩展块）。
2. 每行三列：章节标题 / 内容摘要 / 何时读取。
3. 大纲表上方嵌入显式 AI 读取指引（见骨架正文）。
4. "何时读取"列给出**明确判断条件**，例如：
   - "涉及优先级裁决、真相源判定时" → 控制总纲
   - "改 bash 脚本时" → 关联 rules/script-observability.md
   - "首次进入本目录" → 定位 + 目录说明

---

## 核心 5 节骨架（所有 engineering 下 README 必遵）

以下为复制起点，`{...}` 为待替换占位：

```markdown
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
```

### 章节约束力

- 5 个必选章节所有 README 必遵，即使内容仅一行（决策 B1：严格统一）。
- 每个章节有最小必填字段（见骨架正文）。
- 大纲表强制列出全部章节，含已启用的扩展块（决策 C1）。
- 关联资源类型固定枚举：设计文档 / 规则 / workflow / 配置（决策 D1）。

---

## 扩展块（按目录类型选配）

以下扩展块在核心 5 节之外按需追加。**追加后必须同步登记到大纲表**（决策 C1）。

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
````

- [ ] **Step 2：运行 validator 确认无新告警**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：`verdict=PASS` 或未因新文件新增告警。重点关注 Step2 是否报告 `engineering-readme-template.md` 未在 templates/README.md 登记——此告警将在阶段 2 Task 5 消除，此处可暂时容忍。

- [ ] **Step 3：提交**

```bash
git add engineering/harness/templates/engineering-readme-template.md
git commit -m "新增(templates): 建立 engineering-readme 模板（核心5节+扩展块+AI三层读取机制）"
```

---

### Task 2：创建 rules-template.md

**Files:**
- Create: `engineering/harness/templates/rules-template.md`

- [ ] **Step 1：创建模板文件，写入完整正文**

将以下完整内容写入 `engineering/harness/templates/rules-template.md`：

````markdown
# Rules 规则文档模板

> 本模板定义 engineering/harness/rules/ 下所有规则文档的章节结构。
> 模板形态：R-T2（5 节核心 + 允许附录）。新增或重构规则时复制「核心骨架」。

---

## 核心骨架（5 节必选 + 附录可选）

以下为复制起点，`{...}` 为待替换占位：

```markdown
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
```

### 章节约束力

- 前 5 节必选（规则 ID / 适用范围 / MUST / MUST NOT / 例外清单）。
- 例外清单必选，即使写"无例外"——强制作者显式声明边界。
- 附录章节允许（决策 R-T2），位于例外清单之后，必须标注"参考性内容，不属于强制约束"。

---

## 现有规则差异校准指引

现有 6 份规则后续按本模板校准时，模板外章节的处置：

| 现有规则 | 模板外的章节 | 按 template 的处置 |
|---------|------------|------------------|
| script-observability.md | 错误捕获模式选择（模式 A/B） | 归入附录 |
| script-observability.md | API 参考（函数清单） | 归入附录 |
| path-management.md | 路径工具 API（shell/python/bat） | 归入附录 |
| path-management.md | 配置文件格式 | 归入附录 |
| source-code-modify.md | 改动规则（操作流程表） | 融入 MUST |
| doc-paths.md | 路径映射表 | 融入 MUST |
| parallel-strategy.md | 拆分原则 / 禁止并行的场景 | 融入 MUST / MUST NOT |
````

- [ ] **Step 2：运行 validator 确认无新告警**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：`verdict=PASS`。rules-template.md 未在 templates/README.md 登记的告警将在阶段 2 Task 5 消除。

- [ ] **Step 3：提交**

```bash
git add engineering/harness/templates/rules-template.md
git commit -m "新增(templates): 建立 rules 模板（5节核心+附录+现有规则校准指引）"
```

---

### Task 3：创建 reference/ 目录及 README.md

**Files:**
- Create: `engineering/harness/reference/`（目录）
- Create: `engineering/harness/reference/README.md`

- [ ] **Step 1：创建目录**

```bash
mkdir -p engineering/harness/reference
```

- [ ] **Step 2：创建 README.md，写入完整正文（按 engineering-readme-template 核心 5 节）**

将以下完整内容写入 `engineering/harness/reference/README.md`：

```markdown
# Reference

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：harness 工程参考文档承载层，存放命令模板、操作指南等非约束性参考资料
- **职责边界**：承载"正确做法参考"，不承载"强制约束规则"（约束规则在 `../rules/`）
- **上下游依赖**：被 `AGENTS.md`、`../rules/README.md` 引用

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 读取方式 | 实际使用时 |
| [关联资源](#关联资源) | 配置、脚本链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 被谁引用 |
|------|------|---------|
| `build-reference.md` | RPI5 AOSP/内核编译命令参考（BLD-001~008） | `AGENTS.md`（编译时加载）、`../rules/README.md`（索引） |

> 本目录仅承载参考文档，不承载约束规则。规则约束见 `../rules/`。

## 使用方式

本目录无可执行入口，仅作为工程参考文档的承载层。按需读取对应参考文档即可。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联配置 | `../config/harness-paths.conf` | 编译路径定义（KERNEL_SRC / KERNEL_OUT 等） |
| 关联脚本 | `../scripts/mk_rpi5_full_image.sh` | build-reference 的命令源提取 |
| 关联规则 | `../rules/path-management.md`（PATH-001） | 编译环境路径加载规则 |
```

> **注意**：此时 `build-reference.md` 尚未迁入（阶段 2 Task 4 执行 `git mv`）。目录说明表中提前登记是为了迁移后立即可用；validator Step1 会因链接目标暂不存在而告警，此告警在阶段 2 Task 4 完成后自动消除。

- [ ] **Step 3：运行 validator（容忍 build-reference.md 链接暂缺告警）**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：可能出现 `reference/README.md` 中 `build-reference.md` 链接不存在的告警（因文件尚未迁入）。此告警在阶段 2 Task 4 完成后自动消除，此处可容忍。其余应 `PASS`。

- [ ] **Step 4：提交**

```bash
git add engineering/harness/reference/README.md
git commit -m "新增(reference): 建立参考文档承载层 README（按核心5节模板）"
```

---

## 阶段 2：build-reference 迁移

> **依赖**：阶段 1 全部完成（reference/ 目录与 README.md 已存在，2 份模板已建立）。
> **产出**：build-reference.md 从 rules/ 迁至 reference/；全部引用路径同步更新；templates/README.md 登记 2 份新模板。
> **验证基准**：每 Task 完成后运行 `bash engineering/harness/scripts/validate_harness_docs.sh`，预期 `verdict=PASS` 且无遗留告警。

---

### Task 4：迁移 build-reference.md 并同步全部引用

**Files:**
- Move: `engineering/harness/rules/build-reference.md` → `engineering/harness/reference/build-reference.md`
- Modify: `AGENTS.md`（第 32 行 RPI5 编译参考路径）
- Modify: `engineering/harness/rules/README.md`（文件说明表删除 BLD 行 + 优先级链引用更新）
- Modify: `engineering/harness/README.md`（快速导航"编译 RPI5"行路径）

- [ ] **Step 1：重新阅读受影响文件与目录的当前内容**

执行者必须先完整阅读以下文件，理解当前内容与上下文，再进行精准修改（而非机械替换）：

1. `engineering/harness/rules/build-reference.md`——确认头部 front matter（RID、适用范围、参考来源），确保迁移后内容完整不丢失。
2. `AGENTS.md`——理解"RPI5 编译参考"段落的完整上下文。
3. `engineering/harness/rules/README.md`——理解文件说明表结构、规则 ID 说明段（BLD 前缀定义）、底部优先级链引用。
4. `engineering/harness/README.md`——理解快速导航表结构、"编译 RPI5"行的上下文。

```bash
# 快速确认 build-reference.md 总行数，确保迁移后行数一致
wc -l engineering/harness/rules/build-reference.md engineering/harness/reference/build-reference.md
```

- [ ] **Step 2：执行 git mv**

```bash
git mv engineering/harness/rules/build-reference.md engineering/harness/reference/build-reference.md
```

- [ ] **Step 3：更新 AGENTS.md 的 RPI5 编译参考路径**

将 `AGENTS.md` 第 32 行：

```
涉及 RPI5 AOSP/内核编译时，必须先加载 [engineering/harness/rules/build-reference.md](engineering/harness/rules/build-reference.md)。
```

改为：

```
涉及 RPI5 AOSP/内核编译时，必须先加载 [engineering/harness/reference/build-reference.md](engineering/harness/reference/build-reference.md)。
```

> 注意：该段落第 33 行的描述文字（"该规则记录了本项目正确的编译命令与约束……"）无需改动，仅路径变更。

- [ ] **Step 4：更新 rules/README.md——删除 BLD 行 + 更新优先级链引用**

在 `engineering/harness/rules/README.md` 中做两处修改：

**修改 4a：文件说明表删除 BLD 行**

删除文件说明表中的最后一行（第 23 行）：

```markdown
| `BLD-001` ~ `BLD-008` | [build-reference.md](./build-reference.md) | RPI5 AOSP/内核编译命令参考与约束，含完整命令模板、路径约束、mode 选择策略 | 涉及 RPI5 AOSP/内核编译时 |
```

**修改 4b：更新底部优先级链引用**

将文件末尾的引用说明（第 26 行）：

```markdown
> 规则优先级遵循 [CONTROL-CHARTER.md](../CONTROL-CHARTER.md)：用户指令 > Control Charter > `rules/*.md` > `workflows/*/WORKFLOW.md` > README。
```

改为：

```markdown
> 规则优先级遵循 [../README.md#控制总纲](../README.md#控制总纲)：用户指令 > 控制总纲 > `rules/*.md` > `workflows/*/WORKFLOW.md` > README。
```

> **说明**：CONTROL-CHARTER 将在阶段 3 融入 harness/README.md 的「控制总纲」章节。此处提前将引用改为锚点链接，锚点 `#控制总纲` 在阶段 3 完成后生效。阶段 3 完成前此链接指向的锚点暂不存在，但这是预期的中间态。

**修改 4c：更新规则 ID 说明段中的 BLD 前缀定义**

将第 8 行中的 `BLD`（build-reference）前缀说明删除：

```
- 主题前缀：`SRC`（source-code-modify）、`DOC`（doc-paths / plantuml）、`OBS`（script-observability）、`PAR`（parallel-strategy）、`PATH`（path-management）、`BLD`（build-reference）。
```

改为：

```
- 主题前缀：`SRC`（source-code-modify）、`DOC`（doc-paths / plantuml）、`OBS`（script-observability）、`PAR`（parallel-strategy）、`PATH`（path-management）。
- `BLD`（build-reference）已迁至 `../reference/`，详见 [../reference/README.md](../reference/README.md)。
```

- [ ] **Step 5：更新 harness/README.md 快速导航的"编译 RPI5"行**

在 `engineering/harness/README.md` 快速导航表中，将第 24 行：

```markdown
| 编译 RPI5 AOSP/内核 | [rules/build-reference.md](./rules/build-reference.md) |
```

改为：

```markdown
| 编译 RPI5 AOSP/内核 | [reference/build-reference.md](./reference/build-reference.md) |
```

同时，在「目录说明」表（约第 36-45 行）中新增 reference/ 行。在 `templates/` 行之后、`workflows/` 行之前插入：

```markdown
| [reference/](./reference/) | 参考文档承载层（命令模板、操作指南等非约束性参考） |
```

并在「README 同步」章节（约第 83-87 行）的清单中，新增 reference/ 的同步项：

```markdown
  - 新增/删除/重命名 `reference/*.md` → 更新 `reference/README.md` 文件清单 + 本 README 快速导航表
```

- [ ] **Step 6：运行 validator 验证全部链接一致**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：`verdict=PASS`，无告警。重点确认：
- `reference/README.md` 中 `build-reference.md` 链接已存在（git mv 已完成）。
- `AGENTS.md` 不在 validator 扫描范围（validator 只扫 `$HARNESS_DIR` 下），但其链接正确性由人工确认。
- `rules/README.md` 中 `../README.md#控制总纲` 链接：`../README.md`（即 harness/README.md）文件存在，锚点部分 validator 不校验，通过。
- `rules/README.md` 中 `../reference/README.md` 链接存在。
- `harness/README.md` 中 `reference/build-reference.md` 链接存在。
- `harness/README.md` 中 `reference/` 链接存在。

- [ ] **Step 7：提交**

```bash
git add engineering/harness/rules/build-reference.md \
        engineering/harness/reference/build-reference.md \
        AGENTS.md \
        engineering/harness/rules/README.md \
        engineering/harness/README.md
git commit -m "重构(reference): 迁移 build-reference 至 reference/ 并同步全部引用路径"
```

---

### Task 5：更新 templates/README.md 登记 2 份新模板

**Files:**
- Modify: `engineering/harness/templates/README.md`

- [ ] **Step 1：重新阅读 templates/ 目录全部文件与现有 README**

执行者必须先完整阅读以下内容，理解现有模板清单与约束说明，再进行重构（补齐新模板登记，保持表结构一致）：

1. `engineering/harness/templates/README.md`——现有文件说明表（3 行）、约束段。
2. `engineering/harness/templates/engineering-readme-template.md`——阶段 1 Task 1 创建的新模板，确认其标题与用途描述。
3. `engineering/harness/templates/rules-template.md`——阶段 1 Task 2 创建的新模板，确认其标题与用途描述。
4. `engineering/harness/templates/module-template.md`、`module-readme-template.md`、`diagnosis-report-template.md`——现有 3 份模板，理解表格列的描述风格（用途 + 适用对象），保持新增行风格一致。

- [ ] **Step 2：按 engineering-readme-template 核心 5 节重构 templates/README.md**

将 `engineering/harness/templates/README.md` 全文替换为以下内容：

```markdown
# Templates

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：技术文档与工程文档的结构模板承载层——只读契约，约束设计文档、README、规则文档的章节结构
- **职责边界**：定义"文档应该长什么样"，不定义"文档应该写什么内容"（内容由各业务文档自行组织）
- **上下游依赖**：被 `sync-patchs-to-doc` workflow 消费（只读契约）；被 `engineering-readme-template.md` 约束的所有 README 间接引用

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 模板文件清单与用途 | 了解结构时 |
| [使用方式](#使用方式) | 如何使用模板 | 实际使用时 |
| [关联资源](#关联资源) | workflow、规则链接 | 深入理解时 |

## 目录说明

| 文件 | 用途 | 适用对象 |
|------|------|---------|
| [engineering-readme-template.md](./engineering-readme-template.md) | engineering/ 下所有 README.md 的章节结构模板（核心 5 节 + 扩展块 + AI 三层读取机制） | engineering/ 下所有目录的 README.md |
| [rules-template.md](./rules-template.md) | rules/ 下规则文档的章节结构模板（5 节核心 + 允许附录） | engineering/harness/rules/ 下所有规则文档 |
| [module-readme-template.md](./module-readme-template.md) | 模块级 README 模板，4+1 视图（用例 / 逻辑 / 过程 / 开发 / 部署）组织，用于特性的顶层 README（如 `01-打点增强/README.md`） | 特性目录的入口文档 |
| [module-template.md](./module-template.md) | 模块详细设计文档模板，覆盖用例 / 逻辑 / 过程 / 开发 / 部署 / 关键设计 / 接口参考等完整章节 | 特性下的单个子模块文档（如 `01.01-内核态增强.md`） |
| [diagnosis-report-template.md](./diagnosis-report-template.md) | Loop boot 诊断报告模板，约束 AI 在 FAIL 后基于 EvidenceBundle 产出结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / case 建议 / 循环终止建议 | Loop boot 诊断报告产出 |

## 使用方式

本目录无可执行入口，仅作为模板契约的承载层。

### 新增 README 时

1. 复制 `engineering-readme-template.md` 的「核心 5 节骨架」。
2. 按目录类型从「扩展块」表中选取适用的扩展块。
3. 追加的扩展块必须同步登记到 README 的「大纲」表。

### 新增规则文档时

1. 复制 `rules-template.md` 的「核心骨架」。
2. 前 5 节必选，附录按需追加。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../workflows/sync-patchs-to-doc/` | 将本目录视为只读契约，校验 diff 产出文档的章节结构 |
| 关联规则 | `../rules/doc-paths.md`（DOC-001） | 文档分层与归档路径约束 |

## 约束

- **只读**：`sync-patchs-to-doc` workflow 将本目录视为只读契约，AI 不得擅改。
- diff 引入的内容无法归入现有模板章节时，标记 `TEMPLATE-CONFLICT`，由用户确认后才可调整模板。
- 文档结构与模板章节不一致时，`sync-patchs-to-doc` 的自检环节会标记缺失 / 多余章节。
```

- [ ] **Step 3：运行 validator 验证无告警**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：`verdict=PASS`。重点确认：
- Step2 不再报告 `engineering-readme-template.md` / `rules-template.md` 未登记（文件说明表已包含）。
- templates/README.md 中所有链接可达。

- [ ] **Step 4：提交**

```bash
git add engineering/harness/templates/README.md
git commit -m "重构(templates): 按核心5节模板重构 README 并登记 2 份新模板"
```

---
