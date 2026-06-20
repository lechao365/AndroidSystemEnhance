#!/bin/bash
# ============================================================================
# harness_observability.sh — harness 脚本维测公共库
# 规则详见: engineering/harness/rules/script-observability.md
#
# API 分层:
#   公共 API (业务脚本可自由调用):
#     harness_init [--with-errexit] "<script-name>"   初始化
#     harness_exit [code]                             收尾退出
#     log_info / log_warn / log_error                 双格式日志
#     step_begin / step_end                           结构化 step
#     on_err ...                                      错误现场捕获
#     artifact_register <src> <name>                  中间产物归档
#     log_result "<title>" "k=v" ...                  结构化结果记录
#     harness_status_emit <status> <label> [msg]      逐文件状态输出
#     harness_on_exit_add "<cmd>"                     注册 EXIT 回调
#     harness_tmp_file / harness_tmp_dir <name>       临时文件/目录
#     harness_log_file / harness_artifacts_dir        路径查询
#     harness_now_iso / harness_started_at_epoch      时间 API
#     harness_git_current_branch / harness_git_upstream_ref
#     harness_find_upstream_base                      upstream 基线
#     harness_report_no_upstream "<ctx>"              upstream 缺失报错
#
#   私有 API (以下划线开头，业务脚本禁止直接依赖):
#     _h_* / _H_*                                     库内部实现
# ============================================================================

# 防止重复 source
[ -n "${_HARNESS_OBSERVABILITY_SOURCED:-}" ] && return 0
_HARNESS_OBSERVABILITY_SOURCED=1

# --- 全局状态（harness_init 后填充）-----------------------------------------
_H_LOG_DIR=""           # output/log/<script>
_H_LOG_FILE=""          # 本次日志文件全路径
_H_ARTIFACTS_DIR=""     # output/log/<script>/artifacts
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
_H_EXIT_HOOKS=()        # EXIT 回调列表（业务脚本可注册 cleanup）

# --- workspace 同步专用共享常量（sync/revert 脚本复用，保证 diff 基线一致）---
# 排除规则：grep -E 模式（构建系统约定，不会因定制变更）
HARNESS_EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/'
# 排除规则：目录 basename
HARNESS_EXCLUDE_DIR_RE='^(out|prebuilts)$'

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
    # 转义 msg 中的双引号，避免破坏结构化键值格式
    local esc_msg="${msg//\"/\\\"}"
    local line="ts=$(_h_ts_iso) level=$level step=${_H_STEP_CURRENT}/? script=${_H_SCRIPT_NAME} msg=\"${esc_msg}\""
    # 追加额外键值（如 failed_cmd=/lineno=/exit=/stack=）
    local kv
    for kv in "$@"; do
        # 转义 value 部分的双引号
        local esc_kv_v="${kv#*=}"
        esc_kv_v="${esc_kv_v//\"/\\\"}"
        line+=" ${kv%%=*}=\"${esc_kv_v}\""
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

    if [ -n "${REPO_ROOT:-}" ] && [ -f "$REPO_ROOT/AGENTS.md" ]; then
        REPO_ROOT="$(cd "$REPO_ROOT" && pwd)"
    else
        # 锚点查找 REPO_ROOT（从 BASH_SOURCE 向上找 AGENTS.md）
        local bsrc="${BASH_SOURCE[1]:-${BASH_SOURCE[0]}}"
        local dir
        dir="$(cd "$(dirname "$bsrc")" && pwd)"
        REPO_ROOT="$dir"
        while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
            REPO_ROOT="$(dirname "$REPO_ROOT")"
        done
    fi
    if [ ! -f "$REPO_ROOT/AGENTS.md" ]; then
        echo "ERROR: harness_init 未找到项目根（AGENTS.md 锚点缺失）" >&2
        exit 3
    fi

    # 日志目录
    _H_LOG_DIR="$REPO_ROOT/engineering/output/log/$_H_SCRIPT_NAME"
    _H_ARTIFACTS_DIR="$_H_LOG_DIR/artifacts"
    _H_LOG_FILE="$_H_LOG_DIR/$_H_SCRIPT_NAME-$_H_TS.log"
    mkdir -p "$_H_LOG_DIR" "$_H_ARTIFACTS_DIR"

    # 日志轮转：保留历史 2 份（本次为第 3 份）
    _h_rotate_logs

    # 注册 trap
    # EXIT trap：收尾（执行 hooks、汇总、复制 latest.log、artifact 轮转）
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
    done < <(ls -t "$_H_LOG_DIR"/${_H_SCRIPT_NAME}-*.log 2>/dev/null || true)

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
            rm -rf "$_H_ARTIFACTS_DIR"/${fts}-* 2>/dev/null || true
        fi
    done
}

# EXIT trap 收尾（先执行业务 hooks，再汇总、轮转）
_h_finalize() {
    local exit_code=$?
    # 执行业务脚本注册的 EXIT 回调（cleanup 等）
    local hook
    for hook in "${_H_EXIT_HOOKS[@]}"; do
        eval "$hook" 2>/dev/null || true
    done
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
    local artifact_count=0
    if [ -d "$_H_ARTIFACTS_DIR" ]; then
        artifact_count=$(ls -1 "$_H_ARTIFACTS_DIR"/${_H_TS}-* 2>/dev/null | wc -l) || artifact_count=0
    fi
    {
        echo ""
        echo "=========================================="
        echo " 运行汇总: $_H_SCRIPT_NAME"
        echo " 退出码:   $exit_code"
        echo " Step:     $total 个 ($failed 个失败)"
        echo " 耗时:     ${dur}s"
        echo " Artifacts: $artifact_count 个 ($_H_ARTIFACTS_DIR)"
        echo " 日志:     $_H_LOG_FILE"
        echo "=========================================="
    } >&1
    _h_log_file_write "INFO" "脚本结束: exit=$exit_code duration=${dur}s steps=$total failed=$failed artifacts=$artifact_count"
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
# log_result — 结构化结果记录（成功路径关键产物落日志）
# 用法: log_result "<title>" "k1=v1" "k2=v2" ...
# ============================================================================
log_result() {
    local title="$1"; shift
    printf "\n%s\n" "$title"
    local kv
    for kv in "$@"; do
        printf "  %s\n" "$kv"
    done
    local line="result: $title"
    for kv in "$@"; do
        line+=" $kv"
    done
    printf '%s\n' "$line" >> "$_H_LOG_FILE"
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
# harness_status_emit — 逐文件状态输出（统一终端 + 日志格式）
# 用法: harness_status_emit <OK|MISS|SKIP|STALE|PRUNE> <label> [message]
# ============================================================================
harness_status_emit() {
    local status="$1" label="$2" msg="${3:-}"
    local color
    case "$status" in
        OK)        color="$_H_GREEN" ;;
        MISS)      color="$_H_RED" ;;
        SKIP|STALE) color="$_H_YELLOW" ;;
        PRUNE)     color="$_H_BLUE" ;;
        *)         color="$_H_NC" ;;
    esac
    printf "  ${color}%-5s${_H_NC} %s\n" "$status" "$label"
    # 日志文件中写入裸 status= 行（便于 grep 结构化状态，见 rules 第 9 节）
    local status_line="status=$status label=\"$label\""
    if [ -n "$msg" ]; then
        status_line+=" msg=\"$msg\""
    fi
    printf '%s\n' "$status_line" >> "$_H_LOG_FILE"
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
# harness_on_exit_add — 注册 EXIT 回调（在 lib 收尾前执行）
# 用法: harness_on_exit_add "<command>"
# ============================================================================
harness_on_exit_add() {
    _H_EXIT_HOOKS+=("$1")
}

# ============================================================================
# harness_tmp_file / harness_tmp_dir — 临时文件/目录（落入 artifacts，统一轮转）
# 用法: local f; f=$(harness_tmp_file "name")
#       local d; d=$(harness_tmp_dir "name")
# ============================================================================
harness_tmp_file() {
    local name="${1:-tmp}"
    name="${name//[^a-zA-Z0-9_.-]/_}"
    local f="$_H_ARTIFACTS_DIR/$_H_TS-tmp-${name}"
    # 保证唯一性（同秒多次调用）
    local i=0
    while [ -e "$f" ]; do i=$((i + 1)); f="$_H_ARTIFACTS_DIR/$_H_TS-tmp-${name}-${i}"; done
    : > "$f"
    printf '%s' "$f"
}

harness_tmp_dir() {
    local name="${1:-tmpdir}"
    name="${name//[^a-zA-Z0-9_.-]/_}"
    local d="$_H_ARTIFACTS_DIR/$_H_TS-tmpdir-${name}"
    local i=0
    while [ -e "$d" ]; do i=$((i + 1)); d="$_H_ARTIFACTS_DIR/$_H_TS-tmpdir-${name}-${i}"; done
    mkdir -p "$d"
    printf '%s' "$d"
}

# ============================================================================
# artifact_register（中间产物归档）
# ============================================================================
artifact_register() {
    local src="$1" name="$2"
    local dest="$_H_ARTIFACTS_DIR/$_H_TS-$name"
    if [ -f "$src" ]; then
        cp -f "$src" "$dest" || {
            _h_log_file_write "ERROR" "artifact 归档失败(文件): $name src=$src"
            return 1
        }
        _h_log_file_write "INFO" "artifact 归档: $name -> $dest"
    elif [ -d "$src" ]; then
        cp -rf "$src" "$dest" || {
            _h_log_file_write "ERROR" "artifact 归档失败(目录): $name src=$src"
            return 1
        }
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
    done < <(ls -1 "$_H_ARTIFACTS_DIR" 2>/dev/null || true)

    # ts_list 按降序，保留前 3 个（本轮 + 2 轮历史），删除其余
    # 注意：本轮 ts 就是 $_H_TS
    # 排序（降序）
    local -a sorted=()
    while IFS= read -r t; do sorted+=("$t"); done < <(printf '%s\n' "${ts_list[@]}" | sort -r)
    local i=0 t
    for t in "${sorted[@]}"; do
        i=$((i + 1))
        if [ $i -gt 3 ]; then
            rm -rf "$_H_ARTIFACTS_DIR"/${t}-* 2>/dev/null || true
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
# 路径查询
# ============================================================================
harness_log_file() {
    printf '%s' "$_H_LOG_FILE"
}

harness_artifacts_dir() {
    printf '%s' "$_H_ARTIFACTS_DIR"
}

# ============================================================================
# 时间 API
# ============================================================================
harness_now_iso() {
    _h_ts_iso
}

harness_started_at_epoch() {
    printf '%s' "$_H_INIT_TS"
}

# ============================================================================
# Git upstream 基线检测（显式策略，禁止猜测）
# ============================================================================

# harness_git_current_branch — 返回当前分支名（detached HEAD 返回空串）
harness_git_current_branch() {
    local branch
    branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
    [ "$branch" = "HEAD" ] && branch=""
    printf '%s' "$branch"
}

# harness_git_upstream_ref — 返回当前分支的 upstream ref（如 origin/main），无则空
harness_git_upstream_ref() {
    local ups=""
    # 优先用 git 的 @{upstream}
    ups=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || echo "")
    if [ -z "$ups" ]; then
        # 兜底：从 branch config 读 remote + merge
        local branch remote merge
        branch=$(harness_git_current_branch)
        [ -z "$branch" ] && { printf ''; return; }
        remote=$(git config "branch.${branch}.remote" 2>/dev/null || echo "")
        merge=$(git config "branch.${branch}.merge" 2>/dev/null || echo "")
        if [ -n "$remote" ] && [ -n "$merge" ]; then
            # merge 形如 refs/heads/main，取 short
            local short="${merge#refs/heads/}"
            short="${short#refs/heads}"
            ups="${remote}/${short}"
        fi
    fi
    printf '%s' "$ups"
}

# harness_find_upstream_base — 返回 merge-base HEAD <upstream-ref>，无 upstream 返回空
# 注意：不做任意 remote 猜测，调用者需对空返回值做显式失败处理
harness_find_upstream_base() {
    local ups base
    ups=$(harness_git_upstream_ref)
    [ -z "$ups" ] && { printf ''; return; }
    base=$(git merge-base HEAD "$ups" 2>/dev/null || echo "")
    printf '%s' "$base"
}

# harness_report_no_upstream — upstream 缺失时的统一错误报告
# 用法: harness_report_no_upstream "<上下文描述>"
harness_report_no_upstream() {
    local ctx="${1:-当前仓库}"
    local branch
    branch=$(harness_git_current_branch)
    log_error "${ctx} 无法确定 upstream base（分支: ${branch:-detached}）"
    log_error "请设置 upstream: git branch --set-upstream-to=origin/${branch:-<branch>}"
}
