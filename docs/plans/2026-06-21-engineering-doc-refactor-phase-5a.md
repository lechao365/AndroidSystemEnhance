## 阶段 5（前半）：README 全量校准 + 去重（loop/README 精简 + 中型 README 前 6 个）

> **前置依赖**：阶段 1（模板与 reference/ 建立）已完成——`harness/templates/engineering-readme-template.md`、`harness/templates/rules-template.md` 已存在；阶段 2（build-reference 迁移）已完成；阶段 3（CONTROL-CHARTER 融入 harness/README.md#控制总纲）已完成——`harness/README.md` 已含「控制总纲」锚点；阶段 4（rp5-serial 融合 + 协议重命名）已完成。
>
> **本阶段产出**：loop/README.md 由 235 行精简至 ~80 行（流程细节迁回 loop/WORKFLOW.md）；engineering/README.md 成为 harness↔loop 边界的单一事实源；harness 下 config/lib/scripts/templates/workflows 五份 README 按核心 5 节 + 各自专化扩展块校准。
>
> **共同约束（所有 Task 适用）**：
> - 严格遵循 `harness/templates/engineering-readme-template.md` 的核心 5 节骨架（定位 / 大纲 / 目录说明 / 使用方式 / 关联资源），顶部嵌入「AI 读取指引」+「大纲」表，大纲表强制列出全部章节（核心 + 已启用扩展块），每行三列（章节 / 内容摘要 / 何时读取）。
> - 每个 README Task 的第一步必须**重新通读该目录下所有代码/配置/子目录与现有 README**，结合新材料重构——补齐缺失内容、删除失效内容，而非纯格式调整。
> - 关联资源类型固定四枚举：设计文档 / 关联规则 / 关联 workflow / 关联配置。
> - 每个 Task 结束前运行 `bash engineering/harness/scripts/validate_harness_docs.sh`，退出码必须为 0。
> - commit message 前缀：`重构(docs): ...`。

---

### Task 5.1：loop/WORKFLOW.md 接收从 README 迁回的流程细节（SSOT 落地）

**Files:**
- Modify: `engineering/loop/WORKFLOW.md`（接收迁移内容，行数由 137 → ~230）

**迁移内容清单（从 `engineering/loop/README.md` 迁入 WORKFLOW.md，按目标章节归并）**：

| 迁移项 | README 源（行） | WORKFLOW 目标位置 | 处置（合并/新增） |
|--------|----------------|------------------|------------------|
| 架构 ASCII 图（`opencode→le run→loop_core→connection`） | L5-18 | 「核心流程」章节之后，新增「架构拓扑」小节 | **新增**——WORKFLOW 现仅有「分层职责」表，补入 ASCII 拓扑图作为可视化总览 |
| `run_on` 执行平面（device/host 约束、reboot 限制、示例 YAML） | L128-147 | 「断言类型」之后，新增「`run_on` 执行平面」小节 | **新增** |
| `system.network_adbd` 场景（8 步判定链 + live 运行示例 + 前置要求） | L149-181 | 「扩展新场景」之后，新增「场景细节」小节 | **新增**——作为首个场景样例 |
| `system.adb_shell` 场景（4 步 smoke） | L208-217 | 「场景细节」小节，与 network_adbd 并列 | **新增**（与 network_adbd 同属 system 场景组，一并迁入保持 README 精简） |
| `features.lcview` 场景（collector 清单） | L219-228 | 「场景细节」小节 | **新增** |
| `serial_context` 字段表（4 字段表） | L184-194 | 替换现有「EvidenceBundle 串口上下文」L98-105 的纯文本版 | **合并**——用 README 的完整字段表替换 WORKFLOW 现有的 3 行文字版 |
| 串口 transcript 说明（transcript_path 落盘 + serial_recent 消费机制） | L196-200 | 「EvidenceBundle 串口上下文」小节末尾追加 | **合并** |
| `/le` 失败诊断约束（诊断报告只输出四要素、不强行根因） | L202-206 | 与现有「AI 诊断报告约束」L116-137 合并 | **合并**——WORKFLOW 现有 10 条规则已是更完整版本，README 版作为引言句保留即可 |
| core 模块清单 | （README 无独立清单，散见架构图） | 「core 模块清单」L62-78 | **保持**——WORKFLOW 已是 SSOT，无需改动 |

**步骤：**

- [ ] **Step 1：重新通读 loop/ 目录与现有 WORKFLOW.md / README.md**

  执行以下读取，确认迁移项的当前措辞与 WORKFLOW 现有内容是否重复，避免迁入后出现双份：
  - 读 `engineering/loop/README.md` 全文（235 行），标记 L5-18/L128-181/L184-206/L208-228 八个迁移源段
  - 读 `engineering/loop/WORKFLOW.md` 全文（137 行），标记「分层职责」「core 模块清单」「断言类型」「EvidenceBundle 串口上下文」「AI 诊断报告约束」五个目标段
  - `ls engineering/loop/core/python/loop_core/`（确认模块清单与 WORKFLOW L62-78 一致；若 README 架构图提及的模块名与实际 .py 文件不符，以实际文件为准修正）
  - `ls engineering/loop/cases/system/ engineering/loop/cases/features/lcview/`（确认 network-adbd-success.yaml / adb-shell-success.yaml / lcview/common.yaml 存在，场景描述与实际文件名一致）
  - 读 `engineering/loop/templates/case-template.md`（确认 WORKFLOW「扩展新场景」引用的模板路径正确）

- [ ] **Step 2：在 WORKFLOW.md「核心流程」之后新增「架构拓扑」小节**

  将 README L7-18 的 ASCII 拓扑图迁入，置于 `## 核心流程` 与 `## 分层职责` 之间，标题为 `## 架构拓扑`。拓扑图内容保持原样（`opencode → le run → loop_core{case_loader,assertion_engine,executor,runner,evidence} → connection(rp5-serial provider)`）。核对图中模块名与 `loop_core/` 实际 .py 文件一致（如 `case_loader.py`/`assertion_engine.py`/`executor.py`/`runner.py`/`evidence.py`），不一致则以实际文件名为准。

- [ ] **Step 3：替换「EvidenceBundle 串口上下文」为完整字段表版**

  删除 WORKFLOW 现有 L98-105 的纯文本三行版（`transcript_path`/`serial_snippet`/`reboot_cycles`），替换为 README L184-194 的四字段表（`transcript_path` / `serial_snippet` / `reboot_cycles` / `recent_line_count`），并在表后追加 README L196-200 的「串口 transcript」说明段（transcript_path 默认路径 `output/host-log/rp5-serial-transcript.log`、serial_recent 通过 `mode: serial_context` 消费）。补充一句指向 `summary.txt` 同步渲染。

- [ ] **Step 4：在「断言类型」之后新增「`run_on` 执行平面」小节**

  迁入 README L128-147 全部内容：默认 `device` 执行的说明、host 侧动作声明方式（含 `run_on: host` + `adb connect` 示例 YAML）、三条约束（`run_on` 仅 device/host；`action: reboot` 仅 device；`prompt_visible`/`serial_context` 仅 device）。

- [ ] **Step 5：在「扩展新场景」之后新增「场景细节」小节**

  依次迁入三个场景（按 README 顺序）：
  1. `system.network_adbd`：迁入 README L149-181 全部——8 步判定链列表 + 「该场景继续以串口为主执行与主取证通道；host adb 仅作为最终成功判据」一句 + Live 运行示例 bash 块 + 运行前要求（host 可调 adb / wifi.conf 真实配置 / 静态 IP 192.168.1.55）。
  2. `system.adb_shell`：迁入 README L208-217——4 步 smoke 列表 + 「建议在实现任何 feature adb suite 前先单独跑通本场景」一句。
  3. `features.lcview`：迁入 README L219-228——collector 清单（adb shell reachability / sys.boot_completed / HAL daemon service state / schema data dir readiness / pull logs invalid log runtime context final collectors）。

  每个场景用 `###` 三级标题命名（如 `### system.network_adbd`），引用对应 yaml 相对路径 `cases/system/network-adbd-success.yaml`。

- [ ] **Step 6：校验「AI 诊断报告约束」无重复**

  比对 README L202-206 与 WORKFLOW L116-137。WORKFLOW 现有 10 条规则更完整，保留 WORKFLOW 版本不动；若 README 版含 WORKFLOW 未覆盖的措辞（如「只输出确定事实/现象归类/当前不确定点/候选修复方向」的四要素表述），作为该章节开头的一句引言补入，其余不重复迁入。

- [ ] **Step 7：front matter 保持不变**

  确认 WORKFLOW.md 顶部 front matter（`name: loop-engineering` / `description: ...`）不变——`.opencode/commands/le.md` 通过 `@engineering/loop/WORKFLOW.md` 消费，`validate_harness_docs.sh` Step4 校验 name/description 字段存在。

- [ ] **Step 8：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0（WORKFLOW.md front matter 校验通过；此时 README 尚未精简，README 与 WORKFLOW 内容暂时并存，validator 不查重，应通过）。

- [ ] **Step 9：Commit**

  ```bash
  git add engineering/loop/WORKFLOW.md
  git commit -m "重构(docs): loop/WORKFLOW.md 接收从 README 迁回的架构/场景/serial_context/run_on 流程细节"
  ```

---

### Task 5.2：loop/README.md 精简去重（235 → ~80 行）

**Files:**
- Modify: `engineering/loop/README.md`（235 → ~80 行）

**保留章节（核心 5 节，无扩展块）**：定位 / 大纲 / 目录说明 / 使用方式（快速开始） / 关联资源。

**删除并已在 Task 5.1 迁回 WORKFLOW.md 的章节清单**：
1. `## 架构`（ASCII 图）→ WORKFLOW「架构拓扑」
2. `## run_on 执行平面` → WORKFLOW「`run_on` 执行平面」
3. `## system.network_adbd 场景`（含 Live 运行示例）→ WORKFLOW「场景细节」
4. `## system.adb_shell 场景` → WORKFLOW「场景细节」
5. `## features.lcview 场景` → WORKFLOW「场景细节」
6. `## EvidenceBundle 串口上下文`（字段表）→ WORKFLOW「EvidenceBundle 串口上下文」
7. `## 串口 transcript` → WORKFLOW「EvidenceBundle 串口上下文」
8. `## /le 失败后诊断` → WORKFLOW「AI 诊断报告约束」
9. 「公共 suite 与诊断 collector 库」详细表（L41-74）→ 已在 WORKFLOW「公共 suite 与诊断 collector 库」承载；README 仅在目录说明留一行索引

**保留并重构的章节**：`## 快速开始`（fixture + live 两模式最小命令）、`## 测试`（pytest 命令）并入「使用方式」。

**步骤：**

- [ ] **Step 1：重新通读 loop/ 目录与现有 README / WORKFLOW**

  执行以下读取，确认精简后 README 不丢失任何目录级导航信息：
  - 读 `engineering/loop/README.md` 全文（235 行）
  - 读 `engineering/loop/WORKFLOW.md` 全文（确认 Task 5.1 已迁入全部流程细节，README 可安全删除对应段）
  - `ls -R engineering/loop/` 的顶层目录（core/ cases/ connection/ scripts/ templates/ workflows/ controller/ contracts/）——为「目录说明」表准备完整子目录清单
  - 读 `engineering/loop/scripts/README.md`（确认 CLI 入口 le.sh 的描述，README 目录说明引用脚本 README 而非重复）
  - 读 `docs/specs/2026-06-19-loop-engineering-v2-design.md` 前 30 行（确认「关联资源」引用的设计文档标题与路径准确）

- [ ] **Step 2：重写「定位」章节**

  按 template 三字段：
  - **是什么**：AI 驱动的设备验收闭环——用例驱动 + EvidenceBundle + opencode AI 分析修复
  - **职责边界**：承载 loop engineering 专属能力（cases / connection / core / scripts / controller / workflows / contracts）；不承载公共 harness 基础设施（在 `../harness/`）
  - **上下游依赖**：依赖 `engineering/harness/`（规则/路径/observability）；被 `.opencode/commands/le.md` 通过 `@WORKFLOW.md` 消费

- [ ] **Step 3：重写「大纲」章节**

  顶部嵌入 AI 读取指引（template 标准句）；大纲表列出全部章节：定位 / 目录说明 / 使用方式 / 关联资源，每行三列（章节 / 内容摘要 / 何时读取）。例如「使用方式」行「何时读取」填「实际跑 le run 时」；新增一行指向 WORKFLOW.md：「流程细节（架构/场景/诊断约束）→ 见 WORKFLOW.md | 深入理解流程时」。

- [ ] **Step 4：重写「目录说明」章节**

  表格列出 loop/ 下全部一级子目录与关键文件，每行三列（子目录/文件 / 职责一句话 / 关键入口或被谁引用）：
  - `core/python/loop_core/` — LE 框架通用层 — 详见 WORKFLOW.md「core 模块清单」
  - `cases/` — 声明式用例（YAML），含 common/ features/ system/ — `cases/common/shell.yaml` 公共 suite + 诊断 collector（详见 WORKFLOW.md）
  - `connection/` — 连接层（provider/profiles/protocol）— 详见 `connection/README.md`
  - `scripts/` — CLI 入口 le.sh + host 启动脚本 — 详见 `scripts/README.md`
  - `templates/case-template.md` — AI 用例生成约束模板
  - `workflows/` — loop 专属 workflow（lcview-adb-run）— 详见 `workflows/README.md`
  - `controller/`、`contracts/` — loop 控制层与契约层（预留）
  - `WORKFLOW.md` — **流程细节单一事实源**（架构拓扑 / core 模块 / 断言类型 / run_on / 场景细节 / serial_context / 诊断约束）— 被 `/le` 注入

  表后追加一句：「子目录细节见各自 README.md，本表只给一句话索引。流程级细节见 WORKFLOW.md。」

- [ ] **Step 5：重写「使用方式」章节**

  保留「快速开始」两模式最小命令（fixture 离线回放 + live 模式），命令块原样保留 README L80-100 内容。新增「测试」子节，迁入 L120-126 的 pytest 命令块。新增「添加新场景」一句话指针：参照 `templates/case-template.md` 写 YAML，零 Python，详见 WORKFLOW.md「扩展新场景」。

- [ ] **Step 6：重写「关联资源」章节**

  四枚举表格：
  - 设计文档：`docs/specs/2026-06-19-loop-engineering-v2-design.md`（v2 架构，权威）/ `docs/specs/2026-06-20-loop-zygote-restart-serial-observability-design.md`（串口观测）/ `docs/specs/2026-06-20-le-zygote-diagnosis-and-patch-draft-design.md`（诊断与补丁草案）/ `docs/specs/2026-06-19-loop-core-extraction-design.md`（core 抽取）/ `docs/specs/2026-06-19-loop-engineering-design.md`（v1 历史归档）
  - 关联规则：`../harness/rules/script-observability.md`（改 loop 下 bash 脚本时）/ `../harness/rules/path-management.md`（路径引用）
  - 关联 workflow：`workflows/lcview-adb-run/`（lcview serial→adb 双阶段验收）
  - 关联配置：`../harness/config/harness-paths.conf`（LOOP_* 路径 KEY）

- [ ] **Step 7：删除全部已迁出章节**

  删除 Step「删除清单」中的 9 个章节（架构 / run_on / network_adbd / adb_shell / lcview / serial_context / 串口 transcript / /le 诊断 / 公共 suite 详表）。确认删除后正文不含任何流程细节，仅剩核心 5 节。目标行数 ~80 行（含空行与代码块）。

- [ ] **Step 8：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0（README 内部链接、目录文件清单一致性、WORKFLOW front matter 均通过）。

  手动复核：`wc -l engineering/loop/README.md` 应在 70-90 行区间。

- [ ] **Step 9：Commit**

  ```bash
  git add engineering/loop/README.md
  git commit -m "重构(docs): loop/README.md 精简至 ~80 行，流程细节迁回 WORKFLOW.md（SSOT）"
  ```

---

### Task 5.3：engineering/README.md 校准（边界 SSOT 化）

**Files:**
- Modify: `engineering/README.md`（42 → ~60 行）

**保留扩展块**：「边界与依赖」（单向依赖 + 能力归属判定 + workflow 归属）——**本 README 成为 harness↔loop 边界的单一事实源**（spec §八决策：engineering/README 单一承载，harness/README 与 loop/README 改为链接引用）。

**步骤：**

- [ ] **Step 1：重新通读 engineering/ 目录与现有 README**

  执行以下读取，确认边界规则与实际目录结构一致：
  - 读 `engineering/README.md` 全文（42 行）
  - `ls engineering/`（确认一级目录为 harness/ loop/ output/ 三项，无新增/缺失）
  - 读 `engineering/harness/README.md` 的「与 loop 的边界」段（L47-52）——确认本 README 的边界描述覆盖 harness 版的全部要点，使其可安全改为链接引用
  - 读 `engineering/loop/README.md`（Task 5.2 已精简）——确认 loop/README 不再重复边界内容
  - 读 `docs/specs/2026-06-21-engineering-doc-refactor-design.md` §八（去重与 SSOT）——确认 engineering/README 为边界 SSOT 的决策

- [ ] **Step 2：重写「定位」章节**

  - **是什么**：工程能力总目录，承载公共工程基础设施（harness）与 loop engineering 专属能力（loop），不承载业务源码
  - **职责边界**：做工程控制/约束/工具链/验收闭环；不做业务功能实现（业务源码在 `~/workspace/`）
  - **上下游依赖**：被 `AGENTS.md` 引用为工程入口；harness 与 loop 单向依赖（见「边界与依赖」）

- [ ] **Step 3：重写「大纲」章节**

  AI 读取指引 + 大纲表列出：定位 / 目录说明 / 使用方式 / 边界与依赖 / 关联资源。其中「边界与依赖」行「何时读取」填「判断目录归属、依赖方向时」。

- [ ] **Step 4：重写「目录说明」章节**

  表格三列（目录 / 职责 / 关键入口）：
  - `harness/` — 公共工程能力层（规则/模板/路径/日志/脚本/workflow）— `harness/README.md`
  - `loop/` — loop engineering 专属能力层（cases/connection/core/scripts/controller/workflows/contracts）— `loop/README.md`
  - `output/` — 本地日志与运行产物目录，不承载实现逻辑 — `output/README.md`

- [ ] **Step 5：重写「使用方式」章节**

  本目录无可执行入口，仅作为工程能力总索引。一句话说明：「按需进入 `harness/` 或 `loop/` 子目录，各自 README 提供入口与快速开始。」

- [ ] **Step 6：重写「边界与依赖」扩展块（SSOT）**

  将现有 L13-35 的「单向依赖规则」「能力归属判定规则」「workflow 归属规则」三段合并整理为本扩展块，作为 harness↔loop 边界的**唯一权威表述**：
  - **单向依赖**：允许 loop→harness；禁止 harness→loop
  - **能力归属判定**：列「必须放 loop/」与「允许放 harness/」两组判据（保留现有 L20-30 的四条判据原文）
  - **workflow 归属**：通用工程 workflow→`harness/workflows/`；loop 专属 workflow→`loop/workflows/`
  - 末尾追加一句：「harness/README.md 与 loop/README.md 的边界说明均链接回本节。」

- [ ] **Step 7：新增「关联资源」章节**

  四枚举表格：
  - 设计文档：`docs/specs/2026-06-21-engineering-doc-refactor-design.md`（engineering 文档重构设计）
  - 关联规则：`harness/rules/source-code-modify.md`（源码改动）/ `harness/rules/path-management.md`（路径管理）
  - 关联配置：`harness/config/harness-paths.conf`（工程路径 KEY）
  - （无关联 workflow——本目录为索引层）

- [ ] **Step 8：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0。

- [ ] **Step 9：Commit**

  ```bash
  git add engineering/README.md
  git commit -m "重构(docs): engineering/README.md 按核心 5 节校准，边界与依赖扩展块 SSOT 化"
  ```

---

### Task 5.4：harness/config/README.md 校准

**Files:**
- Modify: `engineering/harness/config/README.md`（72 → ~85 行）

**保留扩展块**：「字段速查」「任务准入矩阵」「何时更新」（spec §六 6.2）。**链接更新**：原文中 `CONTROL-CHARTER.md` 引用改为 `../README.md#控制总纲`（阶段 3 已完成融入）。

**步骤：**

- [ ] **Step 1：重新通读 config/ 目录与现有 README**

  执行以下读取，确认文件清单与字段说明与实际配置一致：
  - 读 `engineering/harness/config/README.md` 全文（72 行）
  - `ls engineering/harness/config/`（确认 5 个文件：scope-mapping.yaml / doc-sync-mapping.yaml / baseline-status.yaml / baseline-evidence-template.yaml / harness-paths.conf）
  - 读 `scope-mapping.yaml`（83 行）——核对「字段速查」的 rules[].match/scope/priority/description 字段说明与实际 YAML 一致
  - 读 `doc-sync-mapping.yaml`——核对 routes[].match/docs/mode/priority/note 字段说明与实际一致
  - 读 `baseline-status.yaml` 与 `baseline-evidence-template.yaml`——确认「文件说明」表对两者的描述准确
  - 读 `harness-paths.conf`——确认路径 KEY 清单（ENGINEERING_DIR/HARNESS_DIR/LOOP_DIR/LOOP_SCRIPTS_DIR/LOOP_WORKFLOWS_DIR/LOOP_CASES_DIR 等）与 README 描述一致
  - 读 `engineering/harness/README.md` 确认 `#控制总纲` 锚点存在（阶段 3 产出）

- [ ] **Step 2：重写「定位」章节**

  - **是什么**：workflow 依赖的映射配置层——把「目录特征 → scope / 文档归属 / baseline 状态」的规则抽成独立 YAML 数据源
  - **职责边界**：做机器可读的映射数据；不做解释性文档（解释在 YAML 注释与 description/note 字段内嵌）
  - **上下游依赖**：被 `workflows/git-push-to-server/`（scope-mapping）、`workflows/sync-patchs-to-doc/`（doc-sync-mapping）、`source-code-modify.md`（baseline-*）、`../README.md#控制总纲`（优先级链）引用

- [ ] **Step 3：重写「大纲」章节**

  大纲表列出全部章节：定位 / 目录说明 / 使用方式 / 字段速查 / 任务准入矩阵 / 何时更新 / 关联资源。

- [ ] **Step 4：重写「目录说明」章节**

  表格三列（文件 / 作用 / 被谁引用），保留现有 L13-24 两张表（配置文件表 + 其他配置表）内容，核对 5 个文件均登记。`baseline-status.yaml` 与 `baseline-evidence-template.yaml` 的「被谁引用」列：原写 `CONTROL-CHARTER.md`，改为 `../README.md#控制总纲` + `../rules/source-code-modify.md`。

- [ ] **Step 5：重写「使用方式」章节**

  本目录无可执行入口，仅作为配置数据承载层。一句话说明：「新增目录或模块时只改本目录 YAML，不动 workflow 脚本；校验通过 `validate_harness_config.sh`。」

- [ ] **Step 6：保留并校准「字段速查」扩展块**

  保留现有 L27-42 的 scope-mapping.yaml 与 doc-sync-mapping.yaml 字段说明。补充 harness-paths.conf 的 KEY 清单速查（列出 ENGINEERING_DIR/HARNESS_DIR/LOOP_DIR/LOOP_SCRIPTS_DIR/LOOP_WORKFLOWS_DIR/LOOP_CASES_DIR/SHELL_LIB_DIR/PYTHON_LIB_DIR/OUTPUT_DIR/LOG_DIR 等主要 KEY 一句话说明），引用 `../rules/path-management.md`（PATH-001）。

- [ ] **Step 7：保留并校准「任务准入矩阵」扩展块**

  保留现有 L44-65 的矩阵表与使用规则。矩阵中「必读规则」列凡引用 `CONTROL-CHARTER.md` 处（L55、L57），改为 `../README.md#控制总纲`。

- [ ] **Step 8：保留并校准「何时更新」扩展块**

  保留现有 L67-72 的四条触发条件。补充一条：baseline-status.yaml 状态变更（promoted baseline 晋升）时同步更新本文件登记。

- [ ] **Step 9：新增「关联资源」章节**

  四枚举表格：
  - 设计文档：`docs/specs/2026-06-21-engineering-doc-refactor-design.md`（文档重构）
  - 关联规则：`../rules/path-management.md`（PATH-001，harness-paths.conf 校验）/ `../rules/source-code-modify.md`（baseline 证据）
  - 关联 workflow：`../workflows/git-push-to-server/`（scope-mapping 消费）/ `../workflows/sync-patchs-to-doc/`（doc-sync-mapping 消费）/ `../workflows/revert-code-from-patchs/`（baseline-evidence 消费）
  - 关联配置：（自身即配置层，无外部配置依赖）

- [ ] **Step 10：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0。

- [ ] **Step 11：Commit**

  ```bash
  git add engineering/harness/config/README.md
  git commit -m "重构(docs): harness/config/README.md 按核心 5 节校准，CONTROL-CHARTER 链接改为 #控制总纲"
  ```

---

### Task 5.5：harness/lib/README.md 校准

**Files:**
- Modify: `engineering/harness/lib/README.md`（55 → ~70 行）

**保留扩展块**：「公共 API 速查」（spec §六 6.2，spec §四扩展块表「公共 API 速查」适用 lib/README）。

**步骤：**

- [ ] **Step 1：重新通读 lib/ 目录与现有 README**

  执行以下读取，确认 API 速查与实际库文件导出的函数一致：
  - 读 `engineering/harness/lib/README.md` 全文（55 行）
  - `ls -R engineering/harness/lib/`（shell/python/bat 三子目录）
  - 读 `engineering/harness/lib/shell/harness_path_util.sh`——确认导出的公共函数名（`harness_path` / `harness_env_path` / `harness_pythonpath` 等）
  - 读 `engineering/harness/lib/shell/harness_bootstrap.sh`——确认 bootstrap 入口 `harness_init` 与 source 链
  - 读 `engineering/harness/lib/shell/harness_observability.sh`——确认 observability 公共 API（log_info/warn/error、log_result、step_begin/end、harness_status_emit、harness_tmp_file/dir、on_err、harness_find_upstream_base、harness_on_exit_add、harness_exit 等）
  - 读 `engineering/harness/lib/python/harness_path_util.py`——确认 `path(key)` / `ensure_dir(key)` 等公共函数
  - 读 `engineering/harness/lib/bat/harness_path_util.bat`——确认 bat 版入口
  - 读 `../harness/README.md` 的「lib 公共能力速查」段（L53-74）——lib/README 的 API 速查应与之对齐，避免两处不一致

- [ ] **Step 2：重写「定位」章节**

  - **是什么**：公共库，为 `engineering/` 下所有脚本（shell/python/bat）提供统一的路径解析、日志、结构化 step、错误捕获、产物归档能力
  - **职责边界**：做公共基础设施函数；不做业务逻辑、不做 workflow 编排
  - **上下游依赖**：被 `scripts/*.sh`、`workflows/*/*.sh`、`loop/scripts/*.sh` 通过 `harness_bootstrap.sh` 统一加载；依赖 `config/harness-paths.conf`（路径数据源）

- [ ] **Step 3：重写「大纲」章节**

  大纲表列出：定位 / 目录说明 / 使用方式 / 公共 API 速查 / 关联资源。

- [ ] **Step 4：重写「目录说明」章节**

  表格三列，保留现有 L7-18 目录树但转为表格：
  - `shell/harness_path_util.sh` — 统一路径工具（REPO_ROOT 定位 + paths.conf 加载）— 被 bootstrap source
  - `shell/harness_bootstrap.sh` — bootstrap 入口（source path_util + observability）— 业务脚本统一入口
  - `shell/harness_observability.sh` — 维测公共库（日志/step/artifact/tmp/upstream）— 被 bootstrap source
  - `python/harness_path_util.py` — Python 版路径工具 — `from harness_path_util import path, ensure_dir`
  - `bat/harness_path_util.bat` — bat 版路径工具 — `call ... harness_path_util.bat`

- [ ] **Step 5：重写「使用方式」章节**

  保留现有 L26-49 三种语言的加载示例代码块（shell source bootstrap / shell source path_util / python import / bat call），核对路径相对引用正确（`$SCRIPT_DIR/../../lib/shell/...`）。

- [ ] **Step 6：扩展「公共 API 速查」扩展块**

  现有 L51-55 仅一句指向 rules。扩充为分三类速查表（函数名 / 作用 / 所属文件）：
  - **路径类**：`harness_path <KEY>`、`harness_env_path`、`harness_pythonpath`（shell）；`path(key)`、`ensure_dir(key)`（python）
  - **日志/步骤类**：`log_info/warn/error`、`log_result`、`step_begin/end`
  - **状态/产物/错误类**：`harness_status_emit`、`harness_tmp_file/dir`、`on_err`、`harness_find_upstream_base`、`harness_report_no_upstream`、`harness_on_exit_add`、`harness_exit`
  表后保留 API 边界声明：「业务脚本只能使用不带下划线前缀的公共 API；`_H_*`/`_h_*` 为库内部私有，禁止直接依赖。」并指向 `../rules/script-observability.md`（observability API 详解）与 `../rules/path-management.md`（路径 API 详解）。

- [ ] **Step 7：新增「关联资源」章节**

  四枚举表格：
  - 关联规则：`../rules/script-observability.md`（observability API 详解）/ `../rules/path-management.md`（PATH-001，路径 API 详解）
  - 关联配置：`../config/harness-paths.conf`（路径 KEY 单一事实源）
  - （无设计文档、无关联 workflow——本目录为被动依赖库）

- [ ] **Step 8：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0。

- [ ] **Step 9：Commit**

  ```bash
  git add engineering/harness/lib/README.md
  git commit -m "重构(docs): harness/lib/README.md 按核心 5 节校准，扩充公共 API 速查表"
  ```

---

### Task 5.6：harness/scripts/README.md 校准（.bat 注意事项 SSOT）

**Files:**
- Modify: `engineering/harness/scripts/README.md`（80 → ~95 行）

**保留扩展块**：Windows `.bat` 注意事项作为 **SSOT**（spec §八 D3 决策：harness/scripts/README 是 .bat 注意事项单一事实源，loop/scripts/README 将改为链接引用——loop 部分属阶段 5b，本 Task 只确保 SSOT 完整）。

**步骤：**

- [ ] **Step 1：重新通读 scripts/ 目录与现有 README**

  执行以下读取，确认脚本清单与 .bat 说明完整：
  - 读 `engineering/harness/scripts/README.md` 全文（80 行）
  - `ls engineering/harness/scripts/`（确认 4 个脚本：mk_rpi5_full_image.sh / validate_harness_docs.sh / validate_harness_scripts.sh / validate_harness_config.sh）
  - 读 `validate_harness_docs.sh` 前 40 行——确认 README 对其校验项的描述（README 链接/文件清单/PlantUML 闭合/WORKFLOW front matter）与实际实现一致
  - 读 `validate_harness_scripts.sh` 前 30 行——确认校验项描述一致
  - 读 `validate_harness_config.sh` 前 30 行——确认校验项描述一致
  - 读 `mk_rpi5_full_image.sh` 前 20 行——确认 README「一键编译打包」描述准确
  - 读 `engineering/loop/scripts/README.md`——确认其 .bat 段当前仍为全文复制（阶段 5b 将改为链接），本 Task 确保 harness 版为权威完整版
  - 读 `engineering/loop/scripts/start_rp5_serial_host.bat`——确认 .bat 注意事项中引用的文件路径与检测命令正确

- [ ] **Step 2：重写「定位」章节**

  - **是什么**：harness 公共工程工具脚本集——编译打包、静态校验（文档/脚本/配置三类 validator）
  - **职责边界**：做 harness 级独立脚本与校验器；不做 loop 专属入口（loop 入口在 `loop/scripts/`）
  - **上下游依赖**：被 `AGENTS.md`、各 workflow、开发者手动调用；validator 依赖 `config/*.yaml`、`lib/`

- [ ] **Step 3：重写「大纲」章节**

  大纲表列出：定位 / 目录说明 / 使用方式 / Windows .bat 注意事项 / 关联资源。

- [ ] **Step 4：重写「目录说明」章节**

  表格三列（脚本 / 作用 / 调用方式），登记 4 个脚本 + 一行说明已迁出至 loop/scripts/ 的 4 个文件（le.sh / le_runs_cleanup.sh / rp5_serial_helper.py / start_rp5_serial_host.bat）：
  - `mk_rpi5_full_image.sh` — RPi5 AOSP 一键编译打包 — `bash engineering/harness/scripts/mk_rpi5_full_image.sh`
  - `validate_harness_docs.sh` — 文档/契约层校验 — `bash ... validate_harness_docs.sh`
  - `validate_harness_scripts.sh` — bash 合规校验 — `bash ... validate_harness_scripts.sh`
  - `validate_harness_config.sh` — 配置层校验 — `bash ... validate_harness_config.sh`
  - （迁出）`le.sh` 等 → 见 `../../loop/scripts/README.md`

- [ ] **Step 5：重写「使用方式」章节**

  「入口清单」表 + validator 调用示例。保留现有 L11-20 的三个 validator 详细说明（校验项 + 退出码语义 0/1/3），核对描述与脚本实际一致。

- [ ] **Step 6：保留「Windows .bat 脚本注意事项」扩展块（SSOT，完整保留）**

  完整保留现有 L28-80 全部内容，**作为 .bat 注意事项的单一事实源**：
  - 换行符必须 CRLF（失败症状 + 检测命令 `file ...` + 修复工具表 unix2dos/VS Code/Git）
  - 编码必须纯 ASCII（检测命令 + 中文说明只能放 README/.md）
  - 推荐在 CMD 中运行
  - 修改后必须验证（两条 python3 检测命令）
  在小节开头追加一句声明：「**本节是 .bat 注意事项的单一事实源；`loop/scripts/README.md` 以链接形式引用本节。**」

- [ ] **Step 7：新增「关联资源」章节**

  四枚举表格：
  - 关联规则：`../rules/script-observability.md`（脚本改造时）/ `../rules/build-reference.md`→已迁移为 `../reference/build-reference.md`（编译命令参考，阶段 2 已迁移，确认路径）
  - 关联配置：`../config/harness-paths.conf`（编译路径 KEY：ENV_KERNEL_WS/ENV_AOSP_WS/ENV_KERNEL_OUT/ENV_CLANG_BIN/ENV_WINDOWS_IMG_DIR）
  - 关联 workflow：`../workflows/`（validator 被 workflow 自检环节调用）
  - （无设计文档）

- [ ] **Step 8：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0。

- [ ] **Step 9：Commit**

  ```bash
  git add engineering/harness/scripts/README.md
  git commit -m "重构(docs): harness/scripts/README.md 按核心 5 节校准，.bat 注意事项确立为 SSOT"
  ```

---

### Task 5.7：harness/templates/README.md 校准（新增 2 份模板登记）

**Files:**
- Modify: `engineering/harness/templates/README.md`（17 → ~45 行）

**核心动作**：新增 `engineering-readme-template.md` 与 `rules-template.md`（阶段 1 已创建）的登记（spec §十同步影响项）。

**步骤：**

- [ ] **Step 1：重新通读 templates/ 目录与现有 README**

  执行以下读取，确认模板清单完整：
  - 读 `engineering/harness/templates/README.md` 全文（17 行）
  - `ls engineering/harness/templates/`（确认阶段 1 产出的 2 份新模板已存在：engineering-readme-template.md / rules-template.md；原有 3 份：module-template.md / module-readme-template.md / diagnosis-report-template.md）
  - 读 `engineering-readme-template.md`——确认其核心 5 节定义与扩展块清单，以便 README 登记描述准确
  - 读 `rules-template.md`——确认其核心 5 节（规则 ID/适用范围/MUST/MUST NOT/例外清单）+ 附录定义
  - 读 `module-template.md` 与 `module-readme-template.md` 各前 20 行——确认现有登记描述准确
  - 读 `diagnosis-report-template.md` 前 15 行——确认描述准确
  - 读 `../workflows/sync-patchs-to-doc/WORKFLOW.md` 前 30 行——确认「只读契约」与 TEMPLATE-CONFLICT 机制描述与实际一致

- [ ] **Step 2：重写「定位」章节**

  - **是什么**：文档结构模板集——engineering 下 README 与 rules 的章节契约，以及技术文档（docs/01-*、docs/02-*）的设计模板
  - **职责边界**：做结构约束模板；不做内容撰写（模板只读，由 sync-patchs-to-doc workflow 消费）
  - **上下游依赖**：被 `sync-patchs-to-doc` workflow 作为只读契约消费；engineering 下所有 README 遵循 `engineering-readme-template.md`，所有 rules 遵循 `rules-template.md`

- [ ] **Step 3：重写「大纲」章节**

  大纲表列出：定位 / 目录说明 / 使用方式 / 关联资源（本目录为纯模板承载层，无额外扩展块）。

- [ ] **Step 4：重写「目录说明」章节**

  表格三列（文件 / 用途 / 适用对象），登记全部 5 份模板：
  - `engineering-readme-template.md` — engineering 下所有 README 的核心 5 节骨架 + 扩展块选配清单 — engineering/*/README.md
  - `rules-template.md` — rules/ 下所有规则文档的核心 5 节（规则 ID/适用范围/MUST/MUST NOT/例外清单）+ 附录 — harness/rules/*.md
  - `module-readme-template.md` — 模块级 README 模板（4+1 视图）— 特性目录入口文档（docs/01-*/README.md）
  - `module-template.md` — 模块详细设计文档模板（完整章节）— 特性下子模块文档（docs/01.01-*.md）
  - `diagnosis-report-template.md` — Loop boot 诊断报告模板（7 节）— Loop 诊断报告产出

- [ ] **Step 5：重写「使用方式」章节**

  本目录无可执行入口，作为只读契约承载层。说明：「新增/修改 engineering 下 README 时遵循 `engineering-readme-template.md`；新增/修改 rules 时遵循 `rules-template.md`；技术文档同步由 `sync-patchs-to-doc` workflow 按模板校验，diff 无法归入现有章节时标记 TEMPLATE-CONFLICT。」

- [ ] **Step 6：保留「约束」要点融入使用方式或目录说明**

  保留现有 L14-17 的三条约束（只读 / TEMPLATE-CONFLICT / 缺失多余章节自检），并入「使用方式」章节。

- [ ] **Step 7：新增「关联资源」章节**

  四枚举表格：
  - 关联规则：`../rules/doc-paths.md`（文档路径）/ `../rules/plantuml.md`（模板内 PlantUML 约束）
  - 关联 workflow：`../workflows/sync-patchs-to-doc/`（消费模板为只读契约）
  - 设计文档：`docs/specs/2026-06-21-engineering-doc-refactor-design.md` §四（模板定义）
  - （无关联配置）

- [ ] **Step 8：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0（validator Step3 扫描 templates/*.md 的 PlantUML 闭合与花括号占位符，新模板若含示例占位符需确认不误报）。

- [ ] **Step 9：Commit**

  ```bash
  git add engineering/harness/templates/README.md
  git commit -m "重构(docs): harness/templates/README.md 按核心 5 节校准，新增 engineering-readme-template 与 rules-template 登记"
  ```

---

### Task 5.8：harness/workflows/README.md 校准

**Files:**
- Modify: `engineering/harness/workflows/README.md`（28 → ~50 行）

**保留扩展块**：「结构约定」（spec §六 6.2）。

**步骤：**

- [ ] **Step 1：重新通读 workflows/ 目录与现有 README**

  执行以下读取，确认工作流清单与结构约定与实际一致：
  - 读 `engineering/harness/workflows/README.md` 全文（28 行）
  - `ls -R engineering/harness/workflows/`（确认 4 个工作流子目录：git-push-to-server / sync-code-to-patchs / revert-code-from-patchs / sync-patchs-to-doc，各含 README.md + WORKFLOW.md + *.sh）
  - 读 4 份 `WORKFLOW.md` 的 front matter（name/description）——确认 README 工作流清单表的「核心语义」描述与 WORKFLOW description 一致
  - 读 4 份 `*.sh` 的前 15 行——确认入口脚本名（collect_diff.sh / commit_and_push.sh / sync_code_to_patchs.sh / revert_code_from_patchs.sh / sync_patchs_to_doc.sh）与 README「入口」列一致
  - 读 `../lib/shell/harness_bootstrap.sh`——确认「结构约定」中「脚本统一通过 bootstrap 接入维测库」的描述准确
  - 读 `../rules/script-observability.md` 前 20 行——确认引用关系正确

- [ ] **Step 2：重写「定位」章节**

  - **是什么**：多步闭环工作流集——每个子目录是一个完整流程（可执行脚本 + WORKFLOW.md 流程契约）
  - **职责边界**：做 harness 公共工程 workflow（git/归档/回退/文档同步）；不做 loop 专属 workflow（在 `../../loop/workflows/`）
  - **上下游依赖**：被 `.opencode/commands/*.md`（4 份）通过 `@WORKFLOW.md` 注入 AI 上下文；依赖 `lib/`（bootstrap）、`config/`（scope/doc-sync mapping）、`rules/`

- [ ] **Step 3：重写「大纲」章节**

  大纲表列出：定位 / 目录说明 / 使用方式 / 结构约定 / 关联资源。

- [ ] **Step 4：重写「目录说明」章节**

  保留现有 L7-12 的工作流清单表（工作流 / 触发场景 / 核心语义 / 入口），核对 4 行与实际子目录一致。每行「入口」列填实际脚本名链。

- [ ] **Step 5：重写「使用方式」章节**

  说明每个工作流的进入方式：「按触发场景进入对应子目录，先读其 WORKFLOW.md 了解流程契约与确认门，再执行入口脚本。」列出 4 个入口脚本的调用示例（如 `bash engineering/harness/workflows/git-push-to-server/collect_diff.sh`）。

- [ ] **Step 6：保留「结构约定」扩展块**

  保留现有 L14-21 的结构约定（每个子目录含 *.sh + WORKFLOW.md + README.md；脚本通过 bootstrap 接入维测；README 极简不重复流程）。补充一句明确 WORKFLOW.md 的工具消费事实：「WORKFLOW.md 被 `.opencode/commands/*.md` 通过 `@` 注入 AI 上下文，且被 `validate_harness_docs.sh` 校验 front matter（name/description）。」

- [ ] **Step 7：保留「脚本默认产物位置」要点**

  现有 L23-28 的产物位置说明（日志/中间产物/临时文件路径约定）保留，可作为「结构约定」的子段或独立段。

- [ ] **Step 8：新增「关联资源」章节**

  四枚举表格：
  - 关联规则：`../rules/script-observability.md`（脚本维测）/ `../rules/source-code-modify.md`（归档/回退约束）
  - 关联配置：`../config/scope-mapping.yaml`（git-push 消费）/ `../config/doc-sync-mapping.yaml`（sync-patchs-to-doc 消费）/ `../config/baseline-status.yaml`（revert 消费）
  - 设计文档：`docs/specs/2026-06-21-engineering-doc-refactor-design.md`（文档重构，WORKFLOW 保留决策）
  - 关联 workflow：（自身即 workflow 索引层；loop 专属 workflow 见 `../../loop/workflows/README.md`）

- [ ] **Step 9：验证**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0（含 4 份 WORKFLOW.md 的 front matter 校验）。

- [ ] **Step 10：Commit**

  ```bash
  git add engineering/harness/workflows/README.md
  git commit -m "重构(docs): harness/workflows/README.md 按核心 5 节校准，保留结构约定扩展块"
  ```

---
