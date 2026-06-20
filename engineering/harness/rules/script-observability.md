# 脚本维测规则（observability）

> **规则 ID**：`OBS-001` / `OBS-002`
> - `OBS-001`：`engineering/` 下所有 bash 脚本必须通过 `lib/shell/harness_bootstrap.sh` 统一入口接入，调用 `harness_init` 完成锚点与 observability 初始化，禁止业务脚本重复实现 `REPO_ROOT` 查找或私自定义日志/step/退出函数。
> - `OBS-002`：必须使用统一退出码（0/1/2/3/4），通过 `harness_exit` 退出，禁止业务逻辑裸 `exit`；临时产物必须通过 `harness_tmp_file/harness_tmp_dir` 或 `artifact_register` 落入 `artifacts/`，禁止裸写 `/tmp/`。

## 1. 适用范围与加载时机

- **适用对象**：`engineering/` 下所有 bash 脚本（harness/workflows/、harness/scripts/、未来 loop/ 等）。
- **加载时机**：改动 `engineering/` 下任何 bash 脚本前，必须先加载本规则（AGENTS.md 已声明）。

## 2. 强制要求清单（MUST）

所有 harness 脚本**必须**：

1. **MUST** 通过 `lib/shell/harness_bootstrap.sh` 统一入口加载（锚点 + source observability），禁止业务脚本重复实现 `REPO_ROOT` 查找。
2. **MUST** 调用 `harness_init "<script-name>"`（模式 A）或 `harness_init --with-errexit "<script-name>"`（模式 B），在所有业务逻辑前。
3. **MUST** 使用 `log_info/log_warn/log_error` 输出诊断信息，禁止裸 `echo`（除第 4 节例外清单）。
4. **MUST** 用 `step_begin/step_end` 包裹每个独立阶段。
5. **MUST** 对所有可能失败的外部命令做错误捕获：
   - 模式 A：`cmd || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?`
   - 模式 B：`set -e` + `harness_init --with-errexit`
6. **MUST** 使用统一退出码（0/1/2/3/4，见第 7 节）。
7. **MUST** 把中间产物注册到 artifacts/（用 `artifact_register`），默认临时文件通过 `harness_tmp_file/harness_tmp_dir` 申请（落入 artifacts 目录），禁止裸写 `/tmp/`。
8. **MUST** 调用 `harness_exit [code]` 退出（禁止业务逻辑裸 `exit`）；lib 内部函数（`on_err`、`harness_init` 早期锚点缺失等）的 `exit` 不在此列，因其会触发 EXIT trap 兜底。
9. **MUST** 仅依赖公共 API（不带下划线前缀的函数），**禁止**直接调用 `_H_*` / `_h_*` 私有符号。
10. **MUST** 对成功路径关键产物调用 `log_result` 记录结构化结果（镜像路径、commit hash、校验结论等），不得只 `echo` 到终端。

## 3. 禁止行为清单（MUST NOT）

1. **MUST NOT** 重复定义 `log_*` 函数（统一从 lib source）。
2. **MUST NOT** 在脚本内硬编码日志路径（一律走 lib API）。
3. **MUST NOT** 把中间产物写到 `/tmp/`（必须通过 `harness_tmp_file` / `harness_tmp_dir` 申请，自动落入 `artifacts/`；或通过 `artifact_register` 归档）。
4. **MUST NOT** 自定义退出码（除非在本文档申请扩展）。
5. **MUST NOT** 在 stdout 输出无格式的诊断信息（一律走 `log_*`）。
6. **MUST NOT** 用 `echo "[ERROR] ..."` 手写错误（用 `log_error`，确保走 stderr + 日志文件）。
7. **MUST NOT** 直接调用 `_H_*` / `_h_*` 私有符号（如 `_h_finalize`、`_H_INIT_TS`），公共 API 已提供等价能力。
8. **MUST NOT** 手写 `trap '... _h_finalize' EXIT`；如需注册 cleanup，用 `harness_on_exit_add "<cmd>"`。

## 4. 例外清单（允许的裸 echo）

以下脚本的**数据流输出**（给 AI/用户读取的结构化报告）允许裸 `echo`，但**维测相关的状态信息**仍必须走 `log_*`：

| 脚本 | 允许裸 echo 的内容 | 不允许裸 echo 的内容 |
|------|------------------|-------------------|
| `collect_diff.sh` | diff 报告正文（分支/status/diff/stat） | 启动/前置检查/错误信息 |
| `sync_patchs_to_doc.sh` | 变动分组报告正文、汇总统计 | 启动/前置检查/错误信息 |
| `mk_rpi5_full_image.sh` | 终端友好的成功产物摘要（与 `log_result` 并行） | 诊断/错误信息 |
| `commit_and_push.sh` | 终端友好的结果摘要（与 `log_result` 并行） | 诊断/错误信息 |

## 5. 错误捕获模式选择

### 模式 A（默认，无 `set -e`）

适用于数据处理/同步类脚本（命令数量适中的"重型命令"）。

```bash
set -uo pipefail
harness_init "script-name"
...
git checkout "$BASE" -- "$rel" || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
...
harness_exit 0
```

- `on_err` 默认 `exit 1`（fail-fast，防半修改状态）。
- `--continue`：可恢复错误（如 `find` 子目录失败），打印现场后继续。
- `--exit-code N`：自定义退出码。

### 模式 B（`set -e` + `trap ERR`）

适用于高密度命令的构建脚本（逐命令加 `|| on_err` 成本过高）。

```bash
set -eo pipefail
harness_init --with-errexit "script-name"
...
git checkout "$BASE" -- "$rel"   # 失败时 set -e 自动退出 + trap ERR 触发 on_err
...
harness_exit 0
```

- `on_err` 由 trap 自动触发，仅打印现场（不退出，退出由 `-e` 完成）。
- **注意**：模式 B 下若 step 内命令失败，`set -e` 立即退出，`step_end` 可能来不及调用，该 step 标记为 `INCOMPLETE`。

### 选择依据

- 命令密度低（少量重型 git/make 命令）→ 模式 A。
- 命令密度高（布满 cp/mv/test/find/cp 等轻量命令）→ 模式 B。
- **典型归类**：
  - `sync_code_to_patchs.sh` → **模式 B**（大量 mkdir/cp/rm/find 写操作，需 fail-fast 保证 patchs 镜像一致性）
  - `revert_code_from_patchs.sh` → 模式 A（apply 内部需要显式 rc 处理后继续/中止）
  - `mk_rpi5_full_image.sh` → 模式 B（构建脚本，命令密度高）
  - `collect_diff.sh` / `commit_and_push.sh` / `sync_patchs_to_doc.sh` → 模式 A（少量重型命令）

## 6. API 速查

### bootstrap（统一入口）

```bash
# 业务脚本头部模板（仅两行）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"
```

source 后即可使用：`REPO_ROOT`、`harness_init`、`log_*`、`step_*` 等全部公共 API。

### 核心 API

```bash
harness_init [--with-errexit] "<script-name>"  # 初始化（头部，业务逻辑前）
harness_exit [code]                            # 收尾退出（尾部）

log_info  "<msg>"                              # stdout 绿色 + 日志文件结构化
log_warn  "<msg>"                              # stdout 黄色 + 日志文件
log_error "<msg>"                              # stderr 红色 + 日志文件
log_result "<title>" "k1=v1" "k2=v2" ...       # 结构化结果记录（成功路径关键产物）

step_begin "<title>"                           # 开始 step（自动编号 + 计时）
step_end   [exit_code]                         # 结束 step（打印耗时 + 状态）

on_err [--continue] [--exit-code N] <lineno> "<cmd>" <rc>  # 错误现场捕获
artifact_register "<src-path>" "<name>"        # 中间产物归档到 artifacts/

harness_status_emit <OK|MISS|SKIP|STALE|PRUNE> <label> [msg]  # 逐文件状态输出
harness_on_exit_add "<command>"                # 注册 EXIT 回调（cleanup）
```

### 临时产物与路径

```bash
harness_tmp_file "<name>"        # 申请临时文件（落入 artifacts，自动轮转）
harness_tmp_dir  "<name>"        # 申请临时目录
harness_log_file                 # 输出本次日志文件路径
harness_artifacts_dir            # 输出本次 artifacts 目录路径
```

### 时间与 Git upstream

```bash
harness_now_iso                  # 当前 ISO8601 时间戳
harness_started_at_epoch         # harness_init 时的 epoch
harness_git_current_branch       # 当前分支名（detached 返回空）
harness_git_upstream_ref         # upstream ref（如 origin/main），无则空
harness_find_upstream_base       # merge-base HEAD <upstream>，无则空（不猜测）
harness_report_no_upstream "<ctx>"  # upstream 缺失统一报错（log_error + 修复建议）
```

### 日志文件格式（结构化键值）

```
ts=2026-06-19T15:30:12+0800 level=INFO step=2/? script=sync_code_to_patchs msg="扫描 workspace"
ts=2026-06-19T15:31:00+0800 level=ERROR step=2/? script=sync_code_to_patchs msg="命令失败" failed_cmd="git checkout ..." lineno=432 exit=1 stack="..." step_ctx="step 2: ..."
result: APPLY 结果 applied=5 plan=/path/plan.tsv
status=OK label="kernel/new/foo.c"
```

### stdout 格式（彩色精简）

```
========== STEP 2/?: 扫描 workspace ==========
  OK   drivers/bar.c
[INFO]  Kernel 同步完成
[ERROR] 命令失败 (line 432, exit 1)
```

## 7. 退出码规范

| 退出码 | 语义 | 典型场景 |
|--------|------|---------|
| `0` | 成功 | 全部 step 成功，无 MISS/RESIDUAL |
| `1` | 通用失败 | 校验失败、执行中断、有未解决项（MISS/RESIDUAL/NEW-DIFF） |
| `2` | 部分完成，需人工跟进 | push 失败但 commit 已保留 |
| `3` | 参数/环境错误 | 锚点缺失、workspace 不存在、参数非法、依赖目录缺失、upstream 未配置 |
| `4` | 无操作（非错误） | 无改动、无可同步项、plan 为空 |

## 8. 目录结构与轮转

### 目录布局

```
engineering/output/log/
├── .gitkeep
├── <script-name>/
│   ├── <script-name>-YYYYMMDD-HHMMSS.log   # 最多保留 2 份历史
│   ├── latest.log                           # 最近一次日志（复制覆盖）
│   └── artifacts/
│       └── YYYYMMDD-HHMMSS-<name>           # 中间产物 + 临时文件，保留 2 轮历史
```

### 临时文件默认位置

`harness_tmp_file` / `harness_tmp_dir` 生成的文件落到 `artifacts/YYYYMMDD-HHMMSS-tmp-<name>`，参与 artifact 轮转，不再裸写 `/tmp/`。

对需要让用户/AI 显式消费的产物（如 revert plan/verify），默认路径走 artifacts；同时保留 CLI 参数（`--plan-file` 等）允许用户指定外部路径。

### 轮转规则

- **日志**：每次 `harness_init` 时扫描历史，保留最新 2 份 + 本次 = 3 份。
- **artifact**：每次 `harness_exit` 时按 ts 前缀轮转，保留最新 2 轮 + 本轮 = 3 轮。
- **latest.log**：`harness_exit` 时 `cp` 本次日志覆盖（非符号链接，兼容 Windows 挂载）。

## 9. 维测使用指南（事后回溯）

### 查找日志

```bash
# 最新一次运行
cat engineering/output/log/<script-name>/latest.log

# 按时间翻历史
ls -lt engineering/output/log/<script-name>/
```

### 快速定位错误

```bash
# grep 错误行
grep "level=ERROR" engineering/output/log/<script-name>/latest.log

# 看调用栈（错误行的 stack= 字段）
grep "stack=" engineering/output/log/<script-name>/latest.log

# 查成功路径关键产物
grep "^result:" engineering/output/log/<script-name>/latest.log
```

### 对比两次运行

```bash
diff engineering/output/log/<script-name>/<script-name>-20260619-150000.log \
     engineering/output/log/<script-name>/<script-name>-20260619-160000.log
```

### 查看中间产物

```bash
ls engineering/output/log/<script-name>/artifacts/
```
