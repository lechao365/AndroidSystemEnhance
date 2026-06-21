## 阶段 5（后半）：README 全量校准（中型后 6 个 + 轻型 11 份）

> **前置依赖**：阶段 1–4 全部完成；阶段 5 前半（Task 5.1–5.8）已完成——`loop/README.md` 已精简、`engineering/README.md` 已成为边界 SSOT、harness 下 config/lib/scripts/templates/workflows 五份 README 已按核心 5 节校准（其中 `harness/scripts/README.md` 已是 `.bat` 注意事项的 SSOT，D3 决策）。
>
> **本阶段产出**：loop 下中型 README（scripts / connection / protocol）+ patchs/rpi5 + output 按 `engineering-readme-template.md` 核心 5 节校准，`loop/scripts/README.md` 的 `.bat` 注意事项改为链接引用 `harness/scripts/README.md`（SSOT 单一化）；11 份轻型 README 严格按 B1 写齐核心 5 节（即使内容仅一行）；`rp5-serial/README.md` 阶段 4 已重写，本阶段仅做模板对齐校备。
>
> **共同约束（所有 Task 适用）**：
> - 严格遵循 `engineering/harness/templates/engineering-readme-template.md` 核心 5 节骨架（定位 / 大纲 / 目录说明 / 使用方式 / 关联资源），顶部嵌入「AI 读取指引」+「大纲」表；大纲表强制列出全部章节（核心 + 已启用扩展块），每行三列（章节 / 内容摘要 / 何时读取）。
> - **每个 README Task 的第一步必须重新通读该目录下所有代码/配置/子目录与现有 README**，结合新材料重构——补齐缺失内容、删除失效内容，而非纯格式调整。
> - 关联资源类型固定四枚举：设计文档 / 关联规则 / 关联 workflow / 关联配置（无某类时该行省略，不写"无"）。
> - `validate_harness_docs.sh` 仅扫描 `engineering/harness/`（HARNESS_DIR），**不覆盖** `engineering/loop/`、`engineering/output/`、`patchs/`。这些目录的 Task 验证 Step 需人工核对核心 5 节齐全 + Markdown 链接可达；仅 harness 下 Task（Task 5.16）要求运行 validator。
> - commit message 前缀：`重构(docs): ...`。
> - 步骤用 `- [ ]` 复选框。

---

### Task 5.9：loop/scripts/README.md 中型校准 + .bat 注意事项去重（D3）

**Files:**
- Modify: `engineering/loop/scripts/README.md`（187 行 → ~60 行）

> **SSOT 关系**：`harness/scripts/README.md`（阶段 5 前半 Task 5.6 已校准）是 Windows `.bat` 注意事项的**唯一事实源**；本 Task 将 `loop/scripts/README.md` 第 107–186 行的重复 `.bat` 注意事项整段删除，改为一句话 + 链接引用。

- [ ] **Step 1：重新通读 loop/scripts 目录与现有 README**

  执行：
  - `ls -la engineering/loop/scripts/` 确认实际文件清单：`le.sh`、`le_runs_cleanup.sh`、`rp5_serial_helper.py`、`start_rp5_serial_host.bat`、`README.md`。
  - 读 `engineering/loop/scripts/le.sh`、`le_runs_cleanup.sh`、`rp5_serial_helper.py`、`start_rp5_serial_host.bat`，核对当前 README 第 17–104 行的「le.sh / le_runs_cleanup.sh / start_rp5_serial_host.bat」章节内容是否与脚本实际参数、退出码、用法一致（补齐缺失、删除失效）。
  - 读 `engineering/harness/scripts/README.md` 第 28–80 行（`.bat` 注意事项 SSOT 段），确认其已含完整 `.bat` 规则（CRLF / 纯 ASCII / CMD 运行 / 修改后验证）——确认无信息丢失后再删除本目录的重复段。
  - 关注：`rp5_serial_helper.py` 在现有 README 中仅在「文件说明」一行提及（第 8 行），需在目录说明表补一句用途；`start_rp5_serial_host.bat` 的用法（第 79–103 行）应保留为「使用方式」的精简版（默认 COM5/115200/9700 + 自定义示例），完整 `.bat` 格式规则不在本 README 展开。

- [ ] **Step 2：重写 README，按核心 5 节骨架**

  写入 `engineering/loop/scripts/README.md`，结构如下（约 60 行）：

  - **顶部 AI 读取指引**（引用模板原文）
  - **## 定位**：是什么 = loop engineering 专属脚本入口（CLI wrapper、产物清理、串口辅助、Windows host 启动器）；职责边界 = 允许依赖 `harness/lib/`，禁止把 loop 专属脚本放回 `harness/scripts/`；上下游依赖 = 被 `le.sh` / `start_rp5_serial_host.bat` 调用，依赖 `harness/lib/`（bootstrap/path/observability）。
  - **## 大纲**：列出 5 节 + 无扩展块。
  - **## 目录说明**：四行表格——`le.sh`（Loop Engineering CLI wrapper，底层调 `loop_core.cli`）、`le_runs_cleanup.sh`（runs/ 产物清理，保留最新 N 份）、`rp5_serial_helper.py`（供 loop host case / workflow 使用的串口辅助工具，如 adb endpoint 发现）、`start_rp5_serial_host.bat`（Windows 前台启动 rp5-serial Host，独占物理串口）。
  - **## 使用方式**：
    - **快速开始**：`le.sh` fixture 模式 + live 模式各一个最小示例（从现有第 30–44 行精简，保留 `--suite/--fixture/--device-profile/--case-dirs/--artifacts-dir` 与 live 模式 `--host/--port` 两组参数）。
    - **入口清单**：表格四行，列出 `le.sh`、`le_runs_cleanup.sh --keep N --dry-run`、`start_rp5_serial_host.bat COM5 115200 9700`、`rp5_serial_helper.py`（被 workflow 调用，非 CLI 直接入口）。
    - **退出码**：保留 `le_runs_cleanup.sh` 的 0/1/3/4 含义（从现有第 75 行搬入）。
    - **`.bat` 格式规则**：**删除现有第 107–186 行全部重复内容**，替换为一行："Windows `.bat` 文件的格式要求（CRLF / 纯 ASCII / CMD 运行 / 修改后验证）见 `engineering/harness/scripts/README.md`（SSOT，D3 决策），本 README 不重复。"
  - **## 关联资源**：
    - 关联规则：`../../harness/rules/path-management.md`（PATH-001，`.bat` 路径工具约束）
    - 关联 workflow：`../workflows/lcview-adb-run/`（`le.sh` 编排 lcview suite）/ `../../harness/workflows/`（间接经 `le.sh` 调用）
    - 设计文档：`docs/specs/2026-06-19-loop-engineering-design.md`（loop 架构）

- [ ] **Step 3：验证（人工核对 + 链接可达性）**

  `validate_harness_docs.sh` 不覆盖 `engineering/loop/`，故人工核对：
  - 核心 5 节齐全，大纲表列全部章节。
  - 第 107–186 行的重复 `.bat` 段已删除；剩余 `.bat` 说明仅一行链接引用。
  - 行数 ≤ 70。
  - Markdown 链接 `../../harness/scripts/README.md`、`../../harness/rules/path-management.md` 指向文件存在（用 `ls` 确认）。

- [ ] **Step 4：Commit**

  ```bash
  git add engineering/loop/scripts/README.md
  git commit -m "重构(docs): loop/scripts/README.md 按核心 5 节校准，.bat 注意事项改为链接引用 harness/scripts（D3 SSOT 单一化）"
  ```

---

### Task 5.10：loop/connection/README.md 中型校准（保留「设计原则」扩展块）

**Files:**
- Modify: `engineering/loop/connection/README.md`（17 行 → ~50 行）

- [ ] **Step 1：重新通读 loop/connection 目录与现有 README**

  执行：
  - `ls -la engineering/loop/connection/` 确认子目录：`profiles/`、`protocol/`、`providers/`（当前仅 `rp5-serial`、`adb` 两个 provider——注意 spec 写于 adb provider 落地前，现 README 第 11 行仍写"当前仅 rp5-serial"，需更正为 rp5-serial + adb）。
  - 读现有 README（17 行）：已有「设计原则」三条款（协议与实现分离 / profile 与运行配置分离 / provider 自治），需保留为扩展块。
  - 读 `protocol/README.md`、`profiles/README.md`、`providers/rp5-serial/README.md`、`providers/adb/README.md` 的「定位」段，确保 connection/README 的「目录说明」与子 README 一致、不越界（子目录细节交给其自身 README）。
  - 关注：现 README 无「关联资源」「使用方式」节，需补齐；provider 列表需更新。

- [ ] **Step 2：重写 README，核心 5 节 + 「设计原则」扩展块**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = loop engineering 连接基础设施（跨 provider 协议契约 + provider/device 配置语义 + 具体 provider 实现）；职责边界 = 定义契约与语义，不含业务 case；上下游依赖 = 被 `loop/core` 与 `loop/workflows` 消费，依赖 `loop/contracts`。
  - **## 大纲**：列出核心 5 节 + 「设计原则」扩展块共 6 行。
  - **## 目录说明**：三行表格——`protocol/`（跨 provider 协议文档，不绑实现）、`profiles/`（provider/device 配置语义，描述「如何理解这台板子」）、`providers/`（具体 provider 实现：`rp5-serial/` 串口、`adb/` 网络 ADB）。
  - **## 使用方式**：本目录无可执行入口，仅作为连接能力承载层。快速开始指向 `providers/rp5-serial/README.md`（Host 启动）与 `providers/adb/README.md`（adb transport）。
  - **## 设计原则**（扩展块，保留现有三条款，措辞校准）：
    1. 协议与实现分离：`protocol/` 定义 host/client 契约，provider 实现遵循但不内嵌协议定义。
    2. profile 与运行配置分离：`profiles/` 描述设备语义（prompt marker / boot marker / timeout 等），provider 自身只保留最小运行配置。
    3. provider 自治：每个 provider 同仓管理 host/client/shared/tests，运行位置可不同但代码集中。
  - **## 关联资源**：
    - 设计文档：`docs/specs/2026-06-19-loop-engineering-design.md`（connection 域设计）
    - 关联 workflow：`../workflows/lcview-adb-run/`（使用 rp5-serial bootstrap + adb feature）

- [ ] **Step 3：验证（人工核对）**

  - 核心 5 节 + 设计原则扩展块齐全，大纲表列 6 行。
  - 「目录说明」provider 列表已更新为 rp5-serial + adb（不再写"当前仅 rp5-serial"）。
  - 链接 `../workflows/lcview-adb-run/`、`docs/specs/2026-06-19-loop-engineering-design.md` 可达。

- [ ] **Step 4：Commit**

  ```bash
  git add engineering/loop/connection/README.md
  git commit -m "重构(docs): loop/connection/README.md 按核心 5 节校准，保留设计原则扩展块，更新 provider 列表"
  ```

---

### Task 5.11：loop/connection/protocol/README.md + rp5-serial/README.md 模板对齐校备

**Files:**
- Modify: `engineering/loop/connection/protocol/README.md`（20 行 → ~35 行）
- Check（不重写）：`engineering/loop/connection/providers/rp5-serial/README.md`（阶段 4 已重写为含「运行流程」扩展块，本 Task 仅做模板对齐校备记录）

> **说明**：`rp5-serial/README.md` 在阶段 4 已按 R1 决策重写（融入 WORKFLOW 为「运行流程」扩展块、移除"详见 WORKFLOW.md"指针、协议链接更新为 `rp5-serial-protocol.md`）。本 Task 主体是 `protocol/README.md` 的核心 5 节校准；对 `rp5-serial/README.md` 仅做"是否已含核心 5 节 + 运行流程扩展块"的核对，若阶段 4 已齐则不改动。

- [ ] **Step 1：重新通读 protocol 目录与 rp5-serial 当前状态**

  执行：
  - `ls -la engineering/loop/connection/protocol/` 确认文件：`README.md`、`rp5-serial-protocol.md`（阶段 4 已由 `rp5_serial_protocol.md` 重命名，确认文件名是小写连字符）。
  - 读 `engineering/loop/connection/protocol/rp5-serial-protocol.md`（68 行）：了解协议覆盖范围（传输层 JSON Lines / 操作列表 / 响应结构 / 错误码），用于校准 protocol/README 的「目录说明」与「定位」。
  - 读现有 `protocol/README.md`（20 行）：现「当前文档」表链接已是 `rp5_serial_protocol.md`——**确认阶段 4 是否已更新为 `rp5-serial-protocol.md`**；若仍是旧名，本 Task 必须更正。
  - 读 `rp5-serial/README.md`（阶段 4 产物，118 行）：核对是否已含核心 5 节（定位/大纲/目录说明/使用方式/关联资源）+ 「运行流程」扩展块；若已齐，仅记录"阶段 4 已覆盖，本阶段跳过"。

- [ ] **Step 2：重写 protocol/README.md，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = host 与 client 之间的协议契约文档承载层；职责边界 = 只承载协议定义（传输层 / 操作列表 / 响应结构 / 错误码 / 跨 provider 复用契约），不承载 provider 实现（编解码在 `providers/<provider>/python/`）；上下游依赖 = 被 `providers/rp5-serial/` 与 `providers/adb/`（如适用）遵循，无上游依赖。
  - **## 大纲**：列 5 节，无扩展块。
  - **## 目录说明**：一行表格——`rp5-serial-protocol.md`（rp5-serial host/client 协议定义：JSON Lines 传输、操作列表、统一响应、错误码）。
  - **## 使用方式**：本目录无可执行入口，仅作为协议文档承载层。查阅协议请读 `rp5-serial-protocol.md`。
  - **## 关联资源**：
    - 设计文档：`docs/specs/2026-06-19-loop-engineering-design.md`（协议设计章节）
    - 关联 workflow：`../providers/rp5-serial/`（遵循本协议的 provider 实现）

- [ ] **Step 3：核对 rp5-serial/README.md 模板对齐（不重写）**

  逐项核对 `rp5-serial/README.md` 是否满足：
  - 含核心 5 节（定位 / 大纲 / 目录说明 / 使用方式 / 关联资源）。
  - 含「运行流程」扩展块（阶段 4 融入 WORKFLOW 内容：拓扑 / 启动方式 / Host 前台运行 / Client 三模式）。
  - 大纲表列出核心 5 节 + 运行流程扩展块。
  - 无"详见 WORKFLOW.md"指针（阶段 4 已移除）。
  - 关联资源中协议链接为 `../../protocol/rp5-serial-protocol.md`（小写连字符）。
  - `rp5-serial/WORKFLOW.md` 已在阶段 4 删除（确认文件不存在：`ls engineering/loop/connection/providers/rp5-serial/WORKFLOW.md` 应报 No such file）。
  
  若全部满足，本步仅记录结论，不改动文件。若发现缺失（如大纲表未列运行流程），按模板补齐后再 commit。

- [ ] **Step 4：验证（人工核对）**

  `validate_harness_docs.sh` 不覆盖 loop/，故人工核对：
  - `protocol/README.md` 核心 5 节齐全，链接指向 `rp5-serial-protocol.md`（非旧名 `rp5_serial_protocol.md`）。
  - `rp5-serial/README.md` 模板对齐项全部通过。
  - `rp5-serial/WORKFLOW.md` 不存在。

- [ ] **Step 5：Commit**

  仅当 `protocol/README.md` 有改动时 commit（`rp5-serial/README.md` 若阶段 4 已齐则不纳入）：

  ```bash
  git add engineering/loop/connection/protocol/README.md
  git commit -m "重构(docs): protocol/README.md 按核心 5 节校准，链接更新为 rp5-serial-protocol.md（阶段 4 已重命名）"
  ```
  
  若 Step 3 发现 `rp5-serial/README.md` 需补齐，则一并 `git add` 并在 commit message 追加"+ rp5-serial 模板对齐校备"。

---

### Task 5.12：patchs/rpi5/README.md 中型校准（保留文件映射表）

**Files:**
- Modify: `patchs/rpi5/README.md`（230 行 → ~210 行）

> **关键约束**：「文件映射表」（kernel/modified、kernel/new、aosp/modified、aosp/new、others 五张表）由 `sync-code-to-patchs` workflow **自动维护**，本 Task **不得改动映射表内容**，只校准映射表之外的非映射表章节（概述 / 工作流 / 特性表 / 目录结构 / 回写命令），按核心 5 节重组。

- [ ] **Step 1：重新通读 patchs/rpi5 目录与现有 README**

  执行：
  - `ls -la patchs/rpi5/` 确认顶层：`README.md`、`kernel/`、`aosp/`、`others/`、`manifest.yaml`。
  - `ls patchs/rpi5/kernel/modified drivers/usb/storage/`、`ls patchs/rpi5/kernel/new/vendor/lechao/`、`ls patchs/rpi5/aosp/modified/device/brcm/rpi5/`、`ls patchs/rpi5/aosp/new/`、`ls patchs/rpi5/others/`：核对 README 中目录结构树（第 40–96 行）与文件映射表（第 163–230 行）是否与实际文件一致——**记录任何不一致，但映射表内容不动（交给 sync-code-to-patchs），仅修正「目录结构」树**。
  - 读 `patchs/rpi5/manifest.yaml`：了解 manifest 字段，用于「关联资源」。
  - 读 `engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md` 前 30 行：确认"自动更新 README 文件映射表"的语义边界——确认映射表是脚本写入区，README 其余部分是人工维护区。
  - 关注：现 README 第 100–159 行「回写命令」是 patchs→workspace 部署流程，属于「使用方式」；需保留但归入核心 5 节的「使用方式」节。

- [ ] **Step 2：重写 README，核心 5 节 + 文件映射表保留**

  结构（映射表原文保留，其余按 5 节重组）：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = Raspberry Pi 5 平台 AOSP + Linux kernel 定制改动的归档镜像（`~/workspace/` 编译源码树的精确镜像）；职责边界 = 归档层，非编译树（编译在 `~/workspace/`）；上下游依赖 = 由 `sync-code-to-patchs` 从 workspace 写入，被 `revert-code-from-patchs` 读回 workspace、被 `sync-patchs-to-doc` 读为文档源。
  - **## 大纲**：列核心 5 节 + 「文件映射表」扩展块（标注"由 sync-code-to-patchs 自动维护"）共 6 行。
  - **## 目录说明**：四行表格——`kernel/`（← `~/workspace/rpi5-kernel-build/common/`，modified diff + new 全新文件）、`aosp/`（← `~/workspace/aosp/`，modified diff + new 全新文件）、`others/`（树莓派5专用工具，直接 Git 维护，不同步）、`manifest.yaml`（文件清单元数据，由 sync-code-to-patchs 维护）。
  - **## 使用方式**：
    - 本目录无可执行入口，作为归档承载层。
    - **归档（workspace → patchs）**：`/sync-code-to-patchs` 命令，自动镜像 + 更新 manifest + 更新本 README 文件映射表。
    - **回退（patchs → workspace）**：`/revert-code-from-patchs` 命令，详见 `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`。
    - **手动回写部署**（保留现有第 100–159 行 kernel/aosp patch 应用 + 烧写验证命令，归为本节子小节）。
  - **## 文件映射表**（扩展块，**原文保留第 163–230 行全部五张表，不改一字**）：块首加一行说明 "> 以下映射表由 `sync-code-to-patchs` 自动维护，请勿手动编辑。"
  - **## 关联资源**：
    - 关联 workflow：`engineering/harness/workflows/sync-code-to-patchs/`（归档）/ `revert-code-from-patchs/`（回退）/ `sync-patchs-to-doc/`（文档同步）
    - 关联规则：`engineering/harness/rules/source-code-modify.md`（workspace 是源头，patchs 是归档）
    - 关联配置：`engineering/harness/config/scope-mapping.yaml`（commit scope 判定）
    - 设计文档：`docs/specs/2026-06-21-engineering-doc-refactor-design.md`（文档重构，映射表保留决策）

  > **特性表处置**：现有第 18–35 行「包含的特性」（内核态 / 用户态两张特性表）归入「目录说明」之后作为独立小节「### 特性概览」，并在大纲表登记；不删除（信息有价值），但精简为索引性（详细在映射表）。

- [ ] **Step 3：验证（人工核对）**

  - 核心 5 节 + 文件映射表扩展块齐全，大纲表列全部章节。
  - **文件映射表五张表内容与重写前逐字一致**（用 `git diff` 确认映射表区域无改动）。
  - 「目录结构」树与实际目录一致（`others/usb-verify/` 已在映射表，目录树也含）。
  - 链接 `engineering/harness/workflows/*/WORKFLOW.md`、`engineering/harness/config/scope-mapping.yaml` 可达。

- [ ] **Step 4：Commit**

  ```bash
  git add patchs/rpi5/README.md
  git commit -m "重构(docs): patchs/rpi5/README.md 按核心 5 节校准，文件映射表保留（sync-code-to-patchs 自动维护），其余章节重组"
  ```

---

### Task 5.13：engineering/output/README.md 中型校准

**Files:**
- Modify: `engineering/output/README.md`（36 行 → ~45 行）

- [ ] **Step 1：重新通读 output 目录与现有 README**

  执行：
  - `ls -la engineering/output/` 确认子目录：`host-log/`、`log/`、`runs/`（含 `.pytest_cache/`——pytest 缓存，不归 README 登记）。
  - `ls engineering/output/log/`：确认实际日志子目录清单（collect_diff / commit_and_push / lcview-adb-run / le / le-runs-cleanup / loop-rp5-serial-status / mk_rpi5_full_image / sync_code_to_patchs / sync_patchs_to_doc / validate_* 等）——这些是运行时产物，README 不逐一登记，但「目录说明」需说明"log/ 下按脚本名自动建子目录"。
  - 读 `engineering/harness/lib/shell/harness_observability.sh`（若存在）或其 README：确认 log/ 轮转策略（保留最近 3 份）+ 子目录命名规则的权威描述，避免 README 与实现不符。
  - 读现有 README（36 行）：已有 host-log / log / runs 三节 + runs 自动清理段；需按核心 5 节重组，补「定位」「大纲」「关联资源」。
  - 关注：现 README 未说明 `.gitkeep` 占位机制（host-log/.gitkeep、log/.gitkeep、runs/.gitkeep），「目录说明」需提一句"各子目录通过 .gitkeep 占位纳入版本控制，运行产物本身 gitignore"。

- [ ] **Step 2：重写 README，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = 工程维测与运行产物统一目录（所有脚本运行时产生的日志和产物均落入此目录）；职责边界 = 产物承载层，不承载实现逻辑（脚本在 harness/loop）；上下游依赖 = 被 `harness_observability.sh`（log/）、`start_rp5_serial_host.bat`（host-log/）、`le.sh`（runs/）写入，**本地维测产物，不归档**（AGENTS.md 明确）。
  - **## 大纲**：列 5 节，无扩展块。
  - **## 目录说明**：三行表格——`host-log/`（rp5-serial Windows Host 产物：transcript + host 进程日志，由 `start_rp5_serial_host.bat` 写入）、`log/`（WSL2 端 harness 脚本统一日志，由 `harness_observability.sh` 管理，每脚本独立子目录 `<name>/<ts>.log` + `latest.log`，自动轮转保留最近 3 份）、`runs/`（LE 框架运行产物，按时间戳 `<ts>-<scenario>/`，含 `baseline/report.json` + `summary.txt`）。附注：各子目录通过 `.gitkeep` 占位纳入版本控制，产物本身不提交。
  - **## 使用方式**：本目录无可执行入口，仅作为产物承载层。
    - **runs/ 自动清理**：`le.sh` 每次运行结束自动调 `le_runs_cleanup.sh`，保留最新 N 份（默认 20，`LE_RUNS_KEEP` 或 `--keep N` 覆盖），仅清子目录，散文件保留。手动：`bash engineering/loop/scripts/le_runs_cleanup.sh --keep 20 --dry-run`。退出码 0/1/3/4。
  - **## 关联资源**：
    - 关联 workflow：`../harness/workflows/`（脚本运行产物落入 log/）/ `../loop/workflows/lcview-adb-run/`（产物落入 runs/）
    - 设计文档：`docs/specs/2026-06-21-engineering-doc-refactor-design.md`（output 定位：本地维测，不归档）

- [ ] **Step 3：验证（人工核对）**

  - 核心 5 节齐全，大纲表列 5 行。
  - 「目录说明」三子目录与 `ls` 实际一致。
  - 链接 `../loop/scripts/le_runs_cleanup.sh` 可达。

- [ ] **Step 4：Commit**

  ```bash
  git add engineering/output/README.md
  git commit -m "重构(docs): output/README.md 按核心 5 节校准，补定位/大纲/关联资源，说明 .gitkeep 占位机制"
  ```

---

### Task 5.14：loop 极简 README 三件套（controller / contracts / workflows）按 B1 写齐核心 5 节

**Files:**
- Modify: `engineering/loop/controller/README.md`（3 行 → ~30 行）
- Modify: `engineering/loop/contracts/README.md`（3 行 → ~30 行）
- Modify: `engineering/loop/workflows/README.md`（3 行 → ~35 行）

> **B1 决策**：占位 README 也严格按核心 5 节写齐，即使某节内容仅一行。三份独立 commit（每份改动小，便于 review）。

#### 5.14a：loop/controller/README.md

- [ ] **Step 1：重新通读 controller 目录**

  执行：
  - `ls -R engineering/loop/controller/python/`：确认 `loop_controller/{__init__.py, engine.py, policy.py, state.py}` + `tests/{test_engine.py, test_policy.py}`。
  - 读 `engine.py`（`apply_stage_result`：把 StageResult 应用到 SessionState）、`policy.py`（`decide_termination`：PASS→STOP / 超次数→STOP+escalate / 重复失败→STOP+escalate / 否则 RETRY）、`state.py`（`new_session` 工厂）、`__init__.py`（导出三函数）。
  - 读 `loop_contracts/models.py`：确认 controller 依赖的 SessionState/AttemptState/StageResult/TerminationDecision 数据模型来源。
  - 关注：controller 是纯 Python 控制面（session/attempt/状态机/terminate/retry/regression policy），无 bash 入口；「使用方式」应说明 Python import 路径与测试命令。

- [ ] **Step 2：重写 controller/README.md，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = loop engineering 控制面（session / attempt / 状态机 / terminate / retry / regression policy）；职责边界 = 决策层，不含 transport / case 定义 / 产物 IO；上下游依赖 = 依赖 `loop/contracts`（数据模型 + FailureCode），被 `loop/workflows` 与 `le.sh` 调用。
  - **## 大纲**：5 节。
  - **## 目录说明**：一行表格——`python/loop_controller/`（`engine.py` 应用阶段结果 / `policy.py` 终止决策 / `state.py` session 工厂 / `__init__.py` 公开 API）+ `python/tests/`（test_engine / test_policy）。
  - **## 使用方式**：
    - 本目录无可执行入口，作为 Python 控制面库被 import。
    - **公开 API**：`apply_stage_result(session, *, attempt_index, stage_result, decision)` / `decide_termination(*, max_attempts, current_attempt, latest_stage, previous_failure_codes)` / `new_session(session_id, workflow_id, target, max_attempts)`。
    - **测试**：`PYTHONPATH="engineering/loop/core/python:engineering/loop/controller/python:engineering/loop/contracts/python" python3 -m pytest engineering/loop/controller/python/tests/ -v`
  - **## 关联资源**：
    - 关联 workflow：`../workflows/`（消费 controller 决策）/ `../contracts/`（数据模型源）
    - 设计文档：`docs/specs/2026-06-19-loop-engineering-design.md`（控制面设计）

- [ ] **Step 3：验证 + Commit（controller）**

  人工核对 5 节齐全；链接可达。
  
  ```bash
  git add engineering/loop/controller/README.md
  git commit -m "重构(docs): loop/controller/README.md 按 B1 写齐核心 5 节（定位/API/测试/关联）"
  ```

#### 5.14b：loop/contracts/README.md

- [ ] **Step 1：重新通读 contracts 目录**

  执行：
  - `ls -R engineering/loop/contracts/python/`：确认 `loop_contracts/{__init__.py, models.py, failure_codes.py}` + `tests/test_models.py`。
  - 读 `models.py`（StageResult / AttemptState / SessionState / TerminationDecision 四 dataclass）、`failure_codes.py`（FailureCode StrEnum：NONE/RUN_FAILED/EVIDENCE_INSUFFICIENT/REPEATED_FAILURE/REGRESSION_DETECTED/DEPLOY_FATAL/SESSION_STATE_ERROR）、`__init__.py`（导出五符号）。
  - 关注：contracts 是 machine-readable contract 层，无运行时逻辑，纯数据定义；被 controller / core / workflows 共享依赖。

- [ ] **Step 2：重写 contracts/README.md，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = loop 控制面 machine-readable contract（数据模型 + 失败码枚举）；职责边界 = 纯数据定义层，不含逻辑 / transport / IO；上下游依赖 = 无上游依赖（最底层），被 `loop/controller`、`loop/core`、`loop/workflows` 共享依赖。
  - **## 大纲**：5 节。
  - **## 目录说明**：一行表格——`python/loop_contracts/`（`models.py` 四 dataclass / `failure_codes.py` FailureCode StrEnum / `__init__.py` 导出）+ `python/tests/test_models.py`。
  - **## 使用方式**：本目录无可执行入口，作为契约库被 import。测试：`PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/ -v`
  - **## 关联资源**：
    - 关联 workflow：`../controller/`（消费契约的实现层）
    - 设计文档：`docs/specs/2026-06-19-loop-engineering-design.md`（契约定义章节）

- [ ] **Step 3：验证 + Commit（contracts）**

  ```bash
  git add engineering/loop/contracts/README.md
  git commit -m "重构(docs): loop/contracts/README.md 按 B1 写齐核心 5 节，登记四 dataclass + FailureCode 枚举"
  ```

#### 5.14c：loop/workflows/README.md

- [ ] **Step 1：重新通读 workflows 目录**

  执行：
  - `ls -R engineering/loop/workflows/`：确认 `lcview-adb-run/`（含 README.md + WORKFLOW.md + run_lcview_adb_suite.sh）+ `python/loop_workflows/{__init__.py, base.py, builtin.py}` + `python/tests/test_builtin.py`。
  - 读 `base.py`（WorkflowDefinition dataclass：workflow_id + phases）、`builtin.py`（SingleRunVerifyWorkflow / MultiPhaseVerifyWorkflow）、`__init__.py`。
  - 读 `lcview-adb-run/WORKFLOW.md`：了解 loop 专属 workflow 的形态（多阶段：bootstrap→feature→fallback）。
  - 关注：现 README 第 3 行写"凡直接服务 loop suite/transport/fallback/rerun 的流程都放此目录，而不是 harness/workflows"——这是归属规则，需保留进「定位」的职责边界。

- [ ] **Step 2：重写 workflows/README.md，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = loop engineering 专属 workflow 与 phase plan 承载层；职责边界 = 凡直接服务 loop suite / transport / fallback / rerun 的流程放此目录，**不放** `harness/workflows/`（通用工程 workflow 才进 harness）；上下游依赖 = 依赖 `loop/controller`、`loop/connection`、`loop/core`，被 `le.sh` 编排。
  - **## 大纲**：5 节。
  - **## 目录说明**：两行表格——`lcview-adb-run/`（串口 bootstrap 后切 adb 跑 lcview suite，失败补采 serial fallback；含 `WORKFLOW.md` 契约 + `run_lcview_adb_suite.sh` 入口）、`python/loop_workflows/`（WorkflowDefinition 基类 + SingleRun/MultiPhase 内置定义）。
  - **## 使用方式**：本目录无可执行入口（workflow 由 `le.sh` 或 `run_lcview_adb_suite.sh` 触发）。入口清单：`lcview-adb-run/run_lcview_adb_suite.sh`（多阶段编排）。
  - **## 关联资源**：
    - 关联 workflow：`lcview-adb-run/WORKFLOW.md`（被 `.opencode/commands/le.md` 间接编排）
    - 设计文档：`docs/specs/2026-06-19-loop-engineering-design.md`（workflow 归属规则）

- [ ] **Step 3：验证 + Commit（workflows）**

  ```bash
  git add engineering/loop/workflows/README.md
  git commit -m "重构(docs): loop/workflows/README.md 按 B1 写齐核心 5 节，登记 lcview-adb-run + python loop_workflows"
  ```

---

### Task 5.15：loop/workflows/lcview-adb-run/README.md 轻型校准（保留 WORKFLOW.md 关联）

**Files:**
- Modify: `engineering/loop/workflows/lcview-adb-run/README.md`（5 行 → ~25 行）

- [ ] **Step 1：重新通读 lcview-adb-run 目录**

  执行：
  - `ls -la engineering/loop/workflows/lcview-adb-run/`：确认 `README.md`、`WORKFLOW.md`、`run_lcview_adb_suite.sh`。
  - 读 `WORKFLOW.md`（39 行）：5 阶段（serial bootstrap→adb endpoint 提取→adb feature run→失败 serial fallback→汇总）、输入参数（--serial-host/--serial-port/--adb-endpoint/--artifacts-dir/--serial-profile/--adb-profile）、7 个 failure code、归属规则。
  - 读 `run_lcview_adb_suite.sh`（3607 字节）：确认实际支持的 CLI 参数与 WORKFLOW.md 一致。
  - 关注：现 README 仅 5 行（一句话定位 + 指向 WORKFLOW.md），需按 B1 写齐 5 节，但「关联资源」必须保留指向 `WORKFLOW.md`（D-WF 决策：lcview-adb-run/WORKFLOW.md 保留）。

- [ ] **Step 2：重写 README，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = loop engineering 专属多阶段 workflow（串口 bootstrap → adb feature suite → 失败 serial fallback）；职责边界 = loop 专属，不放 `harness/workflows/`；上下游依赖 = 依赖 `loop/connection`（rp5-serial + adb provider）、`loop/controller`、`loop/core`，被 `le.sh` / `/le` 编排。
  - **## 大纲**：5 节。
  - **## 目录说明**：两行表格——`WORKFLOW.md`（workflow 契约：阶段定义 / 输入参数 / failure code / 归属规则，被 `.opencode/commands/le.md` 间接消费）、`run_lcview_adb_suite.sh`（bash 入口脚本）。
  - **## 使用方式**：
    - 入口：`bash engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh`（参数见 WORKFLOW.md）。
    - 典型参数：`--serial-host 127.0.0.1 --serial-port 9700 --serial-profile rp5/default.json --adb-profile rp5/adb.json --artifacts-dir engineering/output/runs/lcview-adb-run`。
  - **## 关联资源**：
    - 关联 workflow：`./WORKFLOW.md`（完整流程契约，**保留此关联**——D-WF 决策）
    - 设计文档：`docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md`（lcview adb 设计）

- [ ] **Step 3：验证 + Commit**

  人工核对：核心 5 节齐全；`./WORKFLOW.md` 关联保留；链接可达。
  
  ```bash
  git add engineering/loop/workflows/lcview-adb-run/README.md
  git commit -m "重构(docs): lcview-adb-run/README.md 按 B1 写齐核心 5 节，保留指向 WORKFLOW.md 的关联资源（D-WF）"
  ```

---

### Task 5.16：harness/workflows/{4 份}README.md 轻型校准（保留 WORKFLOW.md 关联）

**Files:**
- Modify: `engineering/harness/workflows/git-push-to-server/README.md`（3 行 → ~25 行）
- Modify: `engineering/harness/workflows/sync-code-to-patchs/README.md`（3 行 → ~25 行）
- Modify: `engineering/harness/workflows/revert-code-from-patchs/README.md`（3 行 → ~25 行）
- Modify: `engineering/harness/workflows/sync-patchs-to-doc/README.md`（3 行 → ~25 行）

> **同构合并**：四份结构同构（均为 3 行占位 + 指向 WORKFLOW.md），按 B1 写齐核心 5 节，保留各自 `./WORKFLOW.md` 关联（D-WF：4 份 WORKFLOW.md 保留，被 `.opencode/commands/*.md` `@` 消费）。四份差异点在每个 workflow 的语义、触发命令、脚本入口、消费的配置。
>
> **validator 覆盖**：`validate_harness_docs.sh` Step1 扫描 `harness/workflows/*/README.md` 链接；Step2 因目录含 WORKFLOW.md **跳过文件清单比对**（validator 第 119 行），故 *.sh 脚本无需在 README 登记（登记在 WORKFLOW.md）。本 Task 末尾必须运行 validator。

- [ ] **Step 1：重新通读 4 个 workflow 目录**

  执行（逐个）：
  - **git-push-to-server**：`ls` 确认 `README.md` + `WORKFLOW.md` + `collect_diff.sh` + `commit_and_push.sh`。读 `WORKFLOW.md`（183 行）前 30 行：语义 = 收集 diff → AI 生成中文 commit message → 单次确认 → 提交推送；脚本做机械工作，AI 做语义工作。触发：`/git-push-to-server` 或用户说"提交/推送"。
  - **sync-code-to-patchs**：`ls` 确认 `README.md` + `WORKFLOW.md` + `sync_code_to_patchs.sh`。读 `WORKFLOW.md`（170 行）前 30 行：语义 = workspace 源码改动全量镜像到 `patchs/rpi5/`，自动更新 manifest + README 文件映射表；patchs 是 workspace 的精确镜像。触发：`/sync-code-to-patchs`。
  - **revert-code-from-patchs**：`ls` 确认 `README.md` + `WORKFLOW.md` + `revert_code_from_patchs.sh`。读 `WORKFLOW.md`（190 行）前 30 行：语义 = patchs→workspace 回退（计划生成→逐条确认→执行→落盘校验）；patchs 是已知良好基线；是 sync 的逆操作；source-code-modify.md 的受控例外（灾难恢复）。触发：`/revert-code-from-patchs`。
  - **sync-patchs-to-doc**：`ls` 确认 `README.md` + `WORKFLOW.md` + `sync_patchs_to_doc.sh`。读 `WORKFLOW.md`（230 行）前 30 行：语义 = patchs 变动后生成报告，按模板规范将 diff 转文档更新（方案先行，确认后落盘）；templates 是只读契约。触发：`/sync-patchs-to-doc` 或归档后。
  - 关注：四份 README 的「关联配置」差异——git-push 消费 scope-mapping.yaml；sync-code/revert 消费 manifest + scope-mapping；sync-patchs-to-doc 消费 doc-sync-mapping.yaml + templates。

- [ ] **Step 2：逐份重写，核心 5 节（保留 ./WORKFLOW.md 关联）**

  每份结构相同，差异在内容：

  **git-push-to-server/README.md**：
  - 定位：是什么 = git 提交推送 workflow（收集 diff → AI 生成中文 commit message → 确认 → 提交推送 origin）；职责边界 = 脚本做机械工作，AI 做语义工作；上下游 = 消费 `scope-mapping.yaml` 判定 commit scope，写入 origin。
  - 目录说明：`WORKFLOW.md`（契约，被 `.opencode/commands/git-push-to-server.md` `@` 消费）/ `collect_diff.sh`（diff 收集）/ `commit_and_push.sh`（提交推送）。
  - 使用方式：触发 `/git-push-to-server`；入口 `collect_diff.sh` + `commit_and_push.sh`（由 workflow 编排，不单独调用）。
  - 关联资源：`./WORKFLOW.md`（D-WF 保留）/ 关联配置 `../../config/scope-mapping.yaml`。

  **sync-code-to-patchs/README.md**：
  - 定位：是什么 = workspace→patchs 全量镜像归档 workflow；职责边界 = patchs 是 workspace 精确镜像（含删除对齐）；上下游 = 读 `~/workspace/`，写 `patchs/rpi5/` + manifest + README 映射表。
  - 目录说明：`WORKFLOW.md`（被 `.opencode/commands/sync-code-to-patchs.md` `@` 消费）/ `sync_code_to_patchs.sh`。
  - 使用方式：触发 `/sync-code-to-patchs`。
  - 关联资源：`./WORKFLOW.md` / 关联配置 `../../config/scope-mapping.yaml` / 关联规则 `../../rules/source-code-modify.md`（workspace 是源头）/ 被写 `patchs/rpi5/README.md` 文件映射表。

  **revert-code-from-patchs/README.md**：
  - 定位：是什么 = patchs→workspace 回退 workflow（灾难恢复）；职责边界 = sync 的逆操作，patchs 是已知良好基线；上下游 = 读 `patchs/rpi5/` + manifest，写 `~/workspace/`。
  - 目录说明：`WORKFLOW.md`（被 `.opencode/commands/revert-code-from-patchs.md` `@` 消费）/ `revert_code_from_patchs.sh`。
  - 使用方式：触发 `/revert-code-from-patchs`。
  - 关联资源：`./WORKFLOW.md` / 关联规则 `../../rules/source-code-modify.md`（受控例外）/ 关联配置 `../../config/scope-mapping.yaml` / 读 `patchs/rpi5/README.md` 文件映射表。

  **sync-patchs-to-doc/README.md**：
  - 定位：是什么 = patchs→文档同步 workflow（生成报告 + 按模板转文档更新）；职责边界 = templates 是只读契约，设计文档是受控可变区；上下游 = 读 `patchs/rpi5/`，写 `docs/specs/`。
  - 目录说明：`WORKFLOW.md`（被 `.opencode/commands/sync-patchs-to-doc.md` `@` 消费）/ `sync_patchs_to_doc.sh`。
  - 使用方式：触发 `/sync-patchs-to-doc`（通常在 sync-code-to-patchs 之后）。
  - 关联资源：`./WORKFLOW.md` / 关联配置 `../../config/doc-sync-mapping.yaml` / 关联 workflow `../` templates 只读契约（`../../templates/*.md`）。

- [ ] **Step 3：验证（运行 validator）**

  Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
  Expected: 退出码 0。Step1 链接校验四份 README 的 `./WORKFLOW.md` 链接可达；Step2 跳过文件清单（含 WORKFLOW.md 目录）；Step4 front matter 校验四份 WORKFLOW.md 通过。

- [ ] **Step 4：Commit（四份合并一次提交）**

  ```bash
  git add engineering/harness/workflows/git-push-to-server/README.md \
           engineering/harness/workflows/sync-code-to-patchs/README.md \
           engineering/harness/workflows/revert-code-from-patchs/README.md \
           engineering/harness/workflows/sync-patchs-to-doc/README.md
  git commit -m "重构(docs): harness/workflows 4 份 README 按 B1 写齐核心 5 节，保留各 ./WORKFLOW.md 关联（D-WF）"
  ```

---

### Task 5.17：loop/connection/profiles/README.md + profiles/devices/rp5/README.md 轻型校准

**Files:**
- Modify: `engineering/loop/connection/profiles/README.md`（29 行 → ~40 行）
- Modify: `engineering/loop/connection/profiles/devices/rp5/README.md`（16 行 → ~30 行）

> **同域合并**：profiles/ 是设备语义配置承载层，devices/rp5/ 是具体设备 profile；两份连续校准。

- [ ] **Step 1：重新通读 profiles 目录树**

  执行：
  - `ls -R engineering/loop/connection/profiles/`：确认 `devices/rp5/{README.md, default.json, adb.json}`。
  - 读 `devices/rp5/default.json`（transport=serial，prompt_markers/boot_markers/reboot_markers/panic_markers/line_ending）、`devices/rp5/adb.json`（transport=adb，boot_markers=[sys.boot_completed=1]/default_capture_timeout/default_recent_limit）。
  - 读现有 `profiles/README.md`（29 行）：已有「范围」「配置优先级」「目录结构」三段，信息较全，需按 5 节重组。
  - 读现有 `devices/rp5/README.md`（16 行）：已有「设备」「当前 profile」「串口参数」，需按 5 节重组 + 补关联资源。
  - 关注：profiles 不承载 provider 运行配置（COM 口/baudrate/listen address 在 provider 自身），只承载设备语义（prompt/boot/panic marker/timeout）。

- [ ] **Step 2：重写 profiles/README.md，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = provider/device 配置语义承载层（描述「如何理解这台板子」）；职责边界 = 只承载设备语义（prompt marker / boot marker / panic marker / line ending / timeout / reboot loop 阈值 / rule 参数 / workflow override），**不承载** provider 运行配置（COM 口 / baudrate / listen address 由 provider 自身管理）；上下游依赖 = 被 `loop/connection/providers/*` 与 `loop/workflows` 消费。
  - **## 大纲**：5 节。
  - **## 目录说明**：一行表格——`devices/`（按设备组织 profile，当前仅 `rp5/`）。
  - **## 使用方式**：本目录无可执行入口，作为配置承载层。**配置优先级**（保留现有三层级）：provider 默认 → 设备 profile（如 rp5/default.json）→ workflow override（后者覆盖前者）。
  - **## 关联资源**：
    - 关联 workflow：`../../workflows/lcview-adb-run/`（消费 rp5/default.json + rp5/adb.json）
    - 设计文档：`docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md`（profile 设计）

- [ ] **Step 3：重写 devices/rp5/README.md，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = Raspberry Pi 5 设备 profile 集；职责边界 = 描述 rp5 的设备语义（prompt/boot/panic marker/串口参数），不含运行配置；上下游依赖 = 被 rp5-serial provider 与 adb provider 消费。
  - **## 大纲**：5 节。
  - **## 目录说明**：两行表格——`default.json`（transport=serial，用于 boot/bootstrap/fallback：prompt_markers/console 提示符、boot_markers、reboot_markers、panic_markers、line_ending）、`adb.json`（transport=adb，用于 feature suite 与 adb shell 验收：boot_markers=[sys.boot_completed=1]、default_capture_timeout=10s、default_recent_limit=400）。
  - **## 使用方式**：本目录无可执行入口，作为 profile 文件被 `le.sh --device-profile` / `run_lcview_adb_suite.sh --serial-profile/--adb-profile` 引用。串口参数：baudrate 115200，8N1。
  - **## 关联资源**：
    - 设计文档：`docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md`
    - 关联 workflow：`../../../workflows/lcview-adb-run/`（消费本目录 profile）

- [ ] **Step 4：验证 + Commit**

  人工核对两份 5 节齐全；profile 字段描述与 JSON 实际一致；链接可达。
  
  ```bash
  git add engineering/loop/connection/profiles/README.md \
          engineering/loop/connection/profiles/devices/rp5/README.md
  git commit -m "重构(docs): connection/profiles 两份 README 按核心 5 节校准，登记 default/adb profile 字段语义"
  ```

---

### Task 5.18：loop/connection/providers/adb/README.md 轻型校准

**Files:**
- Modify: `engineering/loop/connection/providers/adb/README.md`（23 行 → ~35 行）

- [ ] **Step 1：重新通读 adb provider 目录**

  执行：
  - `ls -R engineering/loop/connection/providers/adb/`：确认 `python/loop_adb/{__init__.py, client.py, transport.py}` + `python/tests/{__init__.py, test_client.py, test_transport.py}` + `README.md`。
  - 读 `__init__.py`（导出 AdbClient/AdbCommandError/AdbCommandResult/AdbShellResult/AdbTransport）、`client.py` 前 60 行（AdbClient 封装 adb CLI 子进程，AdbCommandResult 统一返回，AdbShellResult 解析 `__LE_EXIT_CODE__` 标记拿真实 exit code）、`transport.py` 前 30 行（AdbTransport 适配 BaseTransport）。
  - 读现有 README（23 行）：已有「范围」「Python 包」「测试」三段，需按 5 节重组。
  - 关注：adb provider 提供 `transport=adb` live transport，能力清单（connect/disconnect/shell/root-su0/pull/logcat/reboot-wait/runtime context）需在「定位」或「目录说明」体现。

- [ ] **Step 2：重写 README，核心 5 节**

  结构：
  - **顶部 AI 读取指引**
  - **## 定位**：是什么 = loop engineering 的 `transport=adb` live transport provider；职责边界 = 提供 adb connect/disconnect/shell（带 exit code 解析）/root-su0 提权/pull/logcat 多 buffer/reboot+wait-for-device/runtime context；上下游依赖 = 依赖 `loop/core`（BaseTransport 契约），被 `loop/workflows/lcview-adb-run` 消费。
  - **## 大纲**：5 节。
  - **## 目录说明**：两行表格——`python/loop_adb/client.py`（AdbClient：adb CLI 子进程封装，AdbCommandResult/AdbShellResult 统一返回结构）、`python/loop_adb/transport.py`（AdbTransport：BaseTransport 适配层）。附 `python/tests/`（test_client / test_transport）。
  - **## 使用方式**：
    - 本目录无可独立 CLI 入口，作为 transport 库被 workflow import。
    - **公开 API**：`AdbClient`（connect/shell/pull/logcat/reboot 等）、`AdbTransport`（实现 BaseTransport 契约供 loop core 调用）。
    - **测试**：`PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" python3 -m pytest engineering/loop/connection/providers/adb/python/tests/ -v`
  - **## 关联资源**：
    - 关联 workflow：`../../workflows/lcview-adb-run/`（adb feature suite 消费本 provider）
    - 设计文档：`docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md`（adb provider 设计）
    - 关联协议：`../../protocol/`（adb 暂无独立协议文档，遵循通用契约）

- [ ] **Step 3：验证 + Commit**

  人工核对 5 节齐全；API 名称与 `__init__.py` 导出一致；测试命令 PYTHONPATH 与现有第 21 行一致；链接可达。
  
  ```bash
  git add engineering/loop/connection/providers/adb/README.md
  git commit -m "重构(docs): providers/adb/README.md 按核心 5 节校准，登记 AdbClient/AdbTransport API 与能力清单"
  ```

---
