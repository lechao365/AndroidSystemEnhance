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

# --- 锚点 + 公共库（bootstrap 统一入口）-------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

PATCH_ROOT="$(harness_path PATCHS_DIR)"
KERNEL_WS="${KERNEL_WS:-$(harness_env_path ENV_KERNEL_WS)}"
AOSP_WS="${AOSP_WS:-$(harness_env_path ENV_AOSP_WS)}"

# --- 接入维测库（模式 A：扫描为主，apply 内部显式失败处理）-------------------
harness_init "revert_code_from_patchs"

# 临时文件清理：通过公共库 exit hook 注册（不再手写 trap）
TMP_FILES=()
_cleanup() {
    [ ${#TMP_FILES[@]} -gt 0 ] && rm -f "${TMP_FILES[@]}" 2>/dev/null || true
}
harness_on_exit_add "_cleanup"
trap 'exit 130' INT   # 128 + SIGINT(2)，触发 EXIT trap 走公共库收尾
trap 'exit 143' TERM  # 128 + SIGTERM(15)

# diff_normalized — 比较两个 diff 文件语义是否一致（忽略 index 行 hash）
diff_normalized() {
    local d1="$1" d2="$2"
    diff <(grep -vE '^index ' "$d1" 2>/dev/null) <(grep -vE '^index ' "$d2" 2>/dev/null) >/dev/null 2>&1
}

# _is_excluded — 判断文件是否命中排除规则（编译产物 / 构建缓存）
_is_excluded() {
    local f="$1"
    echo "$f" | grep -qE "$HARNESS_EXCLUDE_RE" && return 0
    local bn="${f%%/*}"
    [[ "$bn" =~ $HARNESS_EXCLUDE_DIR_RE ]] && return 0
    return 1
}

# _parse_proj — 把 plan 中的"项目"字段解析为 workspace 绝对路径
_parse_proj() {
    local proj="$1"
    case "$proj" in
        kernel) echo "$KERNEL_WS" ;;
        aosp)   echo "$AOSP_WS" ;;
        aosp:*) echo "$AOSP_WS/${proj#aosp:}" ;;
        *)      echo "" ;;
    esac
}

# _resolve_workspace_target — 基于 proj/rel 解析 workspace 文件绝对路径
_resolve_workspace_target() {
    local proj="$1" rel="$2"
    local ws; ws=$(_parse_proj "$proj")
    [ -z "$ws" ] && { printf ''; return; }

    if [[ "$proj" == aosp:* ]]; then
        local scope="${proj#aosp:}"
        if [[ "$rel" == "$scope"/* ]]; then
            printf '%s/%s' "$AOSP_WS" "$rel"
            return
        fi
    fi

    printf '%s/%s' "$ws" "$rel"
}

# --- 参数解析 ---------------------------------------------------------------
MODE="plan"
PLAN_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)         MODE="apply"; shift ;;
        --check-only)    MODE="check-only"; shift ;;
        --plan-file)     [ $# -lt 2 ] && { log_error "--plan-file 需要一个路径参数"; harness_exit 3; }; PLAN_FILE="$2"; shift 2 ;;
        --plan-file=*)   PLAN_FILE="${1#*=}"; shift ;;
        -h|--help)
            cat <<'USAGE'
Usage: bash revert_code_from_patchs.sh [--plan-file <path>] [--apply] [--check-only]
  无参数 / --plan-file X    生成回退计划（默认 artifacts/revert_code_from_patchs/<ts>-plan.tsv）
  --apply --plan-file X     执行回退计划（只执行标记 + 的条目）
  --check-only              仅扫描预览，不生成 plan 文件
USAGE
            harness_exit 0 ;;
        *) log_error "未知参数: $1"; harness_exit 3 ;;
    esac
done

if [ "$MODE" = "apply" ] && [ -z "$PLAN_FILE" ]; then
    log_error "--apply 模式必须配合 --plan-file <path>"
    harness_exit 3
fi

# ============================================================================
# 前置检查
# ============================================================================
step_begin "阶段 0: 前置检查"

KERNEL_OK=false
AOSP_OK=false
if [ -d "$KERNEL_WS/.git" ]; then
    KERNEL_OK=true
    log_info "Kernel workspace: $KERNEL_WS"
fi
if [ -d "$AOSP_WS/.repo" ]; then
    AOSP_OK=true
    log_info "AOSP workspace:   $AOSP_WS"
fi

if [ "$KERNEL_OK" = false ] && [ "$AOSP_OK" = false ]; then
    log_error "未找到有效的 workspace（检查 KERNEL_WS/AOSP_WS 环境变量）"
    step_end 1
    harness_exit 3
fi
log_info "Patch root: $PATCH_ROOT"
step_end 0

# ============================================================================
# 扫描函数（kernel）
# ============================================================================

# 全局 MATCH 计数（gen_plan 重置）
G_MATCH_MODIFIED=0
G_MATCH_NEW=0

# scan_kernel_modified — 比对 patchs/modified/*.diff vs workspace 当前 diff
scan_kernel_modified() {
    local out="$1"
    [ ! -d "$PATCH_ROOT/kernel/modified" ] && return
    cd "$KERNEL_WS" || { log_error "无法进入 $KERNEL_WS"; return 1; }
    local BASE; BASE=$(harness_find_upstream_base)
    if [ -z "$BASE" ]; then
        harness_report_no_upstream "kernel"
        return 1
    fi

    while IFS= read -r -d '' dfile; do
        local rel="${dfile#$PATCH_ROOT/kernel/modified/}"; rel="${rel%.diff}"
        local tmp; tmp=$(harness_tmp_file "kd.diff"); TMP_FILES+=("$tmp")
        git diff "$BASE" -- "$rel" > "$tmp" 2>/dev/null
        if [ ! -s "$tmp" ]; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "kernel" "$rel" "checkout" "workspace 已恢复 upstream，缺失 patchs 定制" >> "$out"
        elif diff_normalized "$tmp" "$dfile"; then
            G_MATCH_MODIFIED=$((G_MATCH_MODIFIED + 1))
        else
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "kernel" "$rel" "checkout" "workspace diff 与 patchs 不一致" >> "$out"
        fi
    done < <(find "$PATCH_ROOT/kernel/modified" -name '*.diff' -print0 2>/dev/null || true)
}

# scan_kernel_new — 比对 patchs/new/ 文件 vs workspace 文件
scan_kernel_new() {
    local out="$1"
    [ ! -d "$PATCH_ROOT/kernel/new" ] && return
    cd "$KERNEL_WS" || return 1
    while IFS= read -r -d '' pfile; do
        local rel="${pfile#$PATCH_ROOT/kernel/new/}"
        local src="$KERNEL_WS/$rel"
        if [ ! -f "$src" ]; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "NEW-MISMATCH" "kernel" "$rel" "restore" "workspace 缺失" >> "$out"
        elif ! diff -q "$src" "$pfile" >/dev/null 2>&1; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "NEW-MISMATCH" "kernel" "$rel" "restore" "内容与 patchs 不一致" >> "$out"
        else
            G_MATCH_NEW=$((G_MATCH_NEW + 1))
        fi
    done < <(find "$PATCH_ROOT/kernel/new" -type f -print0 2>/dev/null || true)
}

# scan_extra_kernel — workspace 改动 − patchs 覆盖集合 = EXTRA
scan_extra_kernel() {
    local out="$1"
    cd "$KERNEL_WS" || return 1
    local BASE; BASE=$(harness_find_upstream_base)
    if [ -z "$BASE" ]; then
        harness_report_no_upstream "kernel"
        return 1
    fi
    local cov; cov=$(coverage_kernel | sort -u)
    local ws_changes; ws_changes=$( { git diff "$BASE" --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | sort -u)
    local extra; extra=$(comm -23 <(echo "$ws_changes") <(echo "$cov") | grep -v '^$')

    while IFS= read -r f; do
        [ -z "$f" ] && continue
        _is_excluded "$f" && continue
        if git cat-file -e "$BASE:$f" 2>/dev/null; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-MODIFIED" "kernel" "$f" "revert" "未归档的 upstream 文件改动" >> "$out"
        elif git ls-files --error-unmatch "$f" 2>/dev/null; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-NEW-TRACKED" "kernel" "$f" "revert" "未归档 tracked 新文件" >> "$out"
        else
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-NEW-UNTRACKED" "kernel" "$f" "revert" "未归档 untracked 新文件" >> "$out"
        fi
    done <<< "$extra"
}

# ============================================================================
# 扫描函数（aosp）
# ============================================================================

# scan_aosp_modified — 遍历 repo 项目，比对 patchs/aosp/modified/<proj>/*.diff
scan_aosp_modified() {
    local out="$1"
    [ ! -d "$PATCH_ROOT/aosp/modified" ] && return
    [ ! -f "$AOSP_WS/.repo/project.list" ] && return

    while IFS= read -r proj; do
        [ -z "$proj" ] && continue
        [ ! -d "$PATCH_ROOT/aosp/modified/$proj" ] && continue
        [ ! -d "$AOSP_WS/$proj/.git" ] && continue
        cd "$AOSP_WS/$proj" || continue
        local BASE; BASE=$(harness_find_upstream_base)
        if [ -z "$BASE" ]; then
            harness_report_no_upstream "aosp:$proj"
            return 1
        fi

        while IFS= read -r -d '' dfile; do
            local rel="${dfile#$PATCH_ROOT/aosp/modified/$proj/}"; rel="${rel%.diff}"
            local tmp; tmp=$(harness_tmp_file "ad.diff"); TMP_FILES+=("$tmp")
            git diff "$BASE" -- "$rel" > "$tmp" 2>/dev/null
            if [ ! -s "$tmp" ]; then
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "aosp:$proj" "$rel" "checkout" "workspace 已恢复 upstream，缺失 patchs 定制" >> "$out"
            elif diff_normalized "$tmp" "$dfile"; then
                G_MATCH_MODIFIED=$((G_MATCH_MODIFIED + 1))
            else
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "aosp:$proj" "$rel" "checkout" "workspace diff 与 patchs 不一致" >> "$out"
            fi
        done < <(find "$PATCH_ROOT/aosp/modified/$proj" -name '*.diff' -print0 2>/dev/null || true)
    done < <(sort "$AOSP_WS/.repo/project.list")
}

# scan_aosp_new — 遍历 repo 项目 + 非 repo 目录的 new 文件
scan_aosp_new() {
    local out="$1"
    [ ! -d "$PATCH_ROOT/aosp/new" ] && return
    [ ! -f "$AOSP_WS/.repo/project.list" ] && return

    # repo 项目的 new
    while IFS= read -r proj; do
        [ -z "$proj" ] && continue
        [ ! -d "$PATCH_ROOT/aosp/new/$proj" ] && continue
        while IFS= read -r -d '' pfile; do
            local rel="${pfile#$PATCH_ROOT/aosp/new/$proj/}"
            local src="$AOSP_WS/$proj/$rel"
            if [ ! -f "$src" ]; then
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "NEW-MISMATCH" "aosp:$proj" "$rel" "restore" "workspace 缺失" >> "$out"
            elif ! diff -q "$src" "$pfile" >/dev/null 2>&1; then
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "NEW-MISMATCH" "aosp:$proj" "$rel" "restore" "内容与 patchs 不一致" >> "$out"
            else
                G_MATCH_NEW=$((G_MATCH_NEW + 1))
            fi
        done < <(find "$PATCH_ROOT/aosp/new/$proj" -type f -print0 2>/dev/null || true)
    done < <(sort "$AOSP_WS/.repo/project.list")

    # 非 repo 目录的 new（用 comm 求差集：全部 new − repo 项目 new）
    local all_new repo_new non_repo_new
    all_new=$(find "$PATCH_ROOT/aosp/new" -type f 2>/dev/null | sed "s|$PATCH_ROOT/aosp/new/||" | sort -u)
    repo_new=""
    while IFS= read -r proj; do
        [ -z "$proj" ] && continue
        [ -d "$PATCH_ROOT/aosp/new/$proj" ] && repo_new="$repo_new$(find "$PATCH_ROOT/aosp/new/$proj" -type f 2>/dev/null | sed "s|$PATCH_ROOT/aosp/new/||")"
    done < <(sort "$AOSP_WS/.repo/project.list")
    non_repo_new=$(comm -23 <(echo "$all_new") <(echo "$repo_new" | sort -u) | grep -v '^$')

    while IFS= read -r rel; do
        [ -z "$rel" ] && continue
        local src="$AOSP_WS/$rel"
        local pfile="$PATCH_ROOT/aosp/new/$rel"
        if [ ! -f "$src" ]; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "NEW-MISMATCH" "aosp" "$rel" "restore" "workspace 缺失（非 repo）" >> "$out"
        elif ! diff -q "$src" "$pfile" >/dev/null 2>&1; then
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "NEW-MISMATCH" "aosp" "$rel" "restore" "内容不一致（非 repo）" >> "$out"
        else
            G_MATCH_NEW=$((G_MATCH_NEW + 1))
        fi
    done <<< "$non_repo_new"
}

# scan_extra_aosp — 遍历 repo 项目的 EXTRA + 非 repo 目录的 EXTRA
scan_extra_aosp() {
    local out="$1"
    [ ! -f "$AOSP_WS/.repo/project.list" ] && return

    # repo 项目
    while IFS= read -r proj; do
        [ -z "$proj" ] && continue
        [ ! -d "$AOSP_WS/$proj/.git" ] && continue
        cd "$AOSP_WS/$proj" || continue
        local BASE; BASE=$(harness_find_upstream_base)
        [ -z "$BASE" ] && continue
        local cov; cov=$(coverage_aosp_project "$proj" | sort -u)
        local ws_changes; ws_changes=$( { git diff "$BASE" --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | sort -u)
        local extra; extra=$(comm -23 <(echo "$ws_changes") <(echo "$cov") | grep -v '^$')

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            _is_excluded "$f" && continue
            if git cat-file -e "$BASE:$f" 2>/dev/null; then
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-MODIFIED" "aosp:$proj" "$f" "revert" "未归档的 upstream 文件改动" >> "$out"
            elif git ls-files --error-unmatch "$f" 2>/dev/null; then
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-NEW-TRACKED" "aosp:$proj" "$f" "revert" "未归档 tracked 新文件" >> "$out"
            else
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-NEW-UNTRACKED" "aosp:$proj" "$f" "revert" "未归档 untracked 新文件" >> "$out"
            fi
        done <<< "$extra"
    done < <(sort "$AOSP_WS/.repo/project.list")

    # 非 repo 目录的 EXTRA
    scan_extra_aosp_non_repo "$out"
}

# scan_extra_aosp_non_repo — 非 repo 顶层目录的未归档文件
scan_extra_aosp_non_repo() {
    local out="$1"
    cd "$AOSP_WS" || return 1
    local cov_all; cov_all=$(find "$PATCH_ROOT/aosp/new" -type f 2>/dev/null | sed "s|$PATCH_ROOT/aosp/new/||" | sort -u)

    local d rel bn
    for d in "$AOSP_WS"/*/; do
        [ -d "$d" ] || continue
        rel="${d#$AOSP_WS/}"; rel="${rel%/}"
        bn=$(basename "$rel")
        [[ "$bn" =~ ^\. ]] && continue
        [[ "$bn" =~ $HARNESS_EXCLUDE_DIR_RE ]] && continue
        # 跳过 repo project（精确匹配顶层名 或 有以 rel/ 开头的项目）
        local top_proj; top_proj=$(sort "$AOSP_WS/.repo/project.list" | cut -d/ -f1 | sort -u)
        echo "$top_proj" | grep -Fxq "$bn" && continue
        sort "$AOSP_WS/.repo/project.list" | grep -q "^${rel}/" && continue

        # 非 repo 目录 → 遍历其中不在 patchs 覆盖集合的文件
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            _is_excluded "$f" && continue
            echo "$cov_all" | grep -Fxq "$f" && continue
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "EXTRA-NEW-UNTRACKED" "aosp:$rel" "$f" "revert" "非 repo 目录未归档文件" >> "$out"
        done < <(find "$rel" -type f 2>/dev/null | sort -u)
    done
}

# ============================================================================
# coverage 函数（patchs 覆盖集合，用于 EXTRA 判定）
# ============================================================================
coverage_kernel() {
    local rel
    while IFS= read -r -d '' pfile; do
        rel="${pfile#$PATCH_ROOT/kernel/modified/}"; rel="${rel%.diff}"
        echo "$rel"
    done < <(find "$PATCH_ROOT/kernel/modified" -name '*.diff' -print0 2>/dev/null || true)
    while IFS= read -r -d '' pfile; do
        rel="${pfile#$PATCH_ROOT/kernel/new/}"
        echo "$rel"
    done < <(find "$PATCH_ROOT/kernel/new" -type f -print0 2>/dev/null || true)
}

coverage_aosp_project() {
    local proj="$1"
    local rel
    while IFS= read -r -d '' pfile; do
        rel="${pfile#$PATCH_ROOT/aosp/modified/$proj/}"; rel="${rel%.diff}"
        echo "$rel"
    done < <(find "$PATCH_ROOT/aosp/modified/$proj" -name '*.diff' -print0 2>/dev/null || true)
    while IFS= read -r -d '' pfile; do
        rel="${pfile#$PATCH_ROOT/aosp/new/$proj/}"
        echo "$rel"
    done < <(find "$PATCH_ROOT/aosp/new/$proj" -type f -print0 2>/dev/null || true)
}

# ============================================================================
# gen_plan — 阶段1：扫描 + 生成 plan 文件
# ============================================================================

# gen_plan — 生成完整 plan（含 log 输出，用于阶段 1）
# 返回: 0=成功; 1=扫描阶段失败（upstream 缺失等，不输出 plan）
gen_plan() {
    local out="$1"
    G_MATCH_MODIFIED=0
    G_MATCH_NEW=0

    # 先写入临时文件，扫描全部成功后才落盘到 out（避免部分 plan 泄漏）
    local tmp; tmp=$(harness_tmp_file "gen-plan.tsv"); TMP_FILES+=("$tmp")
    {
        echo "# REVERT-PLAN generated at $(date +%FT%T)"
        echo "# 格式: <标记>\t<类别>\t<项目>\t<相对路径>\t<动作>\t<差异摘要>"
        echo "# 标记: + = 选中执行, - = 不执行"
        echo "# 类别: MODIFIED-DIVERGED | NEW-MISMATCH | EXTRA-MODIFIED | EXTRA-NEW-TRACKED | EXTRA-NEW-UNTRACKED"
        echo "# 动作: checkout | checkout-only | restore | revert | skip | stash-hint"
        echo ""
    } > "$tmp"

    local scan_rc=0
    log_info "扫描 kernel"
    if [ "$KERNEL_OK" = true ]; then
        scan_kernel_modified "$tmp" || scan_rc=1
        scan_kernel_new "$tmp" || scan_rc=1
        scan_extra_kernel "$tmp" || scan_rc=1
    fi

    log_info "扫描 aosp"
    if [ "$AOSP_OK" = true ]; then
        scan_aosp_modified "$tmp" || scan_rc=1
        scan_aosp_new "$tmp" || scan_rc=1
        scan_extra_aosp "$tmp" || scan_rc=1
    fi

    # 扫描阶段任一失败（如 upstream 缺失）→ 不输出 plan，返回 1
    if [ "$scan_rc" -ne 0 ]; then
        log_error "扫描阶段失败，不输出 plan"
        return 1
    fi

    cp -f "$tmp" "$out"

    local total; total=$(grep -c '^[-+]' "$out" 2>/dev/null || true); total=${total:-0}
    log_info "扫描完成"
    log_info "MODIFIED-MATCH: $G_MATCH_MODIFIED 个文件已是 patchs 状态（不列入 plan）"
    log_info "NEW-MATCH: $G_MATCH_NEW 个文件已是 patchs 状态（不列入 plan）"
    log_info "需确认条目: $total 个（详见 plan 文件）"
    log_info "Plan 文件: $out"
    return 0
}

# gen_plan_silent — 生成 plan（无 log，用于 verify 阶段内部调用）
gen_plan_silent() {
    local out="$1"
    G_MATCH_MODIFIED=0
    G_MATCH_NEW=0
    : > "$out"

    if [ "$KERNEL_OK" = true ]; then
        scan_kernel_modified "$out"
        scan_kernel_new "$out"
        scan_extra_kernel "$out"
    fi
    if [ "$AOSP_OK" = true ]; then
        scan_aosp_modified "$out"
        scan_aosp_new "$out"
        scan_extra_aosp "$out"
    fi
}

# ============================================================================
# apply_plan — 阶段2：执行 plan 中 + 标记的条目
# ============================================================================

# do_checkout_patch — checkout upstream + 按 patchs diff 重新 patch
do_checkout_patch() {
    local proj="$1" rel="$2"
    local ws; ws=$(_parse_proj "$proj")
    local diff_file=""
    case "$proj" in
        kernel)       diff_file="$PATCH_ROOT/kernel/modified/${rel}.diff" ;;
        aosp:*)       diff_file="$PATCH_ROOT/aosp/modified/${proj#aosp:}/${rel}.diff" ;;
        *)            log_error "do_checkout_patch 不支持 proj=$proj"; return 1 ;;
    esac
    [ ! -f "$diff_file" ] && { log_error "patchs diff 不存在: $diff_file"; return 1; }

    cd "$ws" || return 1
    local BASE; BASE=$(harness_find_upstream_base)
    [ -z "$BASE" ] && { harness_report_no_upstream "$proj"; return 1; }
    # 先验证 diff 可应用，再 checkout（避免 BROKEN-DIFF 时 workspace 被半修改导致定制丢失）
    git apply --check "$diff_file" 2>/dev/null || { log_error "BROKEN-DIFF: $diff_file 无法应用到 upstream"; return 1; }
    git checkout "$BASE" -- "$rel" 2>/dev/null || { log_error "checkout 失败: $rel"; return 1; }
    git apply "$diff_file" 2>/dev/null || { log_error "git apply 失败: $diff_file"; return 1; }
    return 0
}

# do_checkout_only — 仅 checkout upstream（移除定制，用于 checkout-only 动作）
do_checkout_only() {
    local proj="$1" rel="$2"
    local ws; ws=$(_parse_proj "$proj")
    cd "$ws" || return 1
    local BASE; BASE=$(harness_find_upstream_base)
    [ -z "$BASE" ] && { harness_report_no_upstream "$proj"; return 1; }
    git checkout "$BASE" -- "$rel" 2>/dev/null || { log_error "checkout 失败: $rel"; return 1; }
    return 0
}

# do_restore — 从 patchs/new 复制文件到 workspace
do_restore() {
    local proj="$1" rel="$2"
    local ws; ws=$(_parse_proj "$proj")
    local pfile=""
    case "$proj" in
        kernel) pfile="$PATCH_ROOT/kernel/new/${rel}" ;;
        aosp)   pfile="$PATCH_ROOT/aosp/new/${rel}" ;;
        aosp:*) pfile="$PATCH_ROOT/aosp/new/${proj#aosp:}/${rel}" ;;
        *)      log_error "do_restore 不支持 proj=$proj"; return 1 ;;
    esac
    [ ! -f "$pfile" ] && { log_error "patchs 源文件不存在: $pfile"; return 1; }
    mkdir -p "$(dirname "$ws/$rel")"
    cp "$pfile" "$ws/$rel" || { log_error "cp 失败: $pfile → $ws/$rel"; return 1; }
    return 0
}

# do_revert_extra — 回退 EXTRA 类条目
do_revert_extra() {
    local proj="$1" rel="$2" category="$3"
    local ws; ws=$(_parse_proj "$proj")
    case "$category" in
        EXTRA-MODIFIED|EXTRA-NEW-TRACKED)
            cd "$ws" || return 1
            local BASE; BASE=$(harness_find_upstream_base)
            [ -z "$BASE" ] && { harness_report_no_upstream "$proj"; return 1; }
            git checkout "$BASE" -- "$rel" 2>/dev/null || { log_error "checkout 失败: $rel"; return 1; }
            ;;
        EXTRA-NEW-UNTRACKED)
            local target; target=$(_resolve_workspace_target "$proj" "$rel")
            [ -z "$target" ] && { log_error "无法解析路径: $proj/$rel"; return 1; }
            rm -f "$target" || { log_error "rm 失败: $target"; return 1; }
            ;;
        *)
            log_error "未知 EXTRA 类别: $category"; return 1
            ;;
    esac
    return 0
}

# apply_plan — 读 plan，执行 + 标记条目，失败立即停止
apply_plan() {
    local plan="$1"
    [ ! -f "$plan" ] && { log_error "plan 文件不存在: $plan"; return 1; }

    local selected; selected=$(grep -c '^+' "$plan" 2>/dev/null || true); selected=${selected:-0}
    if [ "$selected" -eq 0 ]; then
        log_info "plan 中无选中条目（+ 标记），无需执行"
        return 0
    fi

    log_info "执行回退计划 ($selected 条)"
    log_info "Plan 文件: $plan"
    log_warn "如 workspace 有 staged 改动（git index），checkout 可能受影响；建议先 git stash"

    local applied=0
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*# ]] && continue
        [[ "$line" =~ ^[[:space:]]*$ ]] && continue
        local mark="${line%%$'\t'*}"
        [ "$mark" != "+" ] && continue

        local rest="${line#*$'\t'}"
        local category proj rel action summary
        IFS=$'\t' read -r category proj rel action summary <<< "$rest"

        # 统一错误处理：do_* 失败时由 on_err --continue 记录现场（lineno/cmd/stack/step_ctx）后 return 1
        local rc=0
        case "$action" in
            checkout)
                log_info "  [CHECKOUT] $proj:$rel"
                do_checkout_patch "$proj" "$rel"; rc=$?
                [ $rc -ne 0 ] && { on_err --continue "${BASH_LINENO[0]}" "do_checkout_patch $proj:$rel" $rc; return 1; }
                applied=$((applied + 1))
                ;;
            checkout-only)
                log_info "  [CHECKOUT-ONLY] $proj:$rel"
                do_checkout_only "$proj" "$rel"; rc=$?
                [ $rc -ne 0 ] && { on_err --continue "${BASH_LINENO[0]}" "do_checkout_only $proj:$rel" $rc; return 1; }
                applied=$((applied + 1))
                ;;
            restore)
                log_info "  [RESTORE] $proj:$rel"
                do_restore "$proj" "$rel"; rc=$?
                [ $rc -ne 0 ] && { on_err --continue "${BASH_LINENO[0]}" "do_restore $proj:$rel" $rc; return 1; }
                applied=$((applied + 1))
                ;;
            revert)
                log_info "  [REVERT] $category $proj:$rel"
                do_revert_extra "$proj" "$rel" "$category"; rc=$?
                [ $rc -ne 0 ] && { on_err --continue "${BASH_LINENO[0]}" "do_revert_extra $category $proj:$rel" $rc; return 1; }
                applied=$((applied + 1))
                ;;
            skip|stash-hint)
                ;;  # 跳过
            *)
                log_warn "  未知动作 '$action'，跳过: $proj:$rel"
                ;;
        esac
    done < "$plan"

    log_info "执行完成"
    log_info "已执行: $applied 条"
    log_result "APPLY 结果" "applied=$applied" "plan=$plan"
    return 0
}

# ============================================================================
# verify_after_apply — 阶段3：全量重跑落盘校验
# ============================================================================

verify_after_apply() {
    local orig_plan="$1"
    local verify_out; verify_out=$(harness_tmp_file "verify.tsv")

    log_info "落盘校验（全量重跑）"

    # 生成新的扫描结果（静默）
    local new_plan; new_plan=$(harness_tmp_file "verify-plan.tsv"); TMP_FILES+=("$new_plan")
    gen_plan_silent "$new_plan"

    # 构建 key 集合（proj<TAB>rel）
    local orig_exec orig_skip new_diverged
    orig_exec=$(grep '^+' "$orig_plan" 2>/dev/null | cut -f3,4 | sort -u | grep -v '^$')
    orig_skip=$(grep '^-' "$orig_plan" 2>/dev/null | cut -f3,4 | sort -u | grep -v '^$')
    new_diverged=$(grep '^[-+]' "$new_plan" 2>/dev/null | cut -f3,4 | sort -u | grep -v '^$')

    # 四类分类
    local fixed kept residual newdiff
    {
        echo "# VERIFY generated at $(date +%FT%T)"
        echo "# FIXED    = 原执行条目现已 MATCH（回退生效）"
        echo "# KEPT     = 原 skip 条目仍偏离（用户主动保留，不算失败）"
        echo "# RESIDUAL = 原执行条目仍偏离（回退未生效，真正失败）"
        echo "# NEW-DIFF = 新出现的偏离（需排查）"
        echo ""

        # RESIDUAL: orig_exec ∩ new_diverged
        comm -12 <(echo "$orig_exec") <(echo "$new_diverged") 2>/dev/null | while IFS= read -r k; do
            [ -z "$k" ] && continue
            printf 'RESIDUAL\t%s\n' "$k"
        done
        # KEPT: orig_skip ∩ new_diverged
        comm -12 <(echo "$orig_skip") <(echo "$new_diverged") 2>/dev/null | while IFS= read -r k; do
            [ -z "$k" ] && continue
            printf 'KEPT\t%s\n' "$k"
        done
        # FIXED: orig_exec − new_diverged
        comm -23 <(echo "$orig_exec") <(echo "$new_diverged") 2>/dev/null | while IFS= read -r k; do
            [ -z "$k" ] && continue
            printf 'FIXED\t%s\n' "$k"
        done
        # NEW-DIFF: new_diverged − orig_exec − orig_skip
        comm -23 <(echo "$new_diverged") <(echo -e "$orig_exec\n$orig_skip" | sort -u | grep -v '^$') 2>/dev/null | while IFS= read -r k; do
            [ -z "$k" ] && continue
            printf 'NEW-DIFF\t%s\n' "$k"
        done
    } > "$verify_out"

    fixed=$(grep -c '^FIXED' "$verify_out" 2>/dev/null || true); fixed=${fixed:-0}
    kept=$(grep -c '^KEPT' "$verify_out" 2>/dev/null || true); kept=${kept:-0}
    residual=$(grep -c '^RESIDUAL' "$verify_out" 2>/dev/null || true); residual=${residual:-0}
    newdiff=$(grep -c '^NEW-DIFF' "$verify_out" 2>/dev/null || true); newdiff=${newdiff:-0}

    log_info "FIXED:    $fixed"
    log_info "KEPT:     $kept（用户主动保留，不算失败）"
    log_info "RESIDUAL: $residual"
    log_info "NEW-DIFF: $newdiff"
    log_info "校验文件: $verify_out"
    log_result "VERIFY 结果" "fixed=$fixed" "kept=$kept" "residual=$residual" "new_diff=$newdiff" "verify_file=$verify_out"

    # artifact 归档
    artifact_register "$verify_out" "verify.tsv"

    if [ "$residual" -gt 0 ] || [ "$newdiff" -gt 0 ]; then
        log_error "校验失败：有 RESIDUAL($residual) 或 NEW-DIFF($newdiff)"
        return 1
    fi
    log_info "校验通过"
    return 0
}

# ============================================================================
# 主流程
# ============================================================================

case "$MODE" in
    plan)
        if [ -z "$PLAN_FILE" ]; then
            PLAN_FILE=$(harness_tmp_file "plan.tsv")
        fi
        step_begin "阶段 1: 生成回退计划"
        gen_plan "$PLAN_FILE"
        _PRC=$?
        step_end $_PRC
        # 扫描阶段失败（upstream 缺失等环境错误）→ exit 3
        [ $_PRC -ne 0 ] && { log_error "plan 生成失败"; harness_exit 3; }
        # plan 为空 → exit 4（无操作）
        if [ ! -s "$PLAN_FILE" ] || ! grep -q '^[-+]' "$PLAN_FILE" 2>/dev/null; then
            log_info "plan 为空，无操作（exit 4）"
            harness_exit 4
        fi
        # artifact 归档
        artifact_register "$PLAN_FILE" "plan.tsv"
        log_result "PLAN 生成" "plan_file=$PLAN_FILE" "entries=$(grep -c '^[-+]' "$PLAN_FILE" 2>/dev/null || echo 0)"
        ;;
    apply)
        step_begin "阶段 2: 执行回退计划"
        if apply_plan "$PLAN_FILE"; then
            step_end 0
        else
            step_end 1
            log_error "apply 失败，跳过校验"
            harness_exit 1
        fi

        step_begin "阶段 3: 落盘校验"
        verify_after_apply "$PLAN_FILE"
        _VRC=$?
        step_end $_VRC
        # verify 校验失败 → exit 1
        [ $_VRC -ne 0 ] && harness_exit 1
        # artifact 归档（apply 模式下 plan 文件由调用者提供，也归档一份）
        artifact_register "$PLAN_FILE" "plan.tsv"
        ;;
    check-only)
        PLAN_FILE=$(harness_tmp_file "preview-plan.tsv"); TMP_FILES+=("$PLAN_FILE")
        step_begin "阶段 1: 生成回退计划"
        gen_plan "$PLAN_FILE"
        _CRC=$?
        step_end $_CRC
        # check-only 不做 plan 空判断（预览模式，即使为空也输出）
        artifact_register "$PLAN_FILE" "plan.tsv"
        echo ""
        log_info "差异预览"
        cat "$PLAN_FILE"
        ;;
esac

harness_exit 0
