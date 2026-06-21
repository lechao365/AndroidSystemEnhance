## 阶段 3：CONTROL-CHARTER 融入 harness/README.md

> **前置依赖**：阶段 1（engineering-readme-template.md 已建立）、阶段 2（build-reference 已迁移至 reference/，harness/README 快速导航的编译路径已更新为 `reference/build-reference.md`）
>
> **章节锚点约定**：融入后的「控制总纲」章节标题为 `## 控制总纲`，GitHub 锚点为 `#控制总纲`。所有跨文件引用统一使用 `../README.md#控制总纲`（config/README、rules/README）或 `./README.md#控制总纲`（同目录文件）。

---

### Task 3.1：重写 harness/README.md（融入 CONTROL-CHARTER 为「控制总纲」章节）

**Files:**
- Modify: `engineering/harness/README.md`（全文重写）

- [ ] **Step 1：重新阅读目录代码与文档，建立重构基线**

逐项读取以下文件，理解当前内容与目录结构，识别需要保留 / 补齐 / 删除的信息：
- `engineering/harness/CONTROL-CHARTER.md`（全文 148 行——要融入的源）
- `engineering/harness/README.md`（全文 87 行——重写目标，当前内容）
- `engineering/harness/templates/engineering-readme-template.md`（阶段 1 产出——核心 5 节骨架 + 扩展块规范）
- `engineering/harness/rules/README.md`、`engineering/harness/config/README.md`、`engineering/harness/lib/README.md`、`engineering/harness/scripts/README.md`、`engineering/harness/templates/README.md`、`engineering/harness/workflows/README.md`（了解各子目录 README 当前登记了什么，确保目录说明表不遗漏）
- `engineering/harness/reference/README.md`（阶段 1 新建——确认 reference/ 已登记）

> **要求**：执行者必须重新阅读上述文件的实际内容，结合 CONTROL-CHARTER 新材料重构 README——补齐缺失信息（如 reference/ 目录登记）、删除失效引用（所有 `./CONTROL-CHARTER.md` 链接改为内部锚点 `#控制总纲`）、而非纯格式调整。

- [ ] **Step 2：按 engineering-readme-template 核心 5 节 + 扩展块重写 README**

重写后的 README 结构如下（章节顺序严格按此排列）：

```
# Engineering Harness

> AI 读取指引（三层结构提示，照搬模板头部）

## 定位
（一句话 + 职责边界 + 上下游依赖。保留现有 README 第 3-4 行的核心描述：
 harness 是工程控制面与执行保障面；只承载公共 harness 能力，不承载 loop-specific 内容。

## 大纲
（强制列出全部章节的表格，三列：章节 / 内容摘要 / 何时读取。
 含核心 5 节 + 快速导航 + 控制总纲 + lib 公共能力速查 + README 同步约定。

## 目录说明
（保留现有 README 第 36-45 行的目录说明表。
 确认新增 reference/ 目录已登记——如阶段 2 已加则核对，未加则补行：
 | [reference/](./reference/) | 参考文档承载层（build-reference 等） | ... |

## 快速导航                    ← 扩展块（保留现有模式）
（保留现有 README 第 8-27 行的"我要做的事 → 先读哪里"表。
 关键改动：
 - 第 11 行 [CONTROL-CHARTER.md](./CONTROL-CHARTER.md) → [README.md#控制总纲](#控制总纲)
 - 确认"编译 RPI5"行已指向 reference/build-reference.md（阶段 2 应已完成）

## 使用方式
### 快速开始
（harness 无单一入口，写：
 "harness 无统一可执行入口，各子目录有独立入口。
  常见入口：validate_harness_docs.sh（文档校验）、workflows/*/bin/*.sh（工作流脚本）。
  详细入口清单见各子目录 README。")
### 入口清单
（列出 validate_harness_docs.sh 一行即可，其余指向子目录 README）

## 关联资源
（固定枚举表：设计文档 / 规则 / workflow / 配置。
 示例：
 | 设计文档 | docs/specs/2026-06-21-engineering-doc-refactor-design.md | 本轮文档重构设计 |
 | 关联规则 | rules/source-code-modify.md (SRC-001~004) | 改 workspace 源码前加载 |
 | 关联配置 | config/scope-mapping.yaml | commit scope 判定 |
 | 关联配置 | config/harness-paths.conf | 工程路径单一事实源 |

## 控制总纲                    ← 扩展块（NEW，原 CONTROL-CHARTER.md 全文融入）
（见下方 Step 3 的详细章节映射）

## lib 公共能力速查             ← 扩展块（保留现有 README 第 53-74 行内容）
（原样保留 bootstrap 加载示例、API 清单、API 边界说明。

## README 同步约定              ← 扩展块（保留现有 README 第 83-87 行内容）
（原样保留文件变更 → README 更新清单。）
```

- [ ] **Step 3：将 CONTROL-CHARTER 全文融入「控制总纲」章节**

在 `## 控制总纲` 章节下，按以下映射逐节融入 CONTROL-CHARTER 的内容。每节保留原文实质内容，仅调整标题层级（CONTROL-CHARTER 原一级 `##` 降为 `###`，原二级 `###` 降为 `####`）：

| CONTROL-CHARTER 章节 | 融入后小节标题 | 融入要点 |
|---------------------|---------------|---------|
| 引子段（第 3 行） | `## 控制总纲` 章节引言 | 保留一句话定位，作为控制总纲章节的开篇 |
| §1 目标边界（第 7-21 行） | `### 目标边界` | 保留"Harness 负责 / 不负责"两个清单的完整内容 |
| §2 对象模型（第 25-66 行） | `### 对象模型` | 保留 2.1 核心对象清单、2.2 文档分层、2.3 状态模型与证据要求表。标题降级为 `#### 核心对象` / `#### 文档分层` / `#### 状态模型与证据要求` |
| §3 真相源矩阵（第 70-78 行） | `### 真相源矩阵` | 保留 5 行表格完整内容 |
| §4 职责边界（第 82-107 行） | `### 职责边界` | 保留 Human / AI / Script 三角色清单 |
| §5 规则优先级（第 110-120 行） | `### 规则优先级` | 保留 5 级优先级链。**注意**：第 2 级原文"本总纲（Control Charter）"改为"本控制总纲（`#控制总纲`）"，使其自指锚点 |
| §6 受控例外（第 124-132 行） | `### 受控例外` | 保留 5 条受控例外场景 |
| §7 术语表（第 136-148 行） | `### 术语表` | 保留 9 行术语定义表 |

> **融入纪律**：不删减 CONTROL-CHARTER 的实质内容（定义、表格、清单）；仅做标题层级调整与自指链接替换。CONTROL-CHARTER 原文不含 PlantUML 块，无需考虑 PlantUML 合法性。

- [ ] **Step 4：更新 README 中所有 CONTROL-CHARTER 链接为内部锚点**

全文搜索重写后的 README，将以下链接替换：
- `[CONTROL-CHARTER.md](./CONTROL-CHARTER.md)` → `[#控制总纲](#控制总纲)`（快速导航表）
- 第 31 行控制入口段的 `[CONTROL-CHARTER.md](./CONTROL-CHARTER.md)` → 删除该独立行（内容已融入控制总纲章节，快速导航已指向锚点）
- 第 78 行优先级行的 `[CONTROL-CHARTER.md](./CONTROL-CHARTER.md)` → `[#控制总纲](#控制总纲)`

- [ ] **Step 5：验证**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：PASS（warns=0）。此时 CONTROL-CHARTER.md 仍存在于磁盘（Task 3.3 才删除），但 README 已不再链接它，validator Step1 不会报告链接缺失。

- [ ] **Step 6：Commit**

```bash
git add engineering/harness/README.md
git commit -m "重构(docs): harness/README 融入 CONTROL-CHARTER 为「控制总纲」章节"
```

---

### Task 3.2：更新 rules/README.md、config/README.md 与 config YAML 的 CONTROL-CHARTER 引用

**Files:**
- Modify: `engineering/harness/rules/README.md:26`
- Modify: `engineering/harness/config/README.md:17,55,57`
- Modify: `engineering/harness/config/baseline-status.yaml:3`
- Modify: `engineering/harness/config/baseline-evidence-template.yaml:3`

- [ ] **Step 1：更新 rules/README.md 优先级链引用**

`engineering/harness/rules/README.md` 第 26 行当前内容：

```
> 规则优先级遵循 [CONTROL-CHARTER.md](../CONTROL-CHARTER.md)：用户指令 > Control Charter > `rules/*.md` > `workflows/*/WORKFLOW.md` > README。
```

改为：

```
> 规则优先级遵循 [harness/README 控制总纲](../README.md#控制总纲)：用户指令 > 控制总纲 > `rules/*.md` > `workflows/*/WORKFLOW.md` > README。
```

> **注意**：阶段 2 可能已将此行从 `CONTROL-CHARTER.md` 改为其他形式——如果阶段 2 已改过，确认链接指向 `../README.md#控制总纲` 即可；如果仍引用 `../CONTROL-CHARTER.md`，按上方替换。

- [ ] **Step 2：更新 config/README.md 的 3 处引用**

`engineering/harness/config/README.md`：

**第 17 行**（baseline-status.yaml 的"被谁引用"列）：
```
| [baseline-status.yaml](./baseline-status.yaml) | ... | `CONTROL-CHARTER.md`、`source-code-modify.md` |
```
将 `CONTROL-CHARTER.md` → `../README.md#控制总纲`：
```
| [baseline-status.yaml](./baseline-status.yaml) | ... | [../README.md#控制总纲](../README.md#控制总纲)、`source-code-modify.md` |
```

**第 55 行**（任务准入矩阵"必读规则"列）：
```
| harness 规则文档改造 | 是 | `CONTROL-CHARTER.md` + 对应 `rules/*.md` | ...
```
将 `CONTROL-CHARTER.md` → `../README.md#控制总纲`：
```
| harness 规则文档改造 | 是 | `../README.md#控制总纲` + 对应 `rules/*.md` | ...
```

**第 57 行**（任务准入矩阵"必读规则"列）：
```
| harness 配置映射改造 | 是 | `CONTROL-CHARTER.md` + `config/README.md` | ...
```
将 `CONTROL-CHARTER.md` → `../README.md#控制总纲`：
```
| harness 配置映射改造 | 是 | `../README.md#控制总纲` + `config/README.md` | ...
```

- [ ] **Step 3：更新 config YAML 注释中的路径引用**

`engineering/harness/config/baseline-status.yaml` 第 3 行注释：
```yaml
# 状态语义见 CONTROL-CHARTER.md § 2.3，证据字段定义见 baseline-evidence-template.yaml
```
改为：
```yaml
# 状态语义见 ../README.md#控制总纲（状态模型与证据要求），证据字段定义见 baseline-evidence-template.yaml
```

`engineering/harness/config/baseline-evidence-template.yaml` 第 3 行注释：
```yaml
# 状态登记见 baseline-status.yaml，状态语义见 CONTROL-CHARTER.md § 2.3
```
改为：
```yaml
# 状态登记见 baseline-status.yaml，状态语义见 ../README.md#控制总纲（状态模型与证据要求）
```

- [ ] **Step 4：验证**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：PASS（warns=0）。此时所有跨文件引用已改为 `../README.md#控制总纲` 锚点链接，validator Step1 会校验 `../README.md` 文件存在（通过），锚点本身不校验存在性（通过）。

- [ ] **Step 5：Commit**

```bash
git add engineering/harness/rules/README.md engineering/harness/config/README.md \
        engineering/harness/config/baseline-status.yaml \
        engineering/harness/config/baseline-evidence-template.yaml
git commit -m "重构(docs): rules/config 引用 CONTROL-CHARTER 改为 harness/README 控制总纲锚点"
```

---

### Task 3.3：删除 CONTROL-CHARTER.md + 更新 validator Step3 注释

**Files:**
- Delete: `engineering/harness/CONTROL-CHARTER.md`
- Modify: `engineering/harness/scripts/validate_harness_docs.sh:221`

- [ ] **Step 1：确认无残留引用**

```bash
grep -rn "CONTROL-CHARTER" engineering/ --include="*.md" --include="*.yaml" --include="*.sh"
```

预期输出：仅 `engineering/harness/scripts/validate_harness_docs.sh:221` 的注释行 + `engineering/output/log/` 下的历史日志（日志不归档，忽略）。如果还有 `.md` / `.yaml` / `.sh` 残留引用，返回 Task 3.2 补齐后再继续。

- [ ] **Step 2：git rm CONTROL-CHARTER.md**

```bash
git rm engineering/harness/CONTROL-CHARTER.md
```

- [ ] **Step 3：更新 validator Step3 注释**

`engineering/harness/scripts/validate_harness_docs.sh` 第 221 行注释当前：

```bash
# 扫描 templates/*.md 与 harness 下所有 README.md、CONTROL-CHARTER.md
```

改为（移除 CONTROL-CHARTER 特例提及，说明扫描范围已由 `$HARNESS_DIR` 全覆盖）：

```bash
# 扫描 templates/*.md 与 harness 下所有 .md 文件（含 README.md、reference/ 等）
```

> **说明**：`find "$HARNESS_DIR/templates" "$HARNESS_DIR" -name '*.md'` 会递归扫描 `$HARNESS_DIR` 下全部 .md 文件，CONTROL-CHARTER.md 删除后自然不再出现，无需调整 find 逻辑。注释更新仅为移除已失效的文件名提及。

- [ ] **Step 4：验证**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：PASS（warns=0）。CONTROL-CHARTER.md 已删除，无文件引用它，validator 不报告缺失。

- [ ] **Step 5：Commit**

```bash
git add engineering/harness/scripts/validate_harness_docs.sh
git commit -m "杂项(docs): 删除 CONTROL-CHARTER.md（已融入 harness/README），更新 validator 注释"
```

---

## 阶段 4：rp5-serial 融合 + 协议重命名

> **前置依赖**：阶段 1（engineering-readme-template.md 已建立，rp5-serial/README 需按核心 5 节 + 扩展块组织）
>
> **命名规则**：协议文件 `rp5_serial_protocol.md` → `rp5-serial-protocol.md`（N1 全小写连字符）

---

### Task 4.1：重写 rp5-serial/README.md（融入 WORKFLOW 为「运行流程」扩展块）

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/README.md`（全文重写）

- [ ] **Step 1：重新阅读目录代码与文档，建立重构基线**

逐项读取以下文件，理解 provider 实际实现与运行方式：
- `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md`（全文 86 行——要融入的源）
- `engineering/loop/connection/providers/rp5-serial/README.md`（全文 118 行——重写目标）
- `engineering/loop/connection/providers/rp5-serial/bin/`（列出全部 bash 入口脚本，确认实际命令名与参数）
- `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/`（浏览 host/ client/ shared/ 目录结构，确认模块清单与 README 目录结构树一致）
- `engineering/harness/templates/engineering-readme-template.md`（阶段 1 产出——核心 5 节骨架 + 扩展块规范）
- `engineering/loop/scripts/start_rp5_serial_host.bat`（README 引用的一键启动脚本，确认路径与参数正确）

> **要求**：执行者必须重新阅读上述代码与文档，结合 WORKFLOW.md 新材料重构 README——补齐缺失（如运行流程章节）、删除失效（"详见 WORKFLOW.md"指针、与 WORKFLOW 重复的内容只保留一处）、更新协议文件名引用（`rp5_serial_protocol.md` → `rp5-serial-protocol.md`），而非纯格式调整。

- [ ] **Step 2：按 engineering-readme-template 核心 5 节 + 扩展块重写 README**

重写后的 README 结构如下（章节顺序严格按此排列）：

```
# rp5-serial Provider

> AI 读取指引（三层结构提示，照搬模板头部）

## 定位
（保留现有 README 第 8-15 行的"目标"核心内容，提炼为定位段：
 - 是什么：Windows Host 独占物理串口 + WSL2 Client 三模式接入的 rp5-serial provider
 - 职责边界：串口托管 + 数据转发 + session/lease 管理；不负责故障判定 / panic 识别
 - 上下游依赖：依赖 harness observability（bash 入口）；被 loop/workflows 消费

## 大纲
（强制列出全部章节表，三列：章节 / 内容摘要 / 何时读取。
 含核心 5 节 + 运行流程扩展块。

## 目录说明
（保留现有 README 第 29-56 行的目录结构树。
 关键改动：
 - 删除 ├── WORKFLOW.md 行（文件将被删除）
 - 关联协议行更新：rp5_serial_protocol.md → rp5-serial-protocol.md

## 使用方式
### 快速开始
（保留现有 README 第 60-105 行的 Host 启动 + Client 使用内容。
 注意：Host 启动的参数说明、Client 四个 bash 入口的命令示例——这些是
 高频使用的速查内容，保留在"使用方式"而非迁入"运行流程"。
 "关联协议"引用更新：rp5_serial_protocol.md → rp5-serial-protocol.md
）
### 入口清单
（从 bin/ 目录提取，列表：
 | loop_rp5_serial_status.sh | 状态查询 | bash .../loop_rp5_serial_status.sh --host <ip> --port 9700 |
 | loop_rp5_serial_monitor.sh | 只读监控 | bash .../loop_rp5_serial_monitor.sh ... |
 | loop_rp5_serial_interactive.sh | 交互终端 | bash .../loop_rp5_serial_interactive.sh ... |
 | loop_rp5_serial_automation.sh | 自动化发送 | bash .../loop_rp5_serial_automation.sh --send "..." |
）

## 关联资源
（固定枚举表：
 | 设计文档 | docs/specs/2026-06-19-loop-engineering-design.md | loop engineering 设计 |
 | 关联计划 | docs/plans/2026-06-19-rp5-serial-host-client-mvp.md | MVP 实施计划 |
 | 关联协议 | ../protocol/rp5-serial-protocol.md | host/client 协议定义 |
）

## 运行流程                    ← 扩展块（NEW，原 WORKFLOW.md 内容融入）
（见下方 Step 3 的详细章节映射）

## MVP 限制                    ← 扩展块（保留现有 README 第 107-114 行 + WORKFLOW 第 72-78 行合并去重）
```

- [ ] **Step 3：将 WORKFLOW.md 内容融入「运行流程」章节**

在 `## 运行流程` 章节下，按以下映射逐节融入 WORKFLOW.md 内容。WORKFLOW.md 的 front matter（`name` / `description`）不融入（它是工具消费字段，而此 WORKFLOW.md 不被 `.opencode/commands/*.md` 消费）：

| WORKFLOW.md 章节 | 融入后小节标题 | 融入要点与去重指引 |
|-----------------|---------------|-------------------|
| §拓扑（第 8-21 行） | `### 拓扑` | 原样保留 ASCII 拓扑图（RPi5 UART → Windows COM → host → client 三模式） |
| §Windows Host 启动方式（第 23-42 行） | `### Host 启动与职责` | **去重**：README"使用方式 > 快速开始"已有 Host 启动命令与参数说明，此处不重复命令；保留 WORKFLOW 独有的"后续补充（NSSM/WinSW）"与"Host 启动后职责（5 条清单）"。标题降级为 `### Host 启动与职责` |
| §WSL2 Client 三模式（第 44-63 行） | `### Client 三模式详解` | **去重**：README"定位"已有一句话概述三模式；此处保留 WORKFLOW 独有的详细说明（每模式的 writer lease 行为、用途）。monitor/interactive/automation 各为一个 `####` 小节 |
| §单 writer 约束（第 64-69 行） | `### 单 writer 约束` | 原样保留 4 条规则（读共享写独占、WRITER_BUSY、无排队、release 后可立即申请）。README"定位"中"单 writer，无排队"一句话保留，详解放此处 |
| §MVP 限制（第 72-78 行） | 合并到 README 的 `## MVP 限制` 扩展块 | **去重**：README 现有 MVP 限制（第 107-114 行）与 WORKFLOW MVP 限制（第 72-78 行）有重叠。合并策略：以 WORKFLOW 版本为基准（更详细，含 `expect.wait` 与 transcript 落盘路径），补入 README 独有的"不含激进恢复动作（L3/L4）"。合并后放在 `## MVP 限制` 扩展块，不在运行流程章节内重复 |
| §bash 入口与日志（第 80-86 行） | `### bash 入口与日志规范` | 原样保留入口命名规范、日志 script-name 命名、落点路径 |

- [ ] **Step 4：移除"详见 WORKFLOW.md"指针与目录树中的 WORKFLOW 行**

- 删除 README 第 116-118 行的 `## 参考实现` 章节（内容为 `详见 WORKFLOW.md`。融入后不再需要此指针）
- 目录结构树（第 29-56 行）中删除 `├── WORKFLOW.md        provider 工作流与运行方式` 行

- [ ] **Step 5：验证**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：PASS（warns=0）。此时 WORKFLOW.md 仍在磁盘（Task 4.3 才删除），但 README 已不再链接它。协议文件仍是旧名（Task 4.2 才重命名），README 中已用新名 `rp5-serial-protocol.md`——validator Step1 校验相对路径存在性时会报告该文件不存在（1 条告警）。

> **处理策略**：此告警在 Task 4.2 完成 `git mv` 后自动消失。如果执行者希望此步零告警，可在本步先暂保留旧名 `rp5_serial_protocol.md`，Task 4.2 重命名时再全局替换。推荐先写新名，接受 1 条已知告警，在 Task 4.2 commit 信息中注明。

- [ ] **Step 6：Commit**

```bash
git add engineering/loop/connection/providers/rp5-serial/README.md
git commit -m "重构(docs): rp5-serial/README 融入 WORKFLOW 为「运行流程」扩展块"
```

---

### Task 4.2：重命名协议文件 + 删除 WORKFLOW.md + 更新引用

**Files:**
- Rename: `engineering/loop/connection/protocol/rp5_serial_protocol.md` → `rp5-serial-protocol.md`
- Delete: `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md`
- Modify: `engineering/loop/connection/protocol/README.md:20`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py:9`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py:9`

- [ ] **Step 1：git mv 重命名协议文件**

```bash
git mv engineering/loop/connection/protocol/rp5_serial_protocol.md \
       engineering/loop/connection/protocol/rp5-serial-protocol.md
```

- [ ] **Step 2：git rm 删除 WORKFLOW.md**

```bash
git rm engineering/loop/connection/providers/rp5-serial/WORKFLOW.md
```

- [ ] **Step 3：更新 protocol/README.md 链接**

`engineering/loop/connection/protocol/README.md` 第 20 行：

```
| [rp5_serial_protocol.md](./rp5_serial_protocol.md) | rp5-serial host/client 协议定义 |
```

改为：

```
| [rp5-serial-protocol.md](./rp5-serial-protocol.md) | rp5-serial host/client 协议定义 |
```

- [ ] **Step 4：更新 handler.py 与 server.py 代码注释中的协议路径**

`engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py` 第 9 行：

```python
协议请求详见 ``engineering/loop/connection/protocol/rp5_serial_protocol.md``。
```
改为：
```python
协议请求详见 ``engineering/loop/connection/protocol/rp5-serial-protocol.md``。
```

`engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py` 第 9 行：

```python
协议请求详见 ``engineering/loop/connection/protocol/rp5_serial_protocol.md``。
```
改为：
```python
协议请求详见 ``engineering/loop/connection/protocol/rp5-serial-protocol.md``。
```

- [ ] **Step 5：确认无残留引用**

```bash
grep -rn "rp5_serial_protocol\|rp5-serial/WORKFLOW" engineering/ --include="*.md" --include="*.py" --include="*.sh"
```

预期输出：无匹配（或仅 `engineering/output/log/` 下历史日志，忽略）。如果有 `.md` / `.py` / `.sh` 残留引用，补齐后再继续。

- [ ] **Step 6：验证**

```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

预期：PASS（warns=0）。协议文件已重命名，所有引用已更新；WORKFLOW.md 已删除，README 不再引用它。

- [ ] **Step 7：Commit**

```bash
git add engineering/loop/connection/protocol/rp5-serial-protocol.md \
        engineering/loop/connection/protocol/README.md \
        engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py \
        engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py
git commit -m "杂项(docs): 重命名 rp5_serial_protocol → rp5-serial-protocol，删除 rp5-serial/WORKFLOW.md（已融入 README）"
```

---
