---
name: sync-code-to-doc
description: code/rpi5 变动后生成报告，按文档既有结构将代码 diff 精准转换为文档更新（方案先行，确认后落盘）。
no_commit: true
stages:
  - research: "AI 分析 workspace diff/上下文"
  - plan: "AI 生成实施计划，经用户确认"
  - code: "执行具体操作"
  - review: "验证结果并提交"
---

# sync-code-to-doc

当 `code/rpi5/` 发生变动后，生成结构化变动报告，并**按文档既有结构将代码 diff 精准转换为文档更新**。

**核心理念**：设计文档（`01-*/02-*`）的**既有章节结构是受控结构**，AI 只做章节级增量更新，不得全文重写。diff 引入的内容无法归入既有章节时，标记 `DOC-CONFLICT`，必须用户确认后才可调整文档结构。

## Trigger（触发条件）

- cross-device-apply 编辑 `code/rpi5/` 后、git-works-push commit **前**（此时 `git diff HEAD` 有未提交变动）
- publish-main-base 评审通过后、promote **前**（用 `--base origin/main` 对比 dev 相对 main 的批次变动）
- 用户提到"更新文档""文档同步""code 变了"
- `code/rpi5/` 相对对比基线有未同步到文档的变动

> **时序关键**：脚本以 git diff 为数据源。promote 会把代码 commit 进 dev=main 的 HEAD，
> 届时工作区干净、`git diff HEAD` 为空——**promote 后再同步文档必然"无变动"空转**。
> 文档同步必须放在"代码定稿、未 commit"（默认模式）或"promote 前、dev 领先 main"（`--base origin/main`）阶段。

## Preconditions（前置条件）

- `code/rpi5/manifest.yaml` 存在（首次未归档时停下提示用户先经 cross-device-apply 归档）
- `harness/config/doc-sync-mapping.yaml` 可读（code→文档映射规则）
- 目标设计文档（`01-*/02-*`）存在
- `--base <ref>` 时 `<ref>` 可解析（脚本校验失败 exit 3）

## Inputs（输入）

| 参数 | 说明 |
|------|------|
| 无参数 | 生成变动报告（分组 + 变动类型 A/M/D/R + 行数统计），对比工作区 vs HEAD |
| `--base <ref>` | 改为对比 `<ref>...HEAD`（分支相对 ref 的**已提交**累积变动）。promote 前用 `--base origin/main` 拿到 dev 相对 main 的批次 |
| `--full-diff` | 报告 + 完整 diff 正文（AI 零往返） |
| `--check-only` / `--dry-run` | 仅输出报告，不输出 AI 操作提示 |
| `--check-docs` | 仅执行文档索引一致性检查（死索引/漏索引/断链/孤儿/code链接失效/锚点超界/形态D），不依赖 git diff；配合 `--docs-root`/`--code-root` 可覆盖 docs/code 根目录（测试用） |

脚本基于 git diff（默认 `git diff HEAD`，工作区未提交；`--base <ref>` 时 `git diff <ref>...HEAD` 已提交累积）
对比 `code/rpi5/`，按目录分组（`kernel/modified`、`kernel/new`、`aosp/modified`、`aosp/new`、`others`、`(root)`）输出变动类型（A/M/D/R）和行数统计。加 `--full-diff` 在报告末尾追加完整 diff 正文。

> **`(root)` 组**：`code/rpi5/` 根下文件（`README.md`、`manifest.yaml` 等）归入该组。
> `README.md` 映射表更新不纳入本流程（见"不涉及的文档"），`manifest.yaml` 为自动生成物，
> 两者在报告中属噪音，AI 定位时忽略。

## Human confirmation gates（人工确认门）

**方案确认门（强制）**：Step6 落盘前必须展示 Step5 的"动作清单级方案"并获得用户确认。

- 默认只更新文档不动结构
- 若方案含 `DOC-CONFLICT`（diff 引入内容无法归入任何既有章节/违反文档结构约束），**必须用户确认**才能调整文档结构
- 禁止跳过方案展示直接落盘；禁止全文重写/新建非既有章节

## Outputs / artifacts（输出/产物）

- 变动报告（脚本 stdout + stderr 日志）
- 章节级增量文档更新（仅动作清单覆盖的章节，机械执行已确认动作）
- 落盘后自动一致性自检报告：文档章节结构 / 代码路径合规 / 行号锚点有效性（含上界）/ 交叉引用可达 / 形态 D 盲区 / code 链接失效（`--check-docs`）

## Failure / recovery（失败/恢复）

| 场景 | 处理 |
|------|------|
| manifest 缺失（首次未归档） | 停下提示用户先经 cross-device-apply 流程编辑 code/rpi5（含 gen_manifest.py 生成 manifest） |
| `--base` ref 不可解析 | 脚本 exit 3，检查 ref 是否有效 commit |
| diff 引入内容无既有章节承载 | 标记 `DOC-CONFLICT`，等用户确认后才调整文档结构 |
| 行号锚点失效 | 一致性自检标记，刷新含形态 D 盲区、区间终点、重复出现处 |
| 代码路径违规（workspace 绝对路径） | 一致性自检标记，应改为 code 相对路径 |
| 交叉引用断链 | 一致性自检标记 |
| promote 后执行（工作区已 clean） | 脚本报"无变动"（exit 4）属预期——文档同步应在 promote 前用 `--base origin/main` 完成 |

## Related policy IDs（关联规则 ID）

- `SRC-002`：code 是受控归档（本 workflow 读取 code 作为文档同步来源）
- `DOC-002`：PlantUML 改动必须过 `harness/rules/plantuml.md` 约束

---

## 工作流（7 步闭环）

### 1. 生成变动报告

```bash
python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py              # 生成变动报告（工作区 vs HEAD）
python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --base origin/main  # 对比 dev 相对 main 的批次（promote 前）
python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --full-diff  # 报告 + 完整 diff 正文（AI 零往返）
python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-only  # 仅检查，不输出提示
```

### 2. 按映射规则定位文档（依据 harness/config/doc-sync-mapping.yaml）

**禁止凭空自主判断归属**，必须依据 [code→文档映射规则](../../config/doc-sync-mapping.yaml)：

- **精确匹配**：`**/LcView/**`→`docs/01-打点增强/`、`**/lechao_lcview*`→`docs/01-打点增强/`（aosp 侧 lechao_lcview 服务及 sepolicy）、`**/LcIod/**`→`docs/02-IO增强/`、`**/lechao_lciod*`→`docs/02-IO增强/`、`others/usb-verify/**`→`docs/02-IO增强/` 等（首条命中即归属）
- **通用配置类**（如 `device.mk.diff`、`*sepolicy*`、`Android.bp`）：读 diff 正文，按涉及的模块名（lciod/lcview）分发，可分发到多个文档目录
- **未命中**：读 diff 后判断，无法判断时在方案中标注"归属待定"

### 3. 取全量上下文（依据 manifest.yaml）

`code/` 中 modified 类只有 `.diff`、无全量上下文。通过 `code/rpi5/manifest.yaml`（由
cross-device 流程的 gen_manifest.py 生成）拿到 patch↔workspace 映射，去 workspace 读全量源码：

```
manifest.yaml 条目示例：
  - patch: kernel/new/vendor/lechao/LcView/builder.c
    source: rpi5-kernel-build/common/vendor/lechao/LcView/builder.c
```

- **new 类**：code 即全量文件，直接读 code
- **modified 类（.diff）**：manifest 的 `source` 是相对路径，拼接到 workspace 根后读全量源码：
    - kernel：`~/workspace/{source}`（`source` 含 `rpi5-kernel-build/common/` 前缀）
    - aosp：`~/workspace/{source}`（`source` 含 `aosp/` 前缀）
  > **路径以 `harness/config/paths.conf` 为单一事实源**（`KERNEL_WS` / `AOSP_WS`，支持环境变量覆盖）：
  > kernel 全量 = `${KERNEL_WS}/{source 去掉 rpi5-kernel-build/common/ 前缀}`，
  > aosp 全量 = `${AOSP_WS}/{source 去掉 aosp/ 前缀}`。禁止硬编码 `/home/lechao/...` 绝对路径。
- **others 段（source: null）**：`code/*/others/` 不依赖 workspace（`SRC-003`），code 即全量文件，直接读 code
- **删除文件（无 deletions 段）**：manifest 无 `deletions` 段；删除文件以报告 `[D]` 状态为准，全量上下文取 git 历史：
  `git show <删除前 commit>:<相对路径>` 或 `git log --oneline -- <path>` 定位最后版本

> **边界**：若 manifest 缺失（首次未归档），停下提示用户先经 cross-device-apply 归档。

### 4. 定位受影响文档章节（行号锚点 + 符号名）

**文档级影响判定（先行）**：对 diff 先判定结构性变化还是增量变化——

| 判定信号 | 文档级影响 | 动作 |
|---------|-----------|------|
| 服务目录整删（`hal/`、`aidl_api/`、`*-service.xml`、`*.rc`、sepolicy `*_hal.te` 同批删除） | 模块消失 | `REMOVE-DOC` |
| 文件重命名跨目录（如 `hal/DeviceReader.cpp → daemon/`） | 内容归属变更 | `MIGRATE-内容` |
| `REMOVE-DOC` 造成编号空缺需重排（如 01.02 删除后 01.03→01.02）；或用户指定改名/主题变更 | 文档编号/名称变更 | `RENAME-DOC` |
| 单文件行号漂移/签名/常量变化 | 无 | 现有增量机制（UPDATE-*） |

判定依据：diff 状态（A/M/D/R）+ 路径模式 + 同批删除关联性。判定为结构性变化时，优先处置文档级动作（REMOVE-DOC/MIGRATE-内容/RENAME-DOC），再对剩余变更点走下方 5 种形态定位。

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
| `UPDATE-图` | 重画 PlantUML（遵守 `harness/rules/plantuml.md`，即 `DOC-002`）|
| `UPDATE-文本` | 修改描述段落（表述与 diff 语义对齐）|
| `ADD-文件` | 文件矩阵/目录树补入新文件 + 行数 |
| `REMOVE-文件` | 移除所有引用（grep 全量）|
| `REMOVE-DOC` | 整篇删除文档：删除文件 + README 索引清理 + 全仓交叉引用清理 + 断链自检（git 历史即归档，不额外备份） | **强制确认**（同 DOC-CONFLICT） |
| `RENAME-DOC` | 文档重命名/重编号（如 01.03→01.02）：改名文件 + README 索引更新 + 全仓交叉引用清理 + 文档内编号/标题同步 + 断链自检 | **强制确认**（同 DOC-CONFLICT） |
| `MIGRATE-内容` | 跨文档章节/文件引用迁移（源→目标），如文件重命名跨目录后其模块分解/职责矩阵随迁 | **强制确认** |
| `DOC-CONFLICT` | ⚠ 文档结构冲突，**不自动执行**，必须用户确认 |

> **语义失效重写**：架构变化导致章节语义失效（如 HAL 服务被直读内核替代后，"HAL 服务未就绪 / Binder 连接中断"章节失效），整段重写仍属 `UPDATE-文本`，但必须在动作清单中显式标注"语义重写"+失效原因，列入动作清单由用户确认。

#### 5.3 动作清单格式（示例）

```
文档: docs/01-打点增强/01.01-内核态增强-lcview-kernel.md

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
│  据: 结构体新增字段（⚠PlantUML须遵守 harness/rules/plantuml.md）
│
├─ 动作4 [UPDATE-锚点] 章节: 关键设计与实现 (L648 代码块注释)
│  改: // lcview_builder.c:88 → // lcview_builder.c:101
│  据: 形态D盲区扫描命中（非markdown链接，易漏）
│
└─ ⚠动作6 [DOC-CONFLICT] 文档结构冲突
   冲突: diff 引入"异步flush线程"新设计，无对应既有章节承载
   建议: 在"关键设计与实现"下新增子节（文档结构允许），或调整文档结构
   → 必须用户确认后才执行
```

**文档结构冲突处理（防漂移核心）**：若 diff 引入的内容无法归入任何既有章节、或违反文档结构约束，在方案中单独标记 `DOC-CONFLICT`，列出建议的结构调整。**默认只更新文档不动结构；调整结构必须用户确认。**

### 6. 落盘（章节级增量，机械执行）

用户确认方案后执行。Step6 是**机械执行**已确认动作，不做新的语义判断。

- **只改动作清单覆盖的章节**，其他章节一字不动
- 新增内容**只能加到文档既有的现有章节**（如新增字段加到"逻辑视图/字段表"）
- 禁止：全文重写、新建非既有章节、改变章节顺序
- 执行 `REMOVE-DOC` / `ADD-DOC` / `RENAME-DOC` 后**强制同步 README 索引**（文档列表条目、架构图组件、链路描述），避免死索引/断链
- 删除文档前用 `grep -rn "<文档名>" docs/` 全量清理交叉引用
- 执行 `RENAME-DOC`：`git mv <旧文件名> <新文件名>` 改名；`grep -rn "<旧文件名>" docs/` 全量更新引用（README 索引、跨文档链接、文档内编号/标题）
- PlantUML 改动后必须过 `harness/rules/plantuml.md` 约束（`DOC-002`）
- **行号锚点刷新机制**：对受影响文件的每个被引符号，重新 grep 源码取新行号，全量替换文档中的 `#L旧` → `#L新`（含形态 D 代码块注释、区间引用终点、重复出现处）

### 7. 自动一致性自检（落盘后强制）

落盘后自动检查并输出报告：

| 检查项 | 方法 | 失败处理 |
|--------|------|---------|
| 文档章节结构 | 文档章节标题与更新前结构一致（未引入非确认的新章节） | 标记异常结构 |
| 代码路径合规 | grep workspace 绝对路径（`~/workspace`、`/home/`）| 标记违规，应改为 code 相对路径 |
| 行号锚点有效性 | 抽样验证 `#Lxxx` 指向的符号与文档描述一致 | 标记失效锚点 |
| 交叉引用可达 | 验证文档内 `](./xxx)` 链接目标文件存在 | 标记断链 |
| 形态 D 盲区 | 扫描代码块内 `// file:行` 注释 | 标记未同步的注释 |
| 文档索引一致性 | `python3 harness/skills/sync-code-to-doc/sync_code_to_doc.py --check-docs`（死索引/漏索引/断链/孤儿/code链接失效/锚点超界/形态D） | 退出码 5 时逐一修复 |

> **历史遗留问题判定**：`--check-docs` 报出的问题先判定是否**本批 diff 引入**（与本批涉及的文档/文件相关）。
> 非本批问题（既有漏索引/孤儿/过期锚点，如 02 文档的 `hal_service.cpp` 超界）**记录后不处理**，
> 不越界修改本批范围外的文档；如需清理须用户确认后另行安排。

报告以 `git diff --stat` + 问题清单形式呈现，供用户 review。

## 约束

| 约束 | 说明 |
|------|------|
| 方案先行 | 动作清单级方案，确认后落盘 |
| 增量更新 | 章节级，不全量重写 |
| 映射驱动 | 依据 `harness/config/doc-sync-mapping.yaml` 分发 |
| 结构受控 | 文档既有章节结构不可擅改；冲突需确认（`DOC-CONFLICT`）|
| 代码引用规范 | code 相对路径 + `#L行号`；禁 workspace 路径；刷新含形态 D 盲区 |
| 内容对齐 diff | 基于真实 diff/workspace，禁臆造接口/字段/行为 |
| 重复引用全改 | 同一行号/符号多次出现时，grep 全量命中，禁止只改第一处 |
| 文档结构变化兜底 | 删除/新增/重命名文档时强制同步 README 索引与交叉引用（`REMOVE-DOC` / `RENAME-DOC` / `MIGRATE-内容` 强制确认） |
| 文档索引一致 | 落盘后 `--check-docs` 通过（无死索引/漏索引/断链/孤儿/code链接失效/锚点超界/形态D问题） |
| 保持一致 | 落盘后通过一致性自检 |

## 不涉及的文档

- `code/rpi5/README.md` 文件映射表由 AI 基于 manifest.yaml 维护，不纳入本流程；报告 `(root)` 组中的 `README.md` / `manifest.yaml` 变动属噪音，忽略。
- `docs/*/README.md` 索引：**日常增量文本更新不纳入**；但当文档发生结构变化（`REMOVE-DOC` /
  `ADD-DOC` / `RENAME-DOC`）时，**强制同步**对应 README 索引（文档列表条目、架构图组件、链路描述）。

## 退出码
| 退出码 | 含义 | 下一步 |
|--------|------|--------|
| 0 | 成功（有变动） | 正常继续 |
| 3 | 环境/参数错误（`--base` ref 不可解析、code 目录缺失、`--docs-root` 不存在） | 修正参数后重试 |
| 4 | 无变动 | 正常，无需操作。注：harness_lib 的 atexit 会打印 `[FAIL] ... 退出码=4`，属通用行为，非失败；若在 promote 后看到此输出，说明文档同步时机晚了（应在 promote 前用 `--base origin/main`） |
| 5 | 文档索引一致性检查发现不一致（`--check-docs`） | 区分本批/历史遗留后逐一修复（见 Step 7） |

## TODO 跟踪
- [x] Step 1: 生成变动报告
- [x] Step 2: 按映射规则定位文档
- [x] Step 3: 取全量上下文
- [x] Step 4: 定位受影响文档章节
- [x] Step 5: 出方案（动作清单级）
- [x] Step 6: 落盘（章节级增量）
- [x] Step 7: 自动一致性自检
