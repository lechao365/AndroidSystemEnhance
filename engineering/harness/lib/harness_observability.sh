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
