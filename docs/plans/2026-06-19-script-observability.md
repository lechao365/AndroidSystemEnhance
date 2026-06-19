# harness 脚本维测系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `engineering/` 下所有 bash 脚本建立统一维测体系——文件日志、结构化 step、错误现场捕获、统一退出码、中间产物归档；新增公共库与规则文件，改造现有 6 个脚本。

**Architecture:** 新增 `engineering/harness/lib/harness_observability.sh` 公共库（提供 init/log/step/on_err/artifact/exit 全套 API）+ `engineering/harness/rules/script-observability.md` 强约束规则。6 个脚本接入该库，分两批并行改造：第一批建库+规则+3 个简单脚本，第二批改 3 个复杂脚本。

**Tech Stack:** Bash 4+，无外部依赖（仅标准 GNU coreutils + git）。

**Spec:** `docs/specs/2026-06-19-script-observability-design.md`

---

## File Structure

| 类型 | 路径 | 责任 |
|---|---|---|
| 新增 | `engineering/harness/lib/harness_observability.sh` | 公共维测库（所有 API） |
| 新增 | `engineering/harness/rules/script-observability.md` | 维测规则（强约束） |
| 新增 | `engineering/harness/log/.gitkeep` | 日志目录占位 |
| 修改 | `.gitignore` | 忽略日志目录（保留 .gitkeep） |
| 修改 | `AGENTS.md` | 增加维测规则引用 |
| 修改 | `engineering/harness/workflows/git-push-to-server/collect_diff.sh` | 接入 lib |
| 修改 | `engineering/harness/workflows/git-push-to-server/commit_and_push.sh` | 接入 lib |
| 修改 | `engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh` | 接入 lib |
| 修改 | `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh` | 接入 lib + MISS 退出码 |
| 修改 | `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh` | 接入 lib + artifact |
| 修改 | `engineering/harness/scripts/mk_rpi5_full_image.sh` | 接入 lib（模式 B）+ build-report |

---

## 第一批：基础设施 + 简单脚本

### Task 1: 创建公共库 harness_observability.sh

**Files:**
- Create: `engineering/harness/lib/harness_observability.sh`

- [ ] **Step 1: 创建 lib 目录并写公共库**

```bash
mkdir -p engineering/harness/lib
```

创建 `engineering/harness/lib/harness_observability.sh`，完整内容：

```bash
#!/bin/bash
# ============================================================================
# harness_observability.sh — harness 脚本维测公共库
# 规则详见: engineering/harness/rules/script-observability.md
#
# 所有 engineering/ 下 bash 脚本 source 本库后调用：
#   harness_init "script-name"           # 初始化（模式 A）
#   harness_init --with-errexit "name"   # 初始化（模式 B，配合 set -e）
#   log_info / log_warn / log_error      # 双格式日志
#   step_begin / step_end                # 结构化 step
#   on_err ...                           # 错误现场捕获
#   artifact_register                    # 中间产物归档
#   harness_exit [code]                  # 收尾退出
# ============================================================================

# 防止重复 source
[ -n "${_HARNESS_OBSERVABILITY_SOURCED:-}" ] && return 0
_HARNESS_OBSERVABILITY_SOURCED=1

# --- 全局状态（harness_init 后填充）-----------------------------------------
_H_LOG_DIR=""           # harness/log/<script>
_H_LOG_FILE=""          # 本次日志文件全路径
_H_ARTIFACTS_DIR=""     # harness/log/<script>/artifacts
_H_SCRIPT_NAME=""       # 脚本名
_H_TS=""                # 本次运行 timestamp（YYYYMMDD-HHMMSS）
_H_STEP_CURRENT=0       # 当前 step 编号
_H_STEP_TITLE=""        # 当前 step 标题
_H_STEP_START_TS=0      # 当前 step 开始 epoch
_H_STEP_STATES=()       # 各 step 结束状态（OK/FAILED/INCOMPLETE）
_H_STEP_TITLES=()       # 各 step 标题
_H_STEP_DURATIONS=()    # 各 step 耗时秒
_H_INIT_TS=0            # harness_init epoch
_H_ERREXIT=false        # 是否模式 B

# --- 颜色（仅 stdout 用，日志文件不含 ANSI）---------------------------------
_H_RED='\033[0;31m'
_H_GREEN='\033[0;32m'
_H_YELLOW='\033[1;33m'
_H_BLUE='\033[0;34m'
_H_NC='\033[0m'

# ============================================================================
# 内部函数
# ============================================================================

# 当前时间戳（ISO8601 带时区，秒级）
_h_ts_iso() {
    date '+%Y-%m-%dT%H:%M:%S%z'
}

# 写一行到日志文件（纯文本，结构化键值）
# 用法: _h_log_file_write <level> <msg> [extra_kv...]
_h_log_file_write() {
    local level="$1" msg="$2"; shift 2
    local line="ts=$(_h_ts_iso) level=$level step=${_H_STEP_CURRENT}/? script=${_H_SCRIPT_NAME} msg=\"${msg}\""
    # 追加额外键值（如 failed_cmd=/lineno=/exit=/stack=）
    local kv
    for kv in "$@"; do
        line+=" ${kv%%=*}=\"${kv#*=}\""
    done
    printf '%s\n' "$line" >> "$_H_LOG_FILE"
}

# ============================================================================
# harness_init
# ============================================================================
harness_init() {
    local errexit=false
    # 解析 --with-errexit
    while [ $# -gt 0 ]; do
        case "$1" in
            --with-errexit) errexit=true; shift ;;
            --) shift; break ;;
            *) break ;;
        esac
    done
    _H_SCRIPT_NAME="$1"
    _H_ERREXIT="$errexit"
    _H_INIT_TS=$(date +%s)
    _H_TS=$(date '+%Y%m%d-%H%M%S')

    # 锚点查找 REPO_ROOT（从 BASH_SOURCE 向上找 AGENTS.md）
    local bsrc="${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}"
    local dir
    dir="$(cd "$(dirname "$bsrc")" && pwd)"
    REPO_ROOT="$dir"
    while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
        REPO_ROOT="$(dirname "$REPO_ROOT")"
    done
    if [ ! -f "$REPO_ROOT/AGENTS.md" ]; then
        echo "ERROR: harness_init 未找到项目根（AGENTS.md 锚点缺失）" >&2
        exit 3
    fi

    # 日志目录
    _H_LOG_DIR="$REPO_ROOT/engineering/harness/log/$_H_SCRIPT_NAME"
    _H_ARTIFACTS_DIR="$_H_LOG_DIR/artifacts"
    _H_LOG_FILE="$_H_LOG_DIR/$_H_SCRIPT_NAME-$_H_TS.log"
    mkdir -p "$_H_LOG_DIR" "$_H_ARTIFACTS_DIR"

    # 日志轮转：保留历史 2 份（本次为第 3 份）
    _h_rotate_logs

    # 注册 trap
    # EXIT trap：收尾（汇总、复制 latest.log、artifact 轮转）
    trap '_h_finalize' EXIT
    # 模式 B：ERR trap 自动触发 on_err 现场捕获（退出由 set -e 完成）
    if [ "$_H_ERREXIT" = true ]; then
        trap '_h_on_err_trapped' ERR
    fi

    # 启动横幅到日志
    _h_log_file_write "INFO" "脚本启动: $_H_SCRIPT_NAME (errexit=$_H_ERREXIT)"
}

# 日志轮转（保留历史 2 份 + 本次 = 3 份）
_h_rotate_logs() {
    local f
    # 收集历史日志（排除 latest.log）
    local old_logs=()
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        old_logs+=("$f")
    done < <(ls -t "$_H_LOG_DIR"/${_H_SCRIPT_NAME}-*.log 2>/dev/null)

    # 保留最新 2 份，删除其余
    local i=0
    for f in "${old_logs[@]}"; do
        i=$((i + 1))
        if [ $i -gt 2 ]; then
            # 提取该日志的 ts 前缀，顺带清理对应 artifact
            local fts
            fts="$(basename "$f")"
            fts="${fts#${_H_SCRIPT_NAME}-}"
            fts="${fts%.log}"
            rm -f "$f"
            rm -f "$_H_ARTIFACTS_DIR"/${fts}-* 2>/dev/null
        fi
    done
}

# EXIT trap 收尾
_h_finalize() {
    local exit_code=$?
    # 若处于某个 step 内未正常结束，标记 INCOMPLETE
    if [ -n "$_H_STEP_TITLE" ]; then
        _H_STEP_STATES+=("INCOMPLETE")
        local dur=$(( $(date +%s) - _H_STEP_START_TS ))
        _H_STEP_DURATIONS+=("$dur")
    fi
    # 打印汇总
    _h_print_summary "$exit_code"
    # 复制本次日志到 latest.log（覆盖）
    [ -f "$_H_LOG_FILE" ] && cp -f "$_H_LOG_FILE" "$_H_LOG_DIR/latest.log"
    # artifact 轮转（保留 2 轮 + 本轮 = 3 轮）
    _h_rotate_artifacts
}

# 打印运行汇总
_h_print_summary() {
    local exit_code="$1"
    local total=${#_H_STEP_STATES[@]}
    local failed=0 i
    for ((i=0; i<total; i++)); do
        [ "${_H_STEP_STATES[i]}" != "OK" ] && failed=$((failed + 1))
    done
    local dur=$(( $(date +%s) - _H_INIT_TS ))
    {
        echo ""
        echo "=========================================="
        echo " 运行汇总: $_H_SCRIPT_NAME"
        echo " 退出码:   $exit_code"
        echo " Step:     $total 个 ($failed 个失败)"
        echo " 耗时:     ${dur}s"
        echo " 日志:     $_H_LOG_FILE"
        echo "=========================================="
    } >&1
    _h_log_file_write "INFO" "脚本结束: exit=$exit_code duration=${dur}s steps=$total failed=$failed"
}

# ============================================================================
# log_info / log_warn / log_error（双格式）
# ============================================================================
log_info() {
    _h_log_file_write "INFO" "$*"
    printf "${_H_GREEN}[INFO]${_H_NC}  %s\n" "$*"
}

log_warn() {
    _h_log_file_write "WARN" "$*"
    printf "${_H_YELLOW}[WARN]${_H_NC}  %s\n" "$*"
}

log_error() {
    _h_log_file_write "ERROR" "$*"
    # 错误走 stderr，便于 2>error.log 分流
    printf "${_H_RED}[ERROR]${_H_NC} %s\n" "$*" >&2
}

# ============================================================================
# step_begin / step_end
# ============================================================================
step_begin() {
    _H_STEP_CURRENT=$((_H_STEP_CURRENT + 1))
    _H_STEP_TITLE="$1"
    _H_STEP_START_TS=$(date +%s)
    _h_log_file_write "INFO" "step 开始: $1"
    printf "\n${_H_BLUE}========== STEP %s/?: %s ==========${_H_NC}\n" "$_H_STEP_CURRENT" "$1"
}

step_end() {
    local rc="${1:-0}"
    local dur=$(( $(date +%s) - _H_STEP_START_TS ))
    _H_STEP_TITLES+=("$_H_STEP_TITLE")
    _H_STEP_DURATIONS+=("$dur")
    if [ "$rc" -eq 0 ]; then
        _H_STEP_STATES+=("OK")
        _h_log_file_write "INFO" "step 结束: $_H_STEP_TITLE (OK, took ${dur}s)"
        printf "${_H_GREEN}[STEP %s]${_H_NC} OK (took %ss)\n" "$_H_STEP_CURRENT" "$dur"
    else
        _H_STEP_STATES+=("FAILED")
        _h_log_file_write "ERROR" "step 结束: $_H_STEP_TITLE (FAILED exit=$rc, took ${dur}s)"
        printf "${_H_RED}[STEP %s]${_H_NC} FAILED (exit=%s, took %ss)\n" "$_H_STEP_CURRENT" "$rc" "$dur"
    fi
    _H_STEP_TITLE=""
}

# ============================================================================
# on_err（错误现场捕获）
# ============================================================================
# 模式 A 手动调用:  cmd || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
# 模式 B trap 自动: 由 _h_on_err_trapped 调用
on_err() {
    local continue_mode=false exit_code_want=""
    # 解析选项
    while [ $# -gt 0 ]; do
        case "$1" in
            --continue) continue_mode=true; shift ;;
            --exit-code) exit_code_want="$2"; shift 2 ;;
            *) break ;;
        esac
    done
    local lineno="$1" cmd="$2" rc="$3"

    # 调用栈
    local stack=""
    local i
    for ((i=${#FUNCNAME[@]}-1; i>=1; i--)); do
        stack+="${FUNCNAME[i]}:${BASH_LINENO[i-1]}"
        [ $i -gt 1 ] && stack+=" <- "
    done
    [ -z "$stack" ] && stack="(top-level)"

    local step_ctx="(step 外)"
    [ -n "$_H_STEP_TITLE" ] && step_ctx="step ${_H_STEP_CURRENT}: $_H_STEP_TITLE"

    # 写日志 + stderr
    _h_log_file_write "ERROR" "命令失败: $cmd" \
        "failed_cmd=$cmd" "lineno=$lineno" "exit=$rc" "stack=$stack" "step_ctx=$step_ctx"
    printf "${_H_RED}[ERROR]${_H_NC} 命令失败 (line %s, exit %s): %s\n" "$lineno" "$rc" "$cmd" >&2
    printf "  %s\n" "$step_ctx" >&2
    printf "  栈: %s\n" "$stack" >&2

    # 退出或继续
    if [ "$continue_mode" = false ]; then
        local final_rc="${exit_code_want:-1}"
        exit "$final_rc"
    fi
    return "$rc"
}

# 模式 B 的 ERR trap 包装（从 BASH_COMMAND/LINENO 取现场）
_h_on_err_trapped() {
    # ERR trap 下：$BASH_COMMAND 是失败命令，$? 是退出码
    local rc=$?
    local cmd="$BASH_COMMAND"
    local lineno="${BASH_LINENO[0]:-unknown}"
    on_err --continue "$lineno" "$cmd" "$rc"
    # 退出由 set -e 完成，这里不退出
}

# ============================================================================
# artifact_register（中间产物归档）
# ============================================================================
artifact_register() {
    local src="$1" name="$2"
    local dest="$_H_ARTIFACTS_DIR/$_H_TS-$name"
    if [ -f "$src" ]; then
        cp -f "$src" "$dest"
        _h_log_file_write "INFO" "artifact 归档: $name -> $dest"
    elif [ -d "$src" ]; then
        cp -rf "$src" "$dest"
        _h_log_file_write "INFO" "artifact 归档(目录): $name -> $dest"
    else
        _h_log_file_write "WARN" "artifact 源不存在: $src"
        return 1
    fi
}

# artifact 轮转：保留最新 2 轮 ts 前缀（+ 本轮 = 3 轮）
_h_rotate_artifacts() {
    [ -d "$_H_ARTIFACTS_DIR" ] || return 0
    # 收集不同 ts 前缀
    local -a ts_list=()
    local f base ts
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        base="$(basename "$f")"
        ts="${base%%-*}"   # YYYYMMDD-HHMMSS 取到第一个 - 之前? 不对
        # 文件名格式 YYYYMMDD-HHMMSS-name，ts 含一个 -，需特殊处理
        ts="$(echo "$base" | grep -oE '^[0-9]{8}-[0-9]{6}')"
        [ -z "$ts" ] && continue
        # 去重
        local found=false t
        for t in "${ts_list[@]}"; do [ "$t" = "$ts" ] && { found=true; break; }; done
        [ "$found" = false ] && ts_list+=("$ts")
    done < <(ls -1 "$_H_ARTIFACTS_DIR" 2>/dev/null)

    # ts_list 按降序，保留前 2 个（本轮 + 1 轮历史），删除其余
    # 注意：本轮 ts 就是 $_H_TS
    # 排序（降序）
    local -a sorted=()
    while IFS= read -r t; do sorted+=("$t"); done < <(printf '%s\n' "${ts_list[@]}" | sort -r)
    local i=0 t
    for t in "${sorted[@]}"; do
        i=$((i + 1))
        if [ $i -gt 2 ]; then
            rm -f "$_H_ARTIFACTS_DIR"/${t}-* 2>/dev/null
        fi
    done
}

# ============================================================================
# harness_exit
# ============================================================================
harness_exit() {
    local rc="${1:-$?}"
    exit "$rc"
}

# ============================================================================
# 工具函数
# ============================================================================
harness_log_file() {
    printf '%s' "$_H_LOG_FILE"
}

harness_artifacts_dir() {
    printf '%s' "$_H_ARTIFACTS_DIR"
}
```

- [ ] **Step 2: 语法检查**

Run: `bash -n engineering/harness/lib/harness_observability.sh`
Expected: 无输出（语法正确）

- [ ] **Step 3: 提交**

```bash
git add engineering/harness/lib/harness_observability.sh
git commit -m "新增(lib): harness_observability.sh 维测公共库"
```

---

### Task 2: 创建规则文件 script-observability.md

**Files:**
- Create: `engineering/harness/rules/script-observability.md`

- [ ] **Step 1: 写规则文件**

创建 `engineering/harness/rules/script-observability.md`，完整内容：

```markdown
# 脚本维测规则（observability）

## 1. 适用范围与加载时机

- **适用对象**：`engineering/` 下所有 bash 脚本（harness/workflows/、harness/scripts/、未来 loop/ 等）。
- **加载时机**：改动 `engineering/` 下任何 bash 脚本前，必须先加载本规则（AGENTS.md 已声明）。

## 2. 强制要求清单（MUST）

所有 harness 脚本**必须**：

1. **MUST** source `engineering/harness/lib/harness_observability.sh`（通过 REPO_ROOT 锚点查找路径）。
2. **MUST** 调用 `harness_init "<script-name>"`（模式 A）或 `harness_init --with-errexit "<script-name>"`（模式 B），在所有业务逻辑前。
3. **MUST** 使用 `log_info/log_warn/log_error` 输出诊断信息，禁止裸 `echo`（除第 4 节例外清单）。
4. **MUST** 用 `step_begin/step_end` 包裹每个独立阶段。
5. **MUST** 对所有可能失败的外部命令做错误捕获：
   - 模式 A：`cmd || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?`
   - 模式 B：`set -e` + `harness_init --with-errexit`
6. **MUST** 使用统一退出码（0/1/2/3/4，见第 7 节）。
7. **MUST** 把中间产物注册到 artifacts/（用 `artifact_register`），禁止裸写 `/tmp/`。
8. **MUST** 调用 `harness_exit [code]` 退出（禁止裸 `exit`，除锚点缺失等极早期场景）。

## 3. 禁止行为清单（MUST NOT）

1. **MUST NOT** 重复定义 `log_*` 函数（统一从 lib source）。
2. **MUST NOT** 在脚本内硬编码日志路径（一律走 lib API）。
3. **MUST NOT** 把中间产物写到 `/tmp/`（除非通过 `artifact_register` 中转）。
4. **MUST NOT** 自定义退出码（除非在本文档申请扩展）。
5. **MUST NOT** 在 stdout 输出无格式的诊断信息（一律走 `log_*`）。
6. **MUST NOT** 用 `echo "[ERROR] ..."` 手写错误（用 `log_error`，确保走 stderr + 日志文件）。

## 4. 例外清单（允许的裸 echo）

以下脚本的**数据流输出**（给 AI/用户读取的结构化报告）允许裸 `echo`，但**维测相关的状态信息**仍必须走 `log_*`：

| 脚本 | 允许裸 echo 的内容 | 不允许裸 echo 的内容 |
|------|------------------|-------------------|
| `collect_diff.sh` | diff 报告正文（分支/status/diff/stat） | 启动/前置检查/错误信息 |
| `sync_patchs_to_doc.sh` | 变动分组报告正文、汇总统计 | 启动/前置检查/错误信息 |

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

## 6. API 速查

```bash
harness_init [--with-errexit] "<script-name>"  # 初始化（头部，业务逻辑前）
harness_exit [code]                            # 收尾退出（尾部）

log_info  "<msg>"                              # stdout 绿色 + 日志文件结构化
log_warn  "<msg>"                              # stdout 黄色 + 日志文件
log_error "<msg>"                              # stderr 红色 + 日志文件

step_begin "<title>"                           # 开始 step（自动编号 + 计时）
step_end   [exit_code]                         # 结束 step（打印耗时 + 状态）

on_err [--continue] [--exit-code N] <lineno> "<cmd>" <rc>  # 错误现场捕获
artifact_register "<src-path>" "<name>"        # 中间产物归档到 artifacts/

harness_log_file                               # 输出本次日志文件路径
harness_artifacts_dir                          # 输出本次 artifacts 目录路径
```

### 日志文件格式（结构化键值）

```
ts=2026-06-19T15:30:12+0800 level=INFO step=2/? script=sync_code_to_patchs msg="扫描 workspace"
ts=2026-06-19T15:31:00+0800 level=ERROR step=2/? script=sync_code_to_patchs msg="命令失败" failed_cmd="git checkout ..." lineno=432 exit=1 stack="..." step_ctx="step 2: ..."
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
| `3` | 参数/环境错误 | 锚点缺失、workspace 不存在、参数非法、依赖目录缺失 |
| `4` | 无操作（非错误） | 无改动、无可同步项、plan 为空 |

## 8. 目录结构与轮转

### 目录布局

```
engineering/harness/log/
├── .gitkeep
├── <script-name>/
│   ├── <script-name>-YYYYMMDD-HHMMSS.log   # 最多保留 2 份历史
│   ├── latest.log                           # 最近一次日志（复制覆盖）
│   └── artifacts/
│       └── YYYYMMDD-HHMMSS-<name>           # 中间产物，保留 2 轮历史
```

### 轮转规则

- **日志**：每次 `harness_init` 时扫描历史，保留最新 2 份 + 本次 = 3 份。
- **artifact**：每次 `harness_exit` 时按 ts 前缀轮转，保留最新 2 轮 + 本轮 = 3 轮。
- **latest.log**：`harness_exit` 时 `cp` 本次日志覆盖（非符号链接，兼容 Windows 挂载）。

## 9. 维测使用指南（事后回溯）

### 查找日志

```bash
# 最新一次运行
cat engineering/harness/log/<script-name>/latest.log

# 按时间翻历史
ls -lt engineering/harness/log/<script-name>/
```

### 快速定位错误

```bash
# grep 错误行
grep "level=ERROR" engineering/harness/log/<script-name>/latest.log

# 看调用栈（错误行的 stack= 字段）
grep "stack=" engineering/harness/log/<script-name>/latest.log
```

### 对比两次运行

```bash
diff engineering/harness/log/<script-name>/<script-name>-20260619-150000.log \
     engineering/harness/log/<script-name>/<script-name>-20260619-160000.log
```

### 查看中间产物

```bash
ls engineering/harness/log/<script-name>/artifacts/
```
```

- [ ] **Step 2: 提交**

```bash
git add engineering/harness/rules/script-observability.md
git commit -m "新增(rules): script-observability 脚本维测规则"
```

---

### Task 3: 更新 .gitignore 与 AGENTS.md，创建 log/.gitkeep

**Files:**
- Modify: `.gitignore`
- Modify: `AGENTS.md`
- Create: `engineering/harness/log/.gitkeep`

- [ ] **Step 1: 更新 .gitignore**

在 `.gitignore` 末尾追加（保留现有 5 行）：

```

# harness 脚本维测日志（本地产物，不归档）
engineering/harness/log/
!engineering/harness/log/.gitkeep
```

- [ ] **Step 2: 创建 log 目录占位**

```bash
mkdir -p engineering/harness/log
touch engineering/harness/log/.gitkeep
```

`.gitkeep` 内容为空文件。

- [ ] **Step 3: 更新 AGENTS.md**

在 `AGENTS.md` 的"## PlantUML 画图约束"段落后、"## 权限规则"段落前，插入新段落：

```markdown
## 脚本维测规则（observability）

改动 `engineering/` 下任何 bash 脚本（含 workflows/、scripts/、未来 loop/ 等）前，必须先加载 [engineering/harness/rules/script-observability.md](engineering/harness/rules/script-observability.md)。
该规则强制要求：source 公共库、接入文件日志、结构化 step、错误现场捕获、统一退出码、中间产物归档。`engineering/harness/log/` 为本地维测产物，不归档。
```

- [ ] **Step 4: 验证 gitignore 生效**

Run: `git status`
Expected: `engineering/harness/log/.gitkeep` 显示为 untracked，且 `.gitignore` 显示为 modified；log 目录下其他内容（若有）不被追踪。

- [ ] **Step 5: 提交**

```bash
git add .gitignore AGENTS.md engineering/harness/log/.gitkeep
git commit -m "配置(observability): 忽略日志目录 + AGENTS.md 引用维测规则"
```

---

### Task 4: 改造 collect_diff.sh

**Files:**
- Modify: `engineering/harness/workflows/git-push-to-server/collect_diff.sh`

**退出码调整**：锚点缺失 1→3；"无改动" 1→4；未知参数 1→3。

- [ ] **Step 1: 改造头部（删自定义 log_*，接入 lib）**

替换 `collect_diff.sh` 第 1-35 行（从 `#!/bin/bash` 到 `log_step` 定义结束）为：

```bash
#!/bin/bash
set -uo pipefail

# ============================================================================
# collect_diff.sh — 收集 git status + diff + 分支信息，格式化输出给 AI
# 规则详见: engineering/harness/workflows/git-push-to-server/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/git-push-to-server/collect_diff.sh [--stat-only]
# 退出码:  0=有改动（正常输出）; 3=参数/环境错误; 4=无改动（输出 nothing to commit）
# ============================================================================

# --- 锚点查找 REPO_ROOT -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }

# --- 接入维测库 -------------------------------------------------------------
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init "collect_diff"
```

删除原来的 Colors 段（RED/GREEN/BLUE/NC）和 log_info/log_error/log_step 函数定义（原 27-35 行）。

- [ ] **Step 2: 修改参数解析的退出码**

原 `collect_diff.sh:48`（未知参数）和 `:44-47`（-h/--help）：

将 `*) log_error "未知参数: $arg"; exit 1 ;;` 改为：
```bash
        *) log_error "未知参数: $arg"; harness_exit 3 ;;
```

- [ ] **Step 3: 修改"无改动"退出码**

原 `collect_diff.sh:70-73`：

```bash
if [ -z "$STATUS_OUTPUT" ]; then
    echo "nothing to commit, working tree clean"
    exit 1
fi
```

改为：
```bash
if [ -z "$STATUS_OUTPUT" ]; then
    echo "nothing to commit, working tree clean"
    _h_log_file_write "INFO" "无改动，退出码 4"
    harness_exit 4
fi
```

- [ ] **Step 4: 替换 log_step 为 step_begin/step_end**

原 `collect_diff.sh:81`：`log_step "GIT PUSH CONTEXT"` 改为：

```bash
step_begin "收集 git push 上下文"
```

在脚本末尾（原 `:175` `echo "======================================"` 之后）追加：

```bash
step_end 0
harness_exit 0
```

- [ ] **Step 5: 前置检查加 on_err**

原 `collect_diff.sh:55`：`cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; exit 1; }`

改为：
```bash
cd "$REPO_ROOT" || { log_error "无法进入仓库根目录: $REPO_ROOT"; harness_exit 3; }
```

- [ ] **Step 6: 语法检查**

Run: `bash -n engineering/harness/workflows/git-push-to-server/collect_diff.sh`
Expected: 无输出

- [ ] **Step 7: 运行验证**

```bash
bash engineering/harness/workflows/git-push-to-server/collect_diff.sh --stat-only
echo "exit=$?"
ls engineering/harness/log/collect_diff/
cat engineering/harness/log/collect_diff/latest.log
```

Expected:
- 正常输出 diff 报告（保持原有格式）。
- `exit=0`（有改动场景）或 `exit=4`（无改动场景）。
- `log/collect_diff/` 目录存在，含 `collect_diff-<ts>.log` 和 `latest.log`。
- `latest.log` 含结构化键值行（`ts=... level=INFO ...`）。

- [ ] **Step 8: 提交**

```bash
git add engineering/harness/workflows/git-push-to-server/collect_diff.sh
git commit -m "改造(observability): collect_diff.sh 接入维测库"
```

---

### Task 5: 改造 commit_and_push.sh

**Files:**
- Modify: `engineering/harness/workflows/git-push-to-server/commit_and_push.sh`

**退出码调整**：锚点 1→3；参数错误 1→3；push 失败维持 2；commit/add 失败维持 1。

- [ ] **Step 1: 改造头部（删自定义 log_*，接入 lib）**

替换 `commit_and_push.sh` 第 1-28 行为：

```bash
#!/bin/bash
set -uo pipefail

# ============================================================================
# commit_and_push.sh — git add -A + commit -F + push，失败保留 commit
# 规则详见: engineering/harness/workflows/git-push-to-server/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh \
#              --message-file <path> [--branch <b>] [--remote origin] [--no-push]
# 退出码:  0=成功; 1=通用失败; 2=push失败(commit已保留); 3=参数/环境错误
# ============================================================================

# --- 锚点查找 REPO_ROOT -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }

# --- 接入维测库 -------------------------------------------------------------
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init "commit_and_push"
```

删除原 Colors 段和 log_info/log_error/log_step 定义（原 20-28 行）。

- [ ] **Step 2: 修改参数解析退出码**

将原 `:41`、`:44`、`:47`、`:58` 的 `exit 1` 改为 `harness_exit 3`（参数错误）。

具体：
- `--message-file` 缺参数：`exit 1` → `harness_exit 3`
- `--branch` 缺参数：`exit 1` → `harness_exit 3`
- `--remote` 缺参数：`exit 1` → `harness_exit 3`
- 未知参数：`exit 1` → `harness_exit 3`

- [ ] **Step 3: 修改校验段退出码**

原 `:65-87`：
- `cd "$REPO_ROOT" || { log_error "..."; exit 1; }` → `harness_exit 3`
- MESSAGE_FILE 空/不存在/为空：`exit 1` → `harness_exit 3`
- 无法确定分支：`exit 1` → `harness_exit 3`

- [ ] **Step 4: Step 1 (git add) 包 step + on_err**

原 `:92-100`：

```bash
log_step "Step 1: 暂存所有改动"
git add -A || { log_error "git add -A 失败"; exit 1; }

STAGED_COUNT=$(git diff --cached --name-only 2>/dev/null | grep -c '.' || true)
if [ "$STAGED_COUNT" -eq 0 ]; then
    log_error "无改动可提交（git add -A 后暂存区为空）"
    exit 1
fi
log_info "已暂存 $STAGED_COUNT 个文件"
```

改为：

```bash
step_begin "Step 1: 暂存所有改动"
git add -A || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?

STAGED_COUNT=$(git diff --cached --name-only 2>/dev/null | grep -c '.' || true)
if [ "$STAGED_COUNT" -eq 0 ]; then
    log_error "无改动可提交（git add -A 后暂存区为空）"
    harness_exit 4
fi
log_info "已暂存 $STAGED_COUNT 个文件"
step_end 0
```

- [ ] **Step 5: Step 2 (git commit) 包 step + on_err**

原 `:105-111`：

改为：
```bash
step_begin "Step 2: 提交"
git commit -F "$MESSAGE_FILE" || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
log_info "提交成功: $COMMIT_HASH"
step_end 0
```

- [ ] **Step 6: Step 3 (git push) 包 step + on_err**

原 `:116-142`：

改为：
```bash
if [ "$NO_PUSH" = true ]; then
    log_info "--no-push 模式，跳过推送"
    step_begin "完成（未推送）"
    echo "  commit: $COMMIT_HASH"
    echo "  分支:   $BRANCH (仅本地)"
    step_end 0
    harness_exit 0
fi

step_begin "Step 3: 推送"
log_info "目标: $REMOTE/$BRANCH"
git push "$REMOTE" "$BRANCH" || on_err --exit-code 2 "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
REMOTE_URL=$(git remote get-url "$REMOTE" 2>/dev/null || echo "unknown")
log_info "推送成功: $REMOTE/$BRANCH ($REMOTE_URL)"
step_end 0

step_begin "完成"
echo "  commit: $COMMIT_HASH"
echo "  推送:   $REMOTE/$BRANCH ($REMOTE_URL)"
step_end 0
harness_exit 0
```

（注意：原 push 失败的多行 log_error 提示信息删除——`on_err` 已打印现场；如需保留"commit 已保留"提示，可在 on_err 前加一行 `log_warn "commit 已保留（本地 $COMMIT_HASH），未自动回退"`。）

- [ ] **Step 7: 语法检查与运行验证**

Run: `bash -n engineering/harness/workflows/git-push-to-server/commit_and_push.sh`
Expected: 无输出

运行验证（用 --no-push 避免真实推送）：
```bash
echo "test message" > /tmp/test-msg.txt
bash engineering/harness/workflows/git-push-to-server/commit_and_push.sh --message-file /tmp/test-msg.txt --no-push
echo "exit=$?"
cat engineering/harness/log/commit_and_push/latest.log
```

（注：此步会产生真实 commit，验证后用 `git reset --soft HEAD~1` 回退。若仓库当前无改动，会 exit 4。）

- [ ] **Step 8: 提交**

```bash
git add engineering/harness/workflows/git-push-to-server/commit_and_push.sh
git commit -m "改造(observability): commit_and_push.sh 接入维测库"
```

---

### Task 6: 改造 sync_patchs_to_doc.sh

**Files:**
- Modify: `engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh`

**退出码调整**：锚点 1→3；patchs 目录缺失 1→3；未知参数 1→3；无变动 exit 0 维持。

- [ ] **Step 1: 改造头部（删自定义 log_*，接入 lib）**

替换 `sync_patchs_to_doc.sh` 第 1-30 行为：

```bash
#!/bin/bash
set -uo pipefail

# ============================================================================
# sync_patchs_to_doc.sh — patchs/rpi5 变动报告生成器
# 规则详见: engineering/harness/workflows/sync-patchs-to-doc/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh [--check-only] [--full-diff]
# 退出码:  0=成功(有/无变动); 3=参数/环境错误
# ============================================================================

# --- 锚点查找 REPO_ROOT -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }
PATCH_DIR="patchs/rpi5"

# --- 接入维测库 -------------------------------------------------------------
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init "sync_patchs_to_doc"
```

删除原 Colors 段和 log_info/log_warn/log_error/log_step 定义（原 20-30 行）。

- [ ] **Step 2: 修改参数解析退出码**

原 `:46`：`*) log_error "未知参数: $arg"; exit 1 ;;` → `harness_exit 3`

- [ ] **Step 3: 修改前置检查退出码**

原 `:53`：`cd "$REPO_ROOT" || { log_error "..."; exit 1; }` → `harness_exit 3`

原 `:55-58`：
```bash
if [ ! -d "$PATCH_DIR" ]; then
    log_error "patchs 目录不存在: $PATCH_DIR"
    exit 1
fi
```
改为 `exit 1` → `harness_exit 3`。

- [ ] **Step 4: 包 step（报告生成分组）**

原 `:84`：`log_step "Patchs → Doc 变动报告"` 改为 `step_begin "Patchs → Doc 变动报告"`

在脚本末尾（原 `:246` AI 提示 cat 块结束后）追加：
```bash
step_end 0
harness_exit 0
```

- [ ] **Step 5: 无变动场景加 step**

原 `:75-79`：
```bash
if [ -z "$DIFF_OUTPUT" ]; then
    echo ""
    log_info "无变动"
    exit 0
fi
```
改为：
```bash
if [ -z "$DIFF_OUTPUT" ]; then
    echo ""
    log_info "无变动"
    harness_exit 4
fi
```
（无变动 = 无操作 = 退出码 4。）

- [ ] **Step 6: 语法检查与运行验证**

Run: `bash -n engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh`
Expected: 无输出

运行验证：
```bash
bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh --check-only
echo "exit=$?"
cat engineering/harness/log/sync_patchs_to_doc/latest.log
```

Expected:
- 正常输出变动报告（保持原有格式）。
- `exit=0`（有变动）或 `exit=4`（无变动）。
- `latest.log` 含结构化键值。

- [ ] **Step 7: 提交**

```bash
git add engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh
git commit -m "改造(observability): sync_patchs_to_doc.sh 接入维测库"
```

---

## 第二批：复杂脚本

### Task 7: 改造 sync_code_to_patchs.sh

**Files:**
- Modify: `engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh`

**退出码调整**：锚点 1→3；workspace 缺失 1→3；**MISS 退出 0→1**；未知参数 1→3。

- [ ] **Step 1: 改造头部（删自定义 log_*，保留 print_*）**

替换 `sync_code_to_patchs.sh` 第 1-52 行（保留 EXCLUDE_RE、Counters、print_* 函数）为：

```bash
#!/bin/bash
set -uo pipefail

# ============================================================================
# sync_code_to_patchs.sh — workspace → patchs/rpi5 全量镜像同步脚本
# 规则详见: engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]
# 退出码:  0=成功; 1=有MISS(需检查); 3=参数/环境错误
# ============================================================================

# --- 锚点查找 REPO_ROOT -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }
PATCH_ROOT="$REPO_ROOT/patchs/rpi5"
KERNEL_WS="${KERNEL_WS:-$HOME/workspace/rpi5-kernel-build/common}"
AOSP_WS="${AOSP_WS:-$HOME/workspace/aosp}"

# 排除规则：grep -E 模式（构建系统约定，不会因定制变更）
EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/'

# 排除规则：目录 basename
EXCLUDE_DIR_RE='^(out|prebuilts)$'

# --- Counters ---------------------------------------------------------------
TOTAL_OK=0
TOTAL_MISS=0
TOTAL_SKIP=0
TOTAL_STALE=0
TOTAL_PRUNE=0

# --- 接入维测库 -------------------------------------------------------------
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

# --- 业务输出（逐文件状态，保留彩色风格，内部走 lib 双写）-------------------
print_ok()    { printf "  ${_H_GREEN}OK${_H_NC}   %s\n" "$1"; TOTAL_OK=$((TOTAL_OK + 1)); _h_log_file_write "INFO" "OK: $1"; }
print_miss()  { printf "  ${_H_RED}MISS${_H_NC} %s\n" "$1"; TOTAL_MISS=$((TOTAL_MISS + 1)); _h_log_file_write "WARN" "MISS: $1"; }
print_skip()  { printf "  ${_H_YELLOW}SKIP${_H_NC} %s\n" "$1"; TOTAL_SKIP=$((TOTAL_SKIP + 1)); _h_log_file_write "INFO" "SKIP: $1"; }
print_stale() { printf "  ${_H_YELLOW}STALE${_H_NC} %s\n" "$1"; TOTAL_STALE=$((TOTAL_STALE + 1)); _h_log_file_write "INFO" "STALE: $1"; }
print_prune() { printf "  ${_H_BLUE}PRUNE${_H_NC} %s\n" "$1"; TOTAL_PRUNE=$((TOTAL_PRUNE + 1)); _h_log_file_write "INFO" "PRUNE: $1"; }

harness_init "sync_code_to_patchs"
```

注意：`print_*` 函数移到 `harness_init` 前，因为它们引用 `_H_GREEN` 等变量（source lib 后已定义）。但 `_h_log_file_write` 需要 `_H_LOG_FILE`（harness_init 后才有），所以 `print_*` 内的 `_h_log_file_write` 调用在 harness_init 前定义没问题（函数体延迟执行）。

- [ ] **Step 2: 修改 find_upstream_base（保留不动）**

`find_upstream_base` 函数（原 `:54-75`）保留原样，不动。

- [ ] **Step 3: 修改参数解析与前置检查退出码**

原 `:91`：`*) log_error "..."; exit 1 ;;` → `harness_exit 3`

原 `:98-110`（前置检查段）：
- `log_step "前置检查"` → `step_begin "前置检查"`
- `[ -d "$KERNEL_WS/.git" ] && ...` 保留
- `if [ "$KERNEL_OK" = false ] && ...; then log_error "未找到有效的 workspace"; exit 1; fi` → `harness_exit 3`
- 段末加 `step_end 0`

- [ ] **Step 4: 改 REPO_LIST_FILE 用 artifact_register**

原 `:123-124`：
```bash
REPO_LIST_FILE=$(mktemp /tmp/sync_repolist.XXXXXX)
trap 'rm -f "$REPO_LIST_FILE"' EXIT
```

改为：
```bash
REPO_LIST_FILE=$(mktemp /tmp/sync_repolist.XXXXXX)
```

删除原 `trap 'rm -f "$REPO_LIST_FILE"' EXIT`（lib 的 EXIT trap 会兜底，且我们在末尾 artifact_register）。

在脚本末尾（manifest 重生成后）追加 artifact 归档：
```bash
artifact_register "$REPO_LIST_FILE" "repolist.txt"
rm -f "$REPO_LIST_FILE"
```

- [ ] **Step 5: manifest 临时文件 artifact 归档（在删除前归档）**

脚本中 `MANIFEST_TMP`（原 `:405` 创建）在后续 `:430`/`:433`/`:437` 被 `mv` 或 `rm` 删除，因此**必须在删除前归档**。

在原 `:428` 的 `if [ ! -f "$MANIFEST" ] || ! diff -q ...` 判断**之前**插入归档：

```bash
# 归档本次生成的 manifest 临时文件（供回溯）
artifact_register "$MANIFEST_TMP" "manifest.yaml"
```

（即：在生成 `> "$MANIFEST_TMP"` 完成后、diff/mv/rm 之前归档。归档后原 mv/rm 逻辑保留不动，/tmp 副本由 artifact_register 已复制到 harness/log。）

- [ ] **Step 6: 5 个 Step 包 step_begin/end**

将原 `log_step "Step 0: 扫描 workspace"` / `log_step "Step 1: Kernel 同步"` 等替换为：
- `step_begin "Step 0: 扫描 workspace"` ... 段末 `step_end 0`
- `step_begin "Step 1: Kernel 同步"` ... 段末 `step_end 0`
- `step_begin "Step 2: AOSP 同步"` ... 段末 `step_end 0`
- `step_begin "Step 3: 删除对齐"` ... 段末 `step_end 0`
- `step_begin "Step 4: 重生成 manifest.yaml"` ... 段末 `step_end 0`

- [ ] **Step 7: MISS 退出码变更**

原脚本末尾（`:453` 附近）：`log_warn "同步完成，有 $TOTAL_MISS 个 MISS"; exit 0`

改为：
```bash
if [ "$TOTAL_MISS" -gt 0 ]; then
    log_warn "同步完成，有 $TOTAL_MISS 个 MISS（退出码 1）"
    harness_exit 1
else
    log_info "同步完成，无 MISS"
    harness_exit 0
fi
```

- [ ] **Step 8: 语法检查与 dry-run 验证**

Run: `bash -n engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh`
Expected: 无输出

运行验证：
```bash
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh --check-only
echo "exit=$?"
ls engineering/harness/log/sync_code_to_patchs/artifacts/
cat engineering/harness/log/sync_code_to_patchs/latest.log | grep "level="
```

Expected:
- 正常输出 OK/MISS/SKIP 列表。
- `exit=1`（有 MISS）或 `exit=0`（无 MISS）。
- `artifacts/` 含 `repolist.txt`（AOSP workspace 存在时）。
- `latest.log` 含结构化键值。

- [ ] **Step 9: 提交**

```bash
git add engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh
git commit -m "改造(observability): sync_code_to_patchs.sh 接入维测库 + MISS退出码"
```

---

### Task 8: 改造 revert_code_from_patchs.sh

**Files:**
- Modify: `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`

**退出码调整**：锚点 1→3；未知参数 1→3；扫描/apply/校验失败维持 1。

- [ ] **Step 1: 改造头部（删自定义 log_*，保留 trap 共存）**

替换 `revert_code_from_patchs.sh` 第 1-43 行为：

```bash
#!/bin/bash
set -uo pipefail

# ============================================================================
# revert_code_from_patchs.sh — patchs/rpi5 → workspace 回退脚本
# 以 patchs/rpi5 为已知良好基线，把 workspace 中偏离 patchs 的部分拉回一致。
# 规则详见: engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md
#
# 用法:
#   bash .../revert_code_from_patchs.sh [--plan-file <path>]         # 生成回退计划
#   bash .../revert_code_from_patchs.sh --apply --plan-file <path>   # 执行回退计划
#   bash .../revert_code_from_patchs.sh --check-only                  # 仅扫描预览
# 退出码:  0=成功; 1=扫描/apply/校验失败; 3=参数/环境错误; 4=plan为空
# ============================================================================

# --- 锚点查找 REPO_ROOT -----------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }
PATCH_ROOT="$REPO_ROOT/patchs/rpi5"
KERNEL_WS="${KERNEL_WS:-$HOME/workspace/rpi5-kernel-build/common}"
AOSP_WS="${AOSP_WS:-$HOME/workspace/aosp}"

# 排除规则（与 sync_code_to_patchs.sh 保持一致）
EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/'
EXCLUDE_DIR_RE='^(out|prebuilts)$'

# --- 接入维测库 -------------------------------------------------------------
# shellcheck source=../../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init "revert_code_from_patchs"

# 临时文件清理（与 lib EXIT trap 共存：lib 先注册，此处追加）
TMP_FILES=()
_cleanup() { [ ${#TMP_FILES[@]} -gt 0 ] && rm -f "${TMP_FILES[@]}" 2>/dev/null || true; }
trap '_cleanup; _h_finalize' EXIT INT TERM
```

删除原 Colors 段、log_info/log_warn/log_error/log_step 定义（原 31-38 行）。

**注意 trap 共存**：原 `trap _cleanup EXIT INT TERM` 与 lib 的 `trap '_h_finalize' EXIT` 冲突。改为合并：`trap '_cleanup; _h_finalize' EXIT INT TERM`，确保 _cleanup 先执行（清临时文件），_h_finalize 后执行（汇总 + latest.log + artifact 轮转）。

- [ ] **Step 2: 保留 find_upstream_base（不动）**

原 `:45-75` 的 `find_upstream_base` 函数保留原样。

- [ ] **Step 3: 修改参数解析退出码**

找到参数解析段（原 `:76-100` 附近，`case "$MODE" in`），未知参数 `exit 1` → `harness_exit 3`。

- [ ] **Step 4: 修改 plan/verify 临时文件 + artifact 归档**

原 plan 生成处（原 `:384-437` 附近）：`PLAN_FILE="/tmp/revert-plan-<ts>.tsv"` 保留为 /tmp 临时文件，但在 plan 生成后追加：

```bash
artifact_register "$PLAN_FILE" "plan.tsv"
```

原 verify 生成处（原 `:593-660` 附近）：`VERIFY_FILE="/tmp/revert-verify-<ts>.tsv"` 保留为 /tmp，verify 后追加：

```bash
artifact_register "$VERIFY_FILE" "verify.tsv"
```

**注意**：不要在生成 plan/verify 后立即 `rm`，让 _cleanup trap 兜底清理 /tmp 副本；artifact 副本已复制到 harness/log。

- [ ] **Step 5: 3 个阶段包 step_begin/end**

- 原 `gen_plan` / `gen_plan_silent` 调用前后：
  ```bash
  step_begin "阶段 1: 生成回退计划"
  ... gen_plan 调用 ...
  step_end $?
  ```

- 原 `apply_plan` 调用前后：
  ```bash
  step_begin "阶段 2: 执行回退计划"
  ... apply_plan 调用 ...
  step_end $?
  ```

- 原 `verify_after_apply` 调用前后：
  ```bash
  step_begin "阶段 3: 落盘校验"
  ... verify_after_apply 调用 ...
  step_end $?
  ```

- [ ] **Step 6: apply 阶段每命令加 on_err**

原 `:444-514` 的 `do_checkout_patch` / `do_checkout_only` / `do_restore` / `do_revert_extra` 函数内，每个 git 命令的 `|| { log_error "..."; return 1; }` 保持 return 1（函数内不直接 exit），但在调用这些函数的 `apply_plan`（原 `:517-587`）中，函数返回非 0 时用 `on_err`：

原 `:548` 附近：
```bash
do_checkout_patch "$rel" "$diff_file" || { log_error "apply 失败"; exit 1; }
```
改为：
```bash
do_checkout_patch "$rel" "$diff_file" || on_err "${BASH_LINENO[0]}" "$BASH_COMMAND" $?
```

对 `do_checkout_only` / `do_restore` / `do_revert_extra` 的调用同理。

- [ ] **Step 7: verify 校验失败退出码**

原 `:660` 附近：有 RESIDUAL/NEW-DIFF 时 `return 1` / `exit 1`，维持 exit 1（通用失败）。

在脚本最末尾追加：
```bash
harness_exit 0
```
（若中间有 exit 1，会走 EXIT trap 兜底收尾。）

- [ ] **Step 8: plan 为空场景退出码 4**

找到 plan 为空的判断处（原 `:437` 附近，`if [ "$PLAN_COUNT" -eq 0 ]` 或类似），`exit 0` → `harness_exit 4`（无操作）。

- [ ] **Step 9: 语法检查与 check-only 验证**

Run: `bash -n engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`
Expected: 无输出

运行验证：
```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --check-only
echo "exit=$?"
ls engineering/harness/log/revert_code_from_patchs/artifacts/
cat engineering/harness/log/revert_code_from_patchs/latest.log | grep "level="
```

Expected:
- 正常输出扫描结果。
- `exit=0`（无偏离）或 `exit=1`（有 RESIDUAL）或 `exit=4`（plan 为空）。
- `artifacts/` 含 `plan.tsv`、`verify.tsv`。
- `latest.log` 含结构化键值。

- [ ] **Step 10: 提交**

```bash
git add engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh
git commit -m "改造(observability): revert_code_from_patchs.sh 接入维测库 + artifact归档"
```

---

### Task 9: 改造 mk_rpi5_full_image.sh

**Files:**
- Modify: `engineering/harness/scripts/mk_rpi5_full_image.sh`

**模式 B**：保留 `set -e` + `harness_init --with-errexit`。退出码：参数错误 1→3；环境缺失 1→3；编译失败维持 1。

- [ ] **Step 1: 改造头部（保留 set -e，接入 lib，删自定义 step()）**

替换 `mk_rpi5_full_image.sh` 第 1-163 行（配置区 + 参数解析 + print_help + step 定义 + 启动横幅）：

保留第 1-145 行（配置、参数解析、MODE 判定、TOTAL_STEPS）不变，仅修改以下部分：

1. 修改 shebang 下 set 行（原 `:28-29`）：
```bash
set -e
set -o pipefail
```
（保留不动，模式 B 需要 set -e。）

2. 删除自定义 `step()` 函数（原 `:149-155`）和 `CUR_STEP`/`TOTAL_STEPS` 变量（原 `:145-147`）——改用 lib 的 step_begin/step_end（动态计数）。

3. 在配置区末尾（原 `:163` 启动横幅 echo 之前）插入 lib 接入：

```bash
# --- 锚点查找 REPO_ROOT + 接入维测库 ---------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 3; }

# shellcheck source=../lib/harness_observability.sh
source "$REPO_ROOT/engineering/harness/lib/harness_observability.sh"

harness_init --with-errexit "mk_rpi5_full_image"
```

4. 启动横幅（原 `:157-163`）的 `echo` 改为 `log_info`：
```bash
log_info "树莓派5 AOSP 一键编译打包"
log_info "模式: ${MODE} — ${PLAN}"
log_info "并行: ${BUILD_JOBS} 核心"
```

- [ ] **Step 2: 修改参数解析退出码**

原 `:92-94`：
```bash
echo "[ERROR] -mode 参数必须是 0~4"
exit 1
```
改为：
```bash
log_error "-mode 参数必须是 0~4"
harness_exit 3
```

原 `:101-106`（未知参数）：
```bash
echo "[ERROR] 未知参数: $1"
...
exit 1
```
改为 `harness_exit 3`，并把 `echo "[ERROR]"` 改为 `log_error`。

- [ ] **Step 3: 替换所有 step() 调用为 step_begin/step_end**

脚本中共有 4 处 `step "..."` 调用（编译内核、编译AOSP镜像/确认镜像就绪、生成.img、拷贝到Windows），每个对应一个阶段。

**关键**：模式 B（set -e）下，step 内命令失败时 set -e 直接退出，step_end 可能不执行。因此每个 step 的结构为：

```bash
step_begin "编译内核（AOSP Clang + LLD）"
# ... 业务命令（失败时 set -e 退出，trap ERR 触发 _h_on_err_trapped）...
step_end 0
```

替换清单：
- 原 `:170` `step "编译内核（AOSP Clang + LLD）"` → `step_begin "编译内核（AOSP Clang + LLD）"`
  在该阶段末尾（原 `:285` `fi` 前，即 else 分支前）加 `step_end 0`
  else 分支 `step "跳过内核编译（使用已有内核）"`（原 `:274`）→ `step_begin "跳过内核编译（使用已有内核）"` + 末尾 `step_end 0`

- 原 `:309` `step "编译 AOSP 镜像（${TARGET_DESC}）"` → `step_begin "..."`
  阶段末尾（原 `:392` `fi` 前）加 `step_end 0`
  else 分支 `step "确认镜像就绪..."`（原 `:375`）→ `step_begin "..."` + `step_end 0`

- 原 `:400` `step "生成可刷写 .img 镜像（rpi5-mkimg.sh）"` → `step_begin "..."`
  阶段末尾（原 `:444` `echo "  [OK] ${IMG_NAME}..."` 后）加 `step_end 0`

- 原 `:450` `step "拷贝刷机包到 ${WINDOWS_IMG_DIR}"` → `step_begin "..."`
  阶段末尾（原 `:475` `echo "  [OK]..."` 后）加 `step_end 0`

- [ ] **Step 4: 环境检查退出码 1→3**

原 `:172-179`（KERNEL_SRC/CLANG_BIN 不存在）、`:313-316`（AOSP build/envsetup.sh 不存在）等所有"目录/文件不存在"的 `exit 1` 改为 `harness_exit 3`（环境错误）。

具体位置（原行号）：
- `:172-175` KERNEL_SRC 不存在
- `:176-179` CLANG_BIN 不存在
- `:232-235` KERNEL_IMAGE 未生成
- `:241-245` dtb 未生成
- `:278-282` 缺少预编译内核
- `:313-316` AOSP 构建脚本不存在
- `:322-325` ANDROID_PRODUCT_OUT 未设置
- `:351-354` 缺少镜像
- `:367-371` 打包所需镜像缺失
- `:383-390` mode 0 镜像缺失
- `:404-407` rpi5-mkimg.sh 不存在
- `:438-441` 刷机包未生成

全部 `exit 1` → `harness_exit 3`，`echo "[ERROR]"` → `log_error`。

**注意**：编译/打包失败（如 `:210-213` defconfig 失败、`:220-223` 内核编译失败、`:331-338` AOSP 编译失败、`:425-432` rpi5-mkimg.sh 失败）维持 `exit 1`（通用失败），由 set -e + trap ERR 自动捕获。把这些 `echo "[ERROR]"` 改为 `log_error`，`exit 1` 维持（或改 `harness_exit 1`）。

- [ ] **Step 5: 添加 build-report.json artifact**

替换原 `:497-500`（尾部 build_history.txt 追加）：

```bash
# 记录构建报告到 artifact（替代原 build_history.txt）
BUILD_END_TS=$(date +%s)
BUILD_DURATION=$((BUILD_END_TS - _H_INIT_TS))
REPORT_FILE=$(mktemp /tmp/build-report.XXXXXX.json)
cat > "$REPORT_FILE" <<EOF
{
  "ts": "$(_h_ts_iso)",
  "script": "mk_rpi5_full_image",
  "mode": ${MODE},
  "plan": "${PLAN}",
  "exit_code": 0,
  "duration_sec": ${BUILD_DURATION},
  "env": {
    "BUILD_JOBS": ${BUILD_JOBS},
    "AOSP_ROOT": "${AOSP_ROOT}",
    "KERNEL_SRC": "${KERNEL_SRC}",
    "LUNCH_TARGET": "${LUNCH_TARGET}"
  },
  "artifacts": {
    "image": "${IMG_NAME:-unknown}",
    "image_size": "${IMG_SIZE:-unknown}",
    "kernel_version": "${KERNEL_VER:-unknown}",
    "windows_dest": "${WINDOWS_IMG_DIR}"
  }
}
EOF
artifact_register "$REPORT_FILE" "build-report.json"
rm -f "$REPORT_FILE"

harness_exit 0
```

- [ ] **Step 6: 语法检查**

Run: `bash -n engineering/harness/scripts/mk_rpi5_full_image.sh`
Expected: 无输出

- [ ] **Step 7: 运行验证（若 AOSP 环境可用）**

```bash
bash engineering/harness/scripts/mk_rpi5_full_image.sh -mode 0
echo "exit=$?"
ls engineering/harness/log/mk_rpi5_full_image/artifacts/
cat engineering/harness/log/mk_rpi5_full_image/artifacts/*-build-report.json
```

Expected:
- `exit=0`（打包成功）或 `exit=1`/`exit=3`（环境问题）。
- `artifacts/` 含 `build-report.json`。
- JSON 格式正确。

**若 AOSP 环境不可用**：仅做 `bash -n` + `shellcheck`（若安装）兜底：
```bash
shellcheck engineering/harness/scripts/mk_rpi5_full_image.sh
```

- [ ] **Step 8: 提交**

```bash
git add engineering/harness/scripts/mk_rpi5_full_image.sh
git commit -m "改造(observability): mk_rpi5_full_image.sh 接入维测库(模式B) + build-report"
```

---

## 最终验收

### Task 10: 全量验证与日志目录确认

- [ ] **Step 1: 所有脚本语法检查**

```bash
for f in engineering/harness/lib/harness_observability.sh \
         engineering/harness/workflows/git-push-to-server/collect_diff.sh \
         engineering/harness/workflows/git-push-to-server/commit_and_push.sh \
         engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh \
         engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh \
         engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh \
         engineering/harness/scripts/mk_rpi5_full_image.sh; do
    echo "=== $f ==="
    bash -n "$f" && echo "OK" || echo "FAIL"
done
```

Expected: 全部 OK。

- [ ] **Step 2: 日志目录结构确认**

```bash
ls -R engineering/harness/log/
```

Expected: 6 个脚本子目录 + .gitkeep，每个子目录含日志文件 + latest.log（+ artifacts/ 含对应产物）。

- [ ] **Step 3: 轮转验证（连续运行 4 次）**

对任一脚本（如 collect_diff.sh）连续运行 4 次：
```bash
for i in 1 2 3 4; do
    bash engineering/harness/workflows/git-push-to-server/collect_diff.sh --stat-only >/dev/null 2>&1
done
ls -1 engineering/harness/log/collect_diff/*.log | wc -l
```

Expected: 3（保留 2 份历史 + latest.log 不计入 *.log 通配，或 latest.log 计入则为 4——需确认 latest.log 是否匹配 `*.log`。按文件名 `latest.log` 匹配 `*.log`，所以应是 4 个：2 历史 + 1 本次 + latest.log。若要 latest.log 不计入，轮转逻辑应排除它。）

**注意**：若 `ls *.log | wc -l` 返回 4（含 latest.log），属正常——latest.log 是额外副本，轮转计数时已排除它。

- [ ] **Step 4: git status 确认无意外提交**

```bash
git status
```

Expected: `engineering/harness/log/` 下内容全部被 gitignore（除 .gitkeep），working tree clean。

- [ ] **Step 5: 最终提交（若有未提交改动）**

```bash
git status
# 若有未提交的改动
git add -A
git commit -m "改造(observability): 维测系统全量落地"
```

---

## Self-Review 记录

（实施时由实施者填写，确认 spec 覆盖、占位符、类型一致性。）
