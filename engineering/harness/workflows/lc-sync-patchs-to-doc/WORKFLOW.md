---
name: lc-sync-patchs-to-doc
description: patchs/rpi5 变动后生成报告，按模板规范将代码 diff 精准转换为文档更新（方案先行，确认后落盘）。
---

# lc-sync-patchs-to-doc

当 `patchs/rpi5/` 发生变动后，生成结构化变动报告，并**按模板规范将代码 diff 精准转换为文档更新**。

**核心理念**：`engineering/harness/templates/*.md` 是**只读契约**，设计文档（`01-*/02-*`）是**受控可变区**。AI 不得擅改模板；diff 与模板冲突时，必须用户确认后才可调整模板。

## Trigger（触发条件）

- 用户执行完归档（lc-sync-code-to-patchs）后
- 用户提到"更新文档""文档同步""patchs 变了"
- `patchs/rpi5/` 相对 git HEAD 有未同步到文档的变动

## Preconditions（前置条件）

- `patchs/rpi5/manifest.yaml` 存在（首次未归档时停下提示用户先执行 `/lc-sync-code-to-patchs`）
- `engineering/harness/config/doc-sync-mapping.yaml` 可读（patchs→文档映射规则）
- 目标设计文档（`01-*/02-*`）与模板（`engineering/harness/templates/*.md`）存在

## Inputs（输入）

| 参数 | 说明 |
|------|------|
| 无参数 | 生成变动报告（分组 + 变动类型 A/M/D/R + 行数统计） |
| `--full-diff` | 报告 + 完整 diff 正文（AI 零往返） |
| `--check-only` | 仅检查，不输出提示 |

脚本基于 git HEAD 对比 `patchs/rpi5/`，按目录分组（`kernel/modified`、`kernel/new`、`aosp/modified`、`aosp/new`、`others`）输出变动类型（A/M/D/R）和行数统计。加 `--full-diff` 在报告末尾追加 `git diff HEAD` 完整正文。

## Human confirmation gates（人工确认门）

**方案确认门（强制）**：Step6 落盘前必须展示 Step5 的"动作清单级方案"并获得用户确认。

- 默认只更新文档不动模板
- 若方案含 `TEMPLATE-CONFLICT`（diff 引入内容无法归入任何模板章节/违反模板约束），**必须用户确认**才能改模板
- 禁止跳过方案展示直接落盘；禁止全文重写/新建非模板章节

## Outputs / artifacts（输出/产物）

- 变动报告（脚本 stdout + 日志/artifacts，`OBS-001`/`OBS-002`）
- 章节级增量文档更新（仅动作清单覆盖的章节，机械执行已确认动作）
- 落盘后自动一致性自检报告：模板章节完整性 / 代码路径合规 / 行号锚点有效性 / 交叉引用可达 / 形态 D 盲区
- 日志/artifacts 按 `OBS-001`/`OBS-002` 落盘

## Failure / recovery（失败/恢复）

| 场景 | 处理 |
|------|------|
| manifest 缺失（首次未归档） | 停下提示用户先执行 `/lc-sync-code-to-patchs` |
| diff 引入内容无模板章节承载 | 标记 `TEMPLATE-CONFLICT`，等用户确认后才动模板 |
| 行号锚点失效 | 一致性自检标记，刷新含形态 D 盲区、区间终点、重复出现处 |
| 代码路径违规（workspace 绝对路径） | 一致性自检标记，应改为 patchs 相对路径 |
| 交叉引用断链 | 一致性自检标记 |

## Related policy IDs（关联规则 ID）

- `SRC-002`：patchs 是受控归档（本 workflow 读取 patchs 作为文档同步来源）
- `DOC-001`：文档分层（spec/plan 与长期技术文档分层，本 workflow 维护长期技术文档）
- `DOC-002`：PlantUML 改动必须过 plantuml.md 约束
- `OBS-001` / `OBS-002`：脚本维测

---

## 工作流（7 步闭环）

### 1. 生成变动报告

```bash
bash engineering/harness/workflows/lc-sync-patchs-to-doc/sync_patchs_to_doc.sh              # 生成变动报告
bash engineering/harness/workflows/lc-sync-patchs-to-doc/sync_patchs_to_doc.sh --full-diff  # 报告 + 完整 diff 正文（AI 零往返）
bash engineering/harness/workflows/lc-sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only  # 仅检查，不输出提示
```

### 2. 按映射规则定位文档（依据 engineering/harness/config/doc-sync-mapping.yaml）

**禁止凭空自主判断归属**，必须依据 [patchs→文档映射规则](../../config/doc-sync-mapping.yaml)：

- **精确匹配**：`**/LcView/**`→`01-打点增强/`、`**/LcIod/**`→`02-IO增强/`、`others/usb-*`→`02-IO增强/` 等（首条命中即归属）
- **通用配置类**（如 `device.mk.diff`、`*sepolicy*`、`Android.bp`）：读 diff 正文，按涉及的模块名（lciod/lcview）分发，可分发到多个文档目录
- **未命中**：读 diff 后判断，无法判断时在方案中标注"归属待定"

### 3. 取全量上下文（依据 manifest.yaml）

`patchs/` 中 modified 类只有 `.diff`、无全量上下文。通过 `patchs/rpi5/manifest.yaml`（由 lc-sync-code-to-patchs 生成）拿到 patch↔workspace 映射，去 `~/workspace/` 读全量源码：

```
manifest.yaml 条目示例：
  - patch: kernel/new/vendor/lechao/LcView/builder.c
    source: rpi5-kernel-build/common/vendor/lechao/LcView/builder.c
```

- **new 类**：patchs 即全量文件，直接读 patchs
- **modified 类（.diff）**：manifest 的 `source` 是相对路径，按来源拼接到 workspace 根后读全量源码：
    - kernel：`~/workspace/{source}` → `~/workspace/rpi5-kernel-build/common/...`
    - aosp：`~/workspace/{source}` → `~/workspace/aosp/...`
  （`source` 值如 `rpi5-kernel-build/common/vendor/lechao/LcView/builder.c`，直接接到 `~/workspace/` 之后）

> **边界**：若 manifest 缺失（首次未归档），停下提示用户先执行 `/lc-sync-code-to-patchs`。

### 4. 定位受影响文档章节（行号锚点 + 符号名）

文档引用代码有 **5 种形态**，定位时必须全覆盖（漏改风险从高到低）：

| 形态 | 格式 | 示例 | 盲区风险 |
|------|------|------|---------|
| **A 行号锚点链接** | `[file:行](路径#L行)` | `[lcview_main.c:248](...#L248)` | 中 |
| **B 文件路径链接** | `` [`file`](路径) `` | `` [`builder.c`](.../builder.c) `` | 低 |
| **C 纯文本文件名** | 正文叙述 | `builder.c 中定义了...` | 高 |
| **D 代码块注释** | `// file:行`（块内）| `// lcview_builder.c:88` | **极高（链接检查器盲区）** |
| **E 文件矩阵** | 目录树/表格行数列 | `├── builder.c` / `行数: 319` | 中 |

**三种定位法**（对每个变更点组合使用）：

```
【定位法1 符号名】对变更涉及的函数/结构体/宏名
  → rg "<symbol>" 01-*/02-*/*.md
  → 命中所有提及该符号的段落（含重复出现）

【定位法2 行号锚点】对行号漂移的文件（形态 A + D）
  → rg "<filename>[:#]L?\d+" *.md       # 形态 A：markdown 链接
  → rg "^//\s*<filename>:\d+" *.md      # 形态 D：代码块注释（盲区，必须单独扫）
  → 命中所有引用该文件行号的位置

【定位法3 文件名】对新增/删除/重命名的文件
  → rg "<filename>" *.md
  → 命中文件矩阵(形态E)、路径链接(形态B)、纯文本(形态C)
```

> **关键**：同一行号/符号可能在文档中**重复出现**（职责矩阵 + 不量表 + 关键设计各一次），必须全量命中，禁止只改第一处。

### 5. 出方案（动作清单级，强制）

将 Step4 的"变更点 → 文档位置"组合成**结构化动作清单**。每条动作含：**文档 / 章节 / 动作类型 / 具体内容 / 依据**。

#### 5.1 变更分类（diff 先归类，决定影响范围）

| 源码改动类型 | 识别方式 | 影响的引用形态 |
|-------------|---------|---------------|
| 函数签名变化 | diff 含函数声明行改动 | A + 接口参考表 |
| 结构体/字段变化 | diff 含 `struct`/字段行 | 协议表 + 二进制布局图(PlantUML) + ABI映射表 |
| 行数漂移（无签名改）| diff hunk 插入/删除行 | A + D（所有行号锚点 + 代码块注释）|
| 新增文件 | diff 状态 = A | E（目录树 + 文件矩阵）|
| 文件删除/重命名 | diff 状态 = D/R | A + B + E（全形态）|
| 常量/配置变化 | diff 含 `#define`/Kconfig | 配置常量表 + 不量表 |

#### 5.2 动作类型

| 动作类型 | 说明 |
|---------|------|
| `UPDATE-锚点` | 行号锚点刷新（含形态 D 盲区、区间终点、重复出现处）|
| `UPDATE-表格` | 表格新增/修改/删除行（值严格来自 diff/workspace）|
| `UPDATE-图` | 重画 PlantUML（遵守 `engineering/harness/rules/plantuml.md`，即 `DOC-002`）|
| `UPDATE-文本` | 修改描述段落（表述与 diff 语义对齐）|
| `ADD-文件` | 文件矩阵/目录树补入新文件 + 行数 |
| `REMOVE-文件` | 移除所有引用（grep 全量）|
| `TEMPLATE-CONFLICT` | ⚠ 模板冲突，**不自动执行**，必须用户确认 |

#### 5.3 动作清单格式（示例）

```
文档: 01-打点增强/01.01-内核态增强-lcview-kernel.md

┌─ 动作1 [UPDATE-锚点] 章节: 逻辑视图/职责矩阵 (L168)
│  改: lcview_builder.c 行号锚点 #L271 → #L284（commit函数下移13行）
│  据: diff 插入13行 + grep 命中 L168, L363, L757（3处全列）
│
├─ 动作2 [UPDATE-表格] 章节: 逻辑视图/字段类型编码表 (L187)
│  改: 新增一行 | TIMESTAMP | 5 | type(1B)+timestamp(8B) |
│  据: diff 新增 LCVIEW_FIELD_TIMESTAMP 宏
│
├─ 动作3 [UPDATE-图] 章节: 逻辑视图/二进制布局图 (L177)
│  改: PlantUML rectangle 字段区追加 timestamp(8B)
│  据: 结构体新增字段（⚠PlantUML须遵守 engineering/harness/rules/plantuml.md）
│
├─ 动作4 [UPDATE-锚点] 章节: 关键设计与实现 (L648 代码块注释)
│  改: // lcview_builder.c:88 → // lcview_builder.c:101
│  据: 形态D盲区扫描命中（非markdown链接，易漏）
│
└─ ⚠动作6 [TEMPLATE-CONFLICT] 模板冲突
   冲突: diff 引入"异步flush线程"新设计，无对应模板章节承载
   建议: 在"关键设计与实现"下新增子节（模板允许），或扩展模板
   → 必须用户确认后才执行（模板只读约束）
```

**模板冲突处理（防漂移核心）**：若 diff 引入的内容无法归入任何模板章节、或违反模板约束，在方案中单独标记 `TEMPLATE-CONFLICT`，列出建议的模板调整。**默认只更新文档不动模板；改模板必须用户确认。**

### 6. 落盘（章节级增量，机械执行）

用户确认方案后执行。Step6 是**机械执行**已确认动作，不做新的语义判断。

- **只改动作清单覆盖的章节**，其他章节一字不动
- 新增内容**只能加到模板定义的现有章节**（如新增字段加到"逻辑视图/字段表"）
- 禁止：全文重写、新建非模板章节、改变章节顺序
- PlantUML 改动后必须过 `engineering/harness/rules/plantuml.md` 约束（`DOC-002`）
- **行号锚点刷新机制**：对受影响文件的每个被引符号，重新 grep 源码取新行号，全量替换文档中的 `#L旧` → `#L新`（含形态 D 代码块注释、区间引用终点、重复出现处）

### 7. 自动一致性自检（落盘后强制）

落盘后自动检查并输出报告：

| 检查项 | 方法 | 失败处理 |
|--------|------|---------|
| 模板章节完整性 | 文档章节标题 vs 模板定义序列 | 标记缺失/多余章节 |
| 代码路径合规 | grep workspace 绝对路径（`~/workspace`、`/home/`）| 标记违规，应改为 patchs 相对路径 |
| 行号锚点有效性 | 抽样验证 `#Lxxx` 指向的符号与文档描述一致 | 标记失效锚点 |
| 交叉引用可达 | 验证文档内 `](./xxx)` 链接目标文件存在 | 标记断链 |
| 形态 D 盲区 | 扫描代码块内 `// file:行` 注释 | 标记未同步的注释 |

报告以 `git diff --stat` + 问题清单形式呈现，供用户 review。

## 约束

| 约束 | 说明 |
|------|------|
| 方案先行 | 动作清单级方案，确认后落盘 |
| 增量更新 | 章节级，不全量重写 |
| 映射驱动 | 依据 `engineering/harness/config/doc-sync-mapping.yaml` 分发 |
| 模板只读 | `engineering/harness/templates/*.md` 不可擅改；冲突需确认（`TEMPLATE-CONFLICT`）|
| 代码引用规范 | patchs 相对路径 + `#L行号`；禁 workspace 路径；刷新含形态 D 盲区 |
| 内容对齐 diff | 基于真实 diff/workspace，禁臆造接口/字段/行为 |
| 重复引用全改 | 同一行号/符号多次出现时，grep 全量命中，禁止只改第一处 |
| 保持一致 | 落盘后通过一致性自检 |

## 不涉及的文档

`patchs/rpi5/README.md` 文件映射表的更新仍走 lc-sync-code-to-patchs 末尾提示，不纳入本流程。
