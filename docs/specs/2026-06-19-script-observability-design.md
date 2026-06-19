# harness 脚本维测系统设计

> **日期**：2026-06-19
> **状态**：已确认，待实施
> **范围**：为 `engineering/` 下所有 bash 脚本建立统一的维测（observability）体系——文件日志、结构化 step、错误现场捕获、统一退出码、中间产物归档；新增公共库与规则文件，并改造现有 6 个脚本

---

## 1. 背景与动机

### 1.1 现状

`engineering/` 下现有 6 个 bash 脚本（5 个 workflow + 1 个 build script）：

| 脚本 | 路径 | 行数 | 作用 |
|---|---|---|---|
| `collect_diff.sh` | `engineering/harness/workflows/git-push-to-server/` | 175 | 收集 git 改动，供 AI 生成 commit message |
| `commit_and_push.sh` | `engineering/harness/workflows/git-push-to-server/` | 142 | git add + commit + push |
| `revert_code_from_patchs.sh` | `engineering/harness/workflows/revert-code-from-patchs/` | 688 | patchs → workspace 回退（会改 workspace） |
| `sync_code_to_patchs.sh` | `engineering/harness/workflows/sync-code-to-patchs/` | 463 | workspace → patchs 同步归档（会改 patchs） |
| `sync_patchs_to_doc.sh` | `engineering/harness/workflows/sync-patchs-to-doc/` | 246 | patchs 变动报告生成 |
| `mk_rpi5_full_image.sh` | `engineering/harness/scripts/` | 500 | 树莓派5 AOSP 一键编译打包 |

这些脚本承担较复杂的工程流程，但**维测手段薄弱**：

- **无文件日志**：全部只输出到 stdout（彩色 echo），无持久化日志，运行结束后无法回溯。
- **无时间戳**：现有 `log_info/log_warn/log_error/log_step` 是彩色 echo，无时间戳、无 step 编号、无耗时。
- **错误现场丢失**：无 ERR trap，未开启 `set -e`（mk_rpi5 除外），命令失败常被静默吞掉，事后无法定位是哪一行、哪个命令、什么退出码。
- **中间产物散落 `/tmp/`**：revert 的 plan/verify、sync 的 repolist/manifest 扔在 `/tmp/`，系统重启即失，无法追溯。
- **退出码语义不统一**：如 `sync_code_to_patchs.sh` 有 MISS 仍退出 0，`collect_diff.sh` "无改动"用 exit 1（易被误判为失败）。
- **代码重复**：`log_*` 函数、`REPO_ROOT` 锚点查找在 6 个脚本中逐字重复。

### 1.2 问题

脚本一旦运行不满足预期，**难以快速定位原因**：
- 看不到当时的完整执行轨迹（stdout 滚屏即失）。
- 不知道在哪个 step、哪一行失败。
- 关键中间产物（plan、verify、repolist）已被系统清理。
- 退出码语义混乱，AI workflow 编排和人工判断都容易被误导。

### 1.3 目标

建立一套**脚本维测（observability）系统**，使复杂脚本从"不可控的运行结果"变为"可完全回溯、快速定位"：

1. **文件日志**：每次运行产生独立日志文件（结构化键值，带时间戳），按脚本隔离，轮转保留 3 份。
2. **结构化 step**：每个阶段有编号、耗时、状态，错误自动关联到所属 step。
3. **错误现场捕获**：失败时记录行号、命令原文、退出码、调用栈、step 上下文。
4. **统一退出码**：0/1/2/3/4 语义统一，便于编排与判断。
5. **中间产物归档**：plan/verify/repolist/manifest/build-report 收归到日志目录，随轮转保留。
6. **规则约束**：作为 `engineering/harness/rules/` 下的强约束规则，约束现有及未来所有脚本。

---

## 2. 总体架构

### 2.1 新增/修改文件清单

| 类型 | 路径 | 作用 |
|---|---|---|
| 新增 | `engineering/harness/lib/harness_observability.sh` | 公共维测库，所有脚本 source 它 |
| 新增 | `engineering/harness/rules/script-observability.md` | 维测规则（强约束） |
| 新增 | `engineering/harness/log/<script-name>/` | 每脚本独立日志目录（运行时按需创建） |
| 新增 | `engineering/harness/log/<script-name>/artifacts/` | 中间产物目录 |
| 新增 | `engineering/harness/log/.gitkeep` | 占位文件 |
| 修改 | `.gitignore` | 追加 `engineering/harness/log/` 忽略（保留 `.gitkeep`） |
| 修改 | `AGENTS.md` | 增加维测规则引用段落 |
| 修改 | 6 个现有脚本 | 接入维测库，统一日志/step/on_err/退出码/artifact |

### 2.2 规则与库的适用范围

- **物理位置**：规则文件和库都在 `engineering/harness/` 下（与现有 4 个 rules 同列，避免 rules 目录分裂）。
- **适用范围（规则文件内声明）**：`engineering/` 下**所有** bash 脚本，含 `harness/workflows/`、`harness/scripts/`、未来的 `loop/` 等。
- **加载机制**：`AGENTS.md` 增加一条约束——"改动 `engineering/` 下任何 bash 脚本前，必须先加载 `engineering/harness/rules/script-observability.md`"。

### 2.3 数据流总览

```
脚本启动
  └─ source harness_observability.sh（锚点查找 REPO_ROOT）
       └─ harness_init "script-name"
            ├─ 创建 harness/log/<script-name>/（不存在则建）
            ├─ 创建本次日志文件 <script-name>-<ts>.log
            ├─ 清理历史日志：保留最新 2 份 + 本次 = 3 份
            ├─ 创建/更新 artifacts/ 目录
            └─ 注册 EXIT trap（收尾：打印汇总、复制 latest.log）
  └─ 运行阶段
       ├─ log_info/log_warn/log_error → stdout（彩色精简） + 日志文件（结构化键值）
       ├─ step_begin/step_end → 自动编号 + 耗时 + step 内错误关联
       ├─ cmd || on_err ... → 错误现场捕获
       └─ 中间产物 → artifact_register → harness/log/<script>/artifacts/<ts>-<name>
  └─ harness_exit [code]
       ├─ 打印运行汇总（总 step 数、失败数、耗时、日志路径）
       ├─ flush 日志
       ├─ 复制本次日志到 latest.log（覆盖）
       └─ 退出（统一退出码：0/1/2/3/4）
```

---

## 3. 公共库 `harness_observability.sh` API 规格

### 3.1 核心 API

#### 初始化与收尾

```bash
harness_init [--with-errexit] "<script-name>"
```
- 创建日志目录、本次日志文件、轮转（保留历史 2 + 本次 1 = 3 份）。
- 创建 `artifacts/` 目录。
- 注册 EXIT trap（收尾：打印汇总、复制 `latest.log`、清理）。
- `--with-errexit`：为 `set -e` 脚本额外注册 `trap ERR`（自动触发 `on_err` 打印现场，退出由 `-e` 完成）。适用于高密度命令构建脚本（如 mk_rpi5）。
- 必须在脚本头部、所有业务逻辑前调用。

```bash
harness_exit [exit_code]
```
- 打印运行汇总、flush 日志、复制本次日志到 `latest.log`（覆盖）、退出。
- `exit_code` 省略时用上一个命令的退出码。

#### 日志输出（双格式）

```bash
log_info  "<message>"   # stdout: 绿色 [INFO]  message
log_warn  "<message>"   # stdout: 黄色 [WARN]  message
log_error "<message>"   # stderr: 红色 [ERROR] message
```
- **stdout**：彩色精简版（保留现有风格，人读优先）。
- **日志文件**：结构化键值（见 3.2）。
- **双写机制**：日志函数内部同时生成两份文本，不依赖 `tee`。
- `log_error` 走 **stderr**（修正现状：当前所有脚本 `log_error` 误走 stdout，不利于 `2>error.log` 分流）。

#### 结构化 step

```bash
step_begin "<step-title>"   # 开始一个 step，自动递增编号，记录开始时间
step_end   [exit_code]      # 结束当前 step，打印耗时；exit_code 非 0 时记为失败
```
- 全局递增 step 编号（`step=2/?`，总数动态计数，`harness_exit` 时补全）。
- `step_end 0` → 打印 `took 12.3s`；`step_end 1` → 打印 `FAILED (exit=1) took 12.3s`。
- step 内的错误自动关联到当前 step（`on_err` 打印 `in step N "title"`）。

示例：
```bash
step_begin "扫描 workspace"
... 业务逻辑 ...
step_end $?
```

#### 错误现场捕获

`on_err` 支持两种模式：

**模式 A（默认，无 `set -e`）**：手动 `|| on_err`
```bash
on_err [--continue] [--exit-code N] <LINENO> "<BASH_COMMAND>" <exit_code>
```
- 打印完整现场到日志（和 stderr）：
  - 失败行号、命令原文、退出码
  - 调用栈（`FUNCNAME` + `BASH_LINENO`）
  - 当前 step 上下文（step 编号 + 标题）
  - 当前脚本名
- **默认行为：打印现场后 `exit 1`**（fail-fast，防止半修改状态）。
- `--continue`：打印现场后不退出（用于可恢复错误，如 `find` 遍历某子目录失败）。
- `--exit-code N`：自定义退出码（如 push 失败用 2）。

调用形态：
```bash
# 默认：打印现场后 exit 1
git checkout "$BASE" -- "$rel" || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?

# 显式继续（可恢复错误）
find "$dir" -name "*.o" -delete || on_err --continue "${BASH_LINENO[0]}" "$BASH_COMMAND" $?

# 自定义退出码
git push || on_err --exit-code 2 "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
```

**设计理由（默认退出）**：
1. **fail-fast 原则**：revert/sync 脚本会修改 workspace，失败后继续会留下半修改状态，比直接停掉更难恢复。
2. **降低调用点心智负担**：绝大多数错误处理是"打印现场 → 退出"，让 `cmd || on_err ...` 一行搞定，避免 `cmd || { on_err ...; exit 1; }` 样板膨胀。
3. **防静默吞错**：一旦有人漏写 `exit`，又回到当前静默吞错的状态——与维测规则初衷相悖。
4. **`--continue` 显式声明**：把"继续"变成需要主动声明的特例，而非默认（安全默认 + 显式解锁）。

**模式 B（`set -e` + `trap ERR`）**：自动触发
```bash
set -e
harness_init --with-errexit "mk_rpi5_full_image"
```
- `harness_init --with-errexit` 注册 `trap 'on_err_trapped' ERR`。
- 任一命令失败时，`on_err_trapped` 自动打印现场（行号、命令、退出码、调用栈、step 上下文），**不退出**（退出由 `set -e` 完成）。
- 适用于高密度命令脚本（如 mk_rpi5，500 行密集命令），避免逐命令写 `|| on_err` 的巨大改造量。
- **注意**：模式 B 下若 step 内命令失败，`set -e` 会立即退出，`step_end` 可能来不及调用。此时该 step 的失败由 `trap ERR` → `on_err_trapped` 记录现场（含 step 上下文），`harness_exit` 由 EXIT trap 兜底执行（打印汇总、复制 latest.log）。step 的"未正常结束"状态会在汇总中体现为 `INCOMPLETE`。

#### 中间产物注册

```bash
artifact_register "<source-path>" "<artifact-name>"
```
- 把中间产物复制到 `harness/log/<script>/artifacts/<ts>-<artifact-name>`。
- 在日志里记录路径。
- 随轮转清理（保留最新 2 轮 + 本轮 = 3 轮）。

示例：
```bash
artifact_register "/tmp/revert-plan-xxx.tsv" "plan.tsv"
# → harness/log/revert_code_from_patchs/artifacts/20260619-153012-plan.tsv
```

#### 工具函数

```bash
harness_log_file        # 返回本次日志文件路径
harness_artifacts_dir   # 返回本次 artifacts 目录路径
```

### 3.2 结构化键值日志格式

日志文件每行格式：
```
ts=2026-06-19T15:30:12+0800 level=INFO  step=2/? script=sync_code_to_patchs msg="扫描 workspace"
ts=2026-06-19T15:30:25+0800 level=WARN  step=2/? script=sync_code_to_patchs msg="MISS: drivers/foo.c"
ts=2026-06-19T15:31:00+0800 level=ERROR step=2/? script=sync_code_to_patchs msg="checkout 失败" failed_cmd="git checkout ..." lineno=432 exit=1 stack="apply_plan::do_checkout_patch::main"
```

**字段约定**：
- `ts`：ISO8601 带时区，秒级精度。
- `level`：INFO / WARN / ERROR。
- `step`：当前 step 编号 / 总数（step 外为 `0/?`，总数动态补全）。
- `script`：脚本名（`harness_init` 传入）。
- `msg`：消息文本。
- 错误行额外字段：`failed_cmd`、`lineno`、`exit`、`stack`。

### 3.3 stdout 彩色精简版

```
========== STEP 2/?: 扫描 workspace ==========
  OK   drivers/bar.c
  MISS drivers/foo.c
[INFO]  Kernel 同步完成
[ERROR] checkout 失败 (line 432, exit 1)
```
- step 标题用蓝色分隔线。
- 逐文件状态保留现有彩色风格（OK 绿 / MISS 红 / SKIP 黄 / PRUNE 蓝）。
- 时间戳、script、step 编号在 stdout **不显示**（保持简洁），只在日志文件里有。
- 错误时 stdout 显示 `(line N, exit M)` 便于实时定位。

---

## 4. 统一退出码规范

| 退出码 | 语义 | 典型场景 |
|---|---|---|
| `0` | 成功 | 全部 step 成功，无 MISS/RESIDUAL |
| `1` | 通用失败 | 校验失败、执行中断、有未解决项（MISS/RESIDUAL/NEW-DIFF） |
| `2` | 部分完成，需人工跟进 | push 失败但 commit 已保留 |
| `3` | 参数/环境错误 | 锚点缺失、workspace 不存在、参数非法、依赖目录缺失 |
| `4` | 无操作（非错误） | 无改动、无可同步项、plan 为空 |

**规则约束**：所有 harness 脚本**必须**使用上述退出码语义，禁止自定义其他退出码（除非在规则文件中申请扩展）。

**对 AI workflow 编排的影响**：
- `collect_diff.sh` 退出 4 → AI 理解为"无需 commit"而非"失败重试"（语义修正）。
- `sync_code_to_patchs.sh` 退出 1 → AI 理解为"同步有缺失，需检查"（当前 exit 0 会让 AI 误判成功）。
- 各 `WORKFLOW.md` 需同步更新退出码语义说明。

---

## 5. 日志目录结构与轮转

### 5.1 目录布局（按脚本扁平）

```
engineering/harness/log/
├── .gitkeep                                    # 占位
├── collect_diff/
│   ├── collect_diff-20260619-153012.log        # 历史日志（最多保留 2 份）
│   ├── collect_diff-20260619-160500.log        # 本次日志
│   └── latest.log                              # 复制覆盖，= 最近一次日志内容
├── commit_and_push/
│   ├── ...
├── revert_code_from_patchs/
│   ├── revert_code_from_patchs-20260619-100000.log
│   ├── revert_code_from_patchs-20260619-150000.log
│   ├── latest.log
│   └── artifacts/
│       ├── 20260619-100000-plan.tsv
│       ├── 20260619-100000-verify.tsv
│       └── 20260619-150000-plan.tsv            # 最多保留 3 轮
├── sync_code_to_patchs/
│   ├── ...
│   └── artifacts/
│       ├── 20260619-153012-repolist.txt
│       └── 20260619-153012-manifest.yaml
├── sync_patchs_to_doc/
│   └── ...
└── mk_rpi5_full_image/
    ├── ...
    └── artifacts/
        └── 20260619-153012-build-report.json
```

### 5.2 轮转规则（`harness_init` 执行）

**日志文件轮转**：
1. 扫描 `<script-dir>/` 下 `<script-name>-*.log`（不含 `latest.log`、不含 `artifacts/`）。
2. 按 mtime 降序排序。
3. **保留最新 2 份**（历史），本次运行产生第 3 份。
4. 其余删除。

**artifact 轮转**：
1. 扫描 `<script-dir>/artifacts/` 下 `<ts>-*` 文件。
2. 按 timestamp 前缀降序排序。
3. **保留最新 2 轮的 artifact**（即 2 个不同 ts 前缀的全部文件）。
4. 其余删除。

**`latest.log` 更新**：
- `harness_exit` 时，`cp` 本次日志到 `<script-dir>/latest.log`（覆盖）。
- `latest.log` 不计入轮转计数。
- 采用复制覆盖（非符号链接），兼容 Windows 挂载点（`/mnt/d`）。

### 5.3 时间戳格式

- 日志文件名：`<script-name>-YYYYMMDD-HHMMSS.log`（本地时间，秒级）。
- artifact 文件名：`YYYYMMDD-HHMMSS-<artifact-name>`（与本次日志同 timestamp）。
- 日志行内 `ts` 字段：ISO8601 带时区 `YYYY-MM-DDTHH:MM:SS+ZZZZ`。

---

## 6. 规则文件 `script-observability.md` 大纲

**路径**：`engineering/harness/rules/script-observability.md`

**章节结构**：

1. **适用范围与加载时机**
   - 适用对象：`engineering/` 下所有 bash 脚本。
   - 加载时机：AGENTS.md 声明——"改动 `engineering/` 下任何 bash 脚本前，必须先加载本规则"。

2. **强制要求清单（MUST）**
   - MUST source `harness_observability.sh`（锚点查找路径）。
   - MUST 调用 `harness_init "<script-name>"`（在所有业务逻辑前）。
   - MUST 使用 `log_info/log_warn/log_error`，禁止裸 `echo`（除例外清单）。
   - MUST 用 `step_begin/step_end` 包裹每个独立阶段。
   - MUST 对所有可能失败的外部命令用 `cmd || on_err ...`（模式 A）或 `set -e + harness_init --with-errexit`（模式 B）。
   - MUST 使用统一退出码（0/1/2/3/4）。
   - MUST 把中间产物注册到 artifacts/（禁止裸写 `/tmp/`）。
   - MUST 调用 `harness_exit [code]` 退出（禁止裸 `exit`）。

3. **禁止行为清单（MUST NOT）**
   - MUST NOT 重复定义 `log_*` 函数（统一从 lib source）。
   - MUST NOT 在脚本内硬编码日志路径（一律走 lib API）。
   - MUST NOT 把中间产物写到 `/tmp/`（除非通过 `artifact_register` 中转）。
   - MUST NOT 自定义退出码（除非规则文件内申请扩展）。
   - MUST NOT 在 stdout 输出无格式的诊断信息（一律走 `log_*`）。

4. **例外清单（允许的裸 echo）**
   - `collect_diff.sh`：核心输出是给 AI 的格式化 diff 报告，属"数据流"而非"日志"，允许裸 `echo` 输出报告正文。维测相关的状态信息仍走 `log_*`。
   - `sync_patchs_to_doc.sh`：同上，报告正文允许裸 `echo`。

5. **错误捕获模式选择**
   - 模式 A（无 `set -e`，`cmd || on_err`）：适用于数据处理/同步类脚本。`on_err` 默认退出。
   - 模式 B（`set -e` + `trap ERR`）：适用于高密度命令构建脚本。`on_err` 仅打印现场，退出由 `-e` 完成。
   - 选择依据：命令密度。若脚本主体是少量"重型命令"（git/make/sync），用模式 A；若脚本布满"轻量命令"（cp/mv/test/find），逐命令加 `|| on_err` 成本过高，用模式 B。

6. **API 速查**（引用第 3 节，含示例）

7. **退出码规范**（引用第 4 节）

8. **目录结构与轮转**（引用第 5 节）

9. **维测使用指南**（面向事后回溯）
   - 如何查找日志：`harness/log/<script>/latest.log` 或按时间翻历史。
   - 如何快速定位错误：`grep "level=ERROR" latest.log`。
   - 如何看调用栈：错误行的 `stack=` 字段。
   - 如何对比两次运行：按 timestamp 文件名 diff。

### 6.1 AGENTS.md 更新

在现有"源码改动优先级"段落后追加：

```markdown
## 脚本维测规则（observability）

改动 `engineering/` 下任何 bash 脚本（含 workflows/scripts/、未来 loop/ 等）前，
必须先加载 `engineering/harness/rules/script-observability.md`。
该规则强制要求：source 公共库、接入文件日志、结构化 step、错误现场捕获、
统一退出码、中间产物归档。`harness/log/` 为本地维测产物，不归档。
```

---

## 7. 现有脚本改造方案

### 7.1 改造共性（6 个脚本统一动作）

每个脚本头部统一改造：

```bash
#!/bin/bash
set -uo pipefail   # 或 set -eo pipefail（模式 B）

# === 锚点查找 REPO_ROOT（保留现有逻辑）===
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"   # 各脚本层级不同，深度需校准
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根" >&2; exit 3; }

# === 接入维测库 ===
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init "collect_diff"   # 各脚本传自己的名字（模式 B 加 --with-errexit）

# === 业务逻辑（step_* 包裹，log_* 替换裸 echo，cmd || on_err 包裹）===
step_begin "收集 git 状态"
...
step_end $?

harness_exit 0
```

**删除的重复代码**：每个脚本内自定义的 `log_info/log_warn/log_error/log_step` 函数**全部删除**，改用 lib 提供。

**保留的业务输出**：`print_ok/print_miss/print_skip/print_prune/print_stale`（sync 脚本特有的逐文件状态打印）属业务输出，保留（内部改用 lib 的双写机制）。

### 7.2 逐脚本改造要点

| 脚本 | 行数 | 模式 | 改造要点 | artifact | 退出码调整 |
|---|---|---|---|---|---|
| `collect_diff.sh` | 175 | A | 删自定义 log_*；锚点失败 1→3；"无改动" 1→4；diff 报告正文保留裸 echo（例外） | 无 | 锚点→3；无改动→4 |
| `commit_and_push.sh` | 142 | A | 删自定义 log_*；3 个 Step 包 step_begin/end；push 失败维持 2；add/commit/push 加 `\|\| on_err` | 无 | 锚点→3；push 失败维持 2 |
| `revert_code_from_patchs.sh` | 688 | A | 删自定义 log_*；3 阶段包 step_begin/end；plan/verify 走 artifact_register；apply 每命令加 `\|\| on_err`；保留 trap _cleanup（与 lib EXIT trap 共存） | plan.tsv、verify.tsv | 锚点→3；扫描/apply/校验失败维持 1 |
| `sync_code_to_patchs.sh` | 463 | A | 删自定义 log_*；5 步包 step_begin/end；repolist/manifest 走 artifact；**MISS 退出 0→1**；保留 print_ok/miss/... | repolist.txt、manifest.yaml | 锚点→3；**MISS→1** |
| `sync_patchs_to_doc.sh` | 246 | A | 删自定义 log_*；分组报告正文保留裸 echo（例外）；状态信息走 log_* | 无 | 锚点→3 |
| `mk_rpi5_full_image.sh` | 500 | B | 保留 set -e；harness_init --with-errexit；删自定义 step()；包 step_begin/end；删除尾部 build_history.txt 追加；生成 build-report.json artifact；退出码重映射 | build-report.json | 参数错误→3；环境缺失→3；编译失败维持 1 |

### 7.3 mk_rpi5_full_image.sh 产物归档重构

**输出/产物重构**：

| 项 | 现状 | 改造后 |
|---|---|---|
| 构建历史 | 追加到 `${AOSP_ROOT}/out/build_history.txt`（workspace，非本仓库） | 每次 `harness_exit` 前生成独立 artifact：`harness/log/mk_rpi5_full_image/artifacts/<ts>-build-report.json`，随轮转 3 份 |
| 运行日志 | 无 | `harness/log/mk_rpi5_full_image/<ts>.log` + `latest.log` |
| 刷机包 .img | `${ANDROID_PRODUCT_OUT}/` → `${WINDOWS_IMG_DIR}` | **不变**（业务产物） |
| .prebuilt 备份 | `device/brcm/rpi5-kernel/` | **不变**（业务数据） |

**build-report.json 内容**：
```json
{
  "ts": "2026-06-19T15:30:12+0800",
  "script": "mk_rpi5_full_image",
  "mode": 2,
  "plan": "内核编译 + bootimage + 打包",
  "exit_code": 0,
  "duration_sec": 1234.5,
  "env": {
    "BUILD_JOBS": 8,
    "AOSP_ROOT": "/home/lechao/workspace/aosp",
    "KERNEL_SRC": "/home/lechao/workspace/rpi5-kernel-build/common",
    "LUNCH_TARGET": "aosp_rpi5-bp1a-userdebug"
  },
  "steps": [
    {"name": "编译内核", "duration_sec": 560.2, "status": "OK"},
    {"name": "编译 AOSP 镜像", "duration_sec": 400.1, "status": "OK"},
    {"name": "生成可刷写 .img", "duration_sec": 180.0, "status": "OK"},
    {"name": "拷贝到 Windows 目录", "duration_sec": 94.2, "status": "OK"}
  ],
  "artifacts": {
    "image": "RaspberryVanillaAOSP15-xxx-rpi5.img",
    "image_size": "3.2G",
    "kernel_version": "Linux version 6.1.x ...",
    "windows_dest": "/mnt/c/Files/RaspberryImages/"
  }
}
```

**退出码重映射**：
- `-mode` 参数非法（`:92-94`、`:101-106`）exit 1 → **3**（参数错误）。
- 源码/工具链/AOSP 目录缺失（`:172-179`、`:313-316` 等）exit 1 → **3**（环境错误）。
- defconfig/编译/打包失败 exit 1 → **1**（通用失败，由 `set -e` + `trap ERR` 捕获现场）。

### 7.4 并行改造策略（分两批，每批最多 3 agent）

**第一批（并行 3 agent）—— 基础设施 + 简单脚本**：
- **Agent A**（基础设施）：
  - 创建 `engineering/harness/lib/harness_observability.sh`
  - 创建 `engineering/harness/rules/script-observability.md`
  - 更新 `.gitignore`（追加 `engineering/harness/log/` 忽略，保留 `.gitkeep`）
  - 更新 `AGENTS.md`（追加维测规则引用段落）
  - 创建 `engineering/harness/log/.gitkeep`
- **Agent B**（git-push 流程，2 个简单脚本）：
  - 改造 `collect_diff.sh`（退出码 1→3/4，保留 diff 报告裸 echo 例外）
  - 改造 `commit_and_push.sh`（退出码调整，push 失败维持 2）
- **Agent C**（只读报告脚本）：
  - 改造 `sync_patchs_to_doc.sh`（退出码 1→3，保留报告正文裸 echo 例外）

**第二批（并行 3 agent）—— 复杂脚本（依赖 lib 已就绪）**：
- **Agent D**：改造 `revert_code_from_patchs.sh`（artifact: plan.tsv, verify.tsv；trap 共存）
- **Agent E**：改造 `sync_code_to_patchs.sh`（artifact: repolist.txt, manifest.yaml；**MISS 退出 0→1**）
- **Agent F**：改造 `mk_rpi5_full_image.sh`（模式 B：set -e + trap ERR；artifact: build-report.json；删除尾部 build_history.txt 追加）

**分两批的理由**：第一批先让 lib 落地并由 3 个简单脚本验证 lib API 正确，第二批复杂脚本在 lib 稳定后改造，降低并行踩坑风险。

### 7.5 验收方式（V1 dry-run 优先）

每个脚本改完后：
1. `bash -n <script>` 语法检查。
2. `shellcheck <script>`（若可用）。
3. 运行 `--check-only` / `--dry-run`（revert / sync_code / sync_patchs_to_doc），确认：
   - 日志文件正确生成在 `harness/log/<script>/`。
   - `latest.log` 正确更新。
   - 结构化键值格式正确。
   - artifact 正确归档（revert / sync_code / mk_rpi5）。
   - 退出码符合预期。
4. `collect_diff.sh` 无副作用，直接跑真实场景。
5. `commit_and_push.sh` 用 `--no-push` 在测试分支验证。
6. `mk_rpi5_full_image.sh` 用 `-mode 0`（仅打包，最快验证），若 AOSP 环境不可用则 `bash -n` + `shellcheck` 兜底。

**第一批验收**：跑 collect_diff（真实）+ commit_and_push --no-push（测试分支）+ sync_patchs_to_doc --check-only，验证 lib 基础功能。

**第二批验收**：
- revert --check-only（验证 plan 生成 + artifact 归档）。
- sync_code --dry-run（验证 repolist/manifest artifact + MISS 退出码）。
- mk_rpi5 -mode 0（仅打包，验证模式 B + build-report.json）。

---

## 8. .gitignore 更新

`.gitignore` 追加：
```
# harness 脚本维测日志（本地产物，不归档）
engineering/harness/log/
!engineering/harness/log/.gitkeep
```

（采用精确忽略 G1：只忽略 `engineering/harness/log/`，保留 `.gitkeep`。）

---

## 9. 非目标（YAGNI）

以下内容**不在本次范围内**，避免范围蔓延：

- **不重构非维测相关的重复代码**：`REPO_ROOT` 锚点查找、`find_upstream_base`、`EXCLUDE_RE` 等业务逻辑重复，本次不抽取（仅抽取维测相关：log/step/on_err/轮转/退出码）。
- **不引入 trace 级日志（set -x）**：默认不开启 `set -x`，错误现场靠 `on_err` 捕获已足够。若未来需要可加 `--verbose` 选项。
- **不引入自动化测试框架**：本次验收靠 dry-run + 手工验证，不引入 bats 等。
- **不修改 mk_rpi5 的硬编码路径**：`AOSP_ROOT`/`KERNEL_SRC` 等写死是构建脚本特性，不在维测范围。
- **不归档刷机包 .img / .prebuilt 备份**：这些是业务产物，不归维测管。
