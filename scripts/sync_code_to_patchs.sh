#!/bin/bash
set -uo pipefail

# ============================================================================
# sync_code_to_patchs.sh — workspace → patchs/rpi5 一键同步脚本
# 规则详见: rules/sync_code_to_patchs.md
# 用法:    bash scripts/sync_code_to_patchs.sh [--check-only]
# ============================================================================

# --- Configuration ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_ROOT="$SCRIPT_DIR/../patchs/rpi5"
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

# --- Colors -----------------------------------------------------------------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# --- Helpers ----------------------------------------------------------------
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}========== $1 ==========${NC}"; }

print_ok()   { echo -e "  ${GREEN}OK${NC}   $1"; TOTAL_OK=$((TOTAL_OK + 1)); }
print_miss() { echo -e "  ${RED}MISS${NC} $1"; TOTAL_MISS=$((TOTAL_MISS + 1)); }
print_skip() { echo -e "  ${YELLOW}SKIP${NC} $1"; TOTAL_SKIP=$((TOTAL_SKIP + 1)); }
print_stale(){ echo -e "  ${YELLOW}STALE${NC} $1"; TOTAL_STALE=$((TOTAL_STALE + 1)); }

# 自动检测 upstream merge-base
find_upstream_base() {
    local base="" m_ref current_branch suffix ref
    m_ref=$(git for-each-ref refs/remotes/m/ --format='%(refname:short)' 2>/dev/null | head -1)
    [ -n "$m_ref" ] && base=$(git merge-base HEAD "$m_ref" 2>/dev/null || true)
    if [ -z "$base" ]; then
        current_branch=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
        if [ "$current_branch" != "HEAD" ] && [ -n "$current_branch" ]; then
            suffix="${current_branch#*/}"
            while IFS= read -r ref; do
                [[ "$ref" == */"$suffix" ]] && { base=$(git merge-base HEAD "$ref" 2>/dev/null || true); [ -n "$base" ] && break; }
            done < <(git for-each-ref refs/remotes/ --format='%(refname:short)' 2>/dev/null)
        fi
    fi
    if [ -z "$base" ]; then
        while IFS= read -r ref; do
            base=$(git merge-base HEAD "$ref" 2>/dev/null || true)
            [ -n "$base" ] && break
        done < <(git for-each-ref refs/remotes/ --format='%(refname:short)' 2>/dev/null)
    fi
    echo "$base"
}

# ============================================================================
# 参数解析
# ============================================================================
CHECK_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --check-only|--dry-run) CHECK_ONLY=true ;;
        -h|--help)
            echo "Usage: bash scripts/sync_code_to_patchs.sh [--check-only]"
            echo "  --check-only  仅扫描和验证，不执行归档"
            exit 0 ;;
        *) log_error "未知参数: $arg"; exit 1 ;;
    esac
done

# ============================================================================
# 前置检查
# ============================================================================
log_step "前置检查"

KERNEL_OK=false
AOSP_OK=false
[ -d "$KERNEL_WS/.git" ] && KERNEL_OK=true && log_info "Kernel workspace: $KERNEL_WS"
[ -d "$AOSP_WS/.repo"  ] && AOSP_OK=true   && log_info "AOSP workspace:   $AOSP_WS"

if [ "$KERNEL_OK" = false ] && [ "$AOSP_OK" = false ]; then
    log_error "未找到有效的 workspace"
    exit 1
fi
log_info "模式:       $([ "$CHECK_ONLY" = true ] && echo '仅检查' || echo '同步归档')"
log_info "Patch root: $PATCH_ROOT"

# ============================================================================
# Step 0: 发现非 repo 目录 + 获取改动项目列表
# ============================================================================
NON_REPO_DIRS=()
REPO_PROJECT_LIST=""
AOSP_CHANGED_PROJECTS=""

if [ "$AOSP_OK" = true ]; then
    log_step "Step 0: 扫描 workspace"

    # 读取 project.list（<1ms），替代 repo forall（400ms）
    REPO_LIST_FILE=$(mktemp /tmp/sync_repolist.XXXXXX)
    trap 'rm -f "$REPO_LIST_FILE"' EXIT
    if [ -f "$AOSP_WS/.repo/project.list" ]; then
        sort "$AOSP_WS/.repo/project.list" > "$REPO_LIST_FILE"
    else
        (cd "$AOSP_WS" && repo forall -c 'echo $REPO_PATH' 2>/dev/null | sort) > "$REPO_LIST_FILE"
    fi

    # 并行扫描有改动的 repo 项目（xargs -P 比 repo status 快 60%）
    AOSP_CHANGED_PROJECTS=$(cd "$AOSP_WS" && cat "$REPO_LIST_FILE" | xargs -P "$(nproc)" -I {} bash -c '
        [ -d "{}/.git" ] || exit 0
        cd "{}" 2>/dev/null || exit 0
        test -n "$(git diff --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null)" && echo "{}"
    ' 2>/dev/null)
    log_info "有改动的 repo 项目: $(echo "$AOSP_CHANGED_PROJECTS" | grep -c '.' 2>/dev/null || echo 0)"

    # 发现非 repo 目录（用 grep -F 批量匹配，避免逐行 while read）
    _discover_non_repo() {
        local prefix="$1"
        local search_dir="$AOSP_WS/${prefix}"
        local d rel bn resolved
        for d in "$search_dir"*/; do
            [ -d "$d" ] || continue
            rel="${d#$AOSP_WS/}"; rel="${rel%/}"
            bn=$(basename "$rel")
            [[ "$bn" =~ ^\. ]] && continue
            [[ "$bn" =~ $EXCLUDE_DIR_RE ]] && continue

            # 判断此目录是否属于某个 repo 项目：精确匹配（rel == proj）或被包含（rel 在 proj 下）
            # grep -Fxq 精确匹配整行；grep -Fq "${rel}/" 匹配以 rel/ 开头的行
            if grep -Fxq "$rel" "$REPO_LIST_FILE" 2>/dev/null; then continue; fi
            if grep -E "^${rel}/" "$REPO_LIST_FILE" >/dev/null 2>&1; then
                # 有以 rel/ 开头的 repo 项目 → rel 本身不是 repo 项目，但其子目录有
                _discover_non_repo "$rel/"
                continue
            fi

            # 符号链接解析（如 build/core → build/make/core）
            if [ -L "$AOSP_WS/$rel" ]; then
                resolved=$(realpath --relative-to="$AOSP_WS" "$AOSP_WS/$rel" 2>/dev/null || true)
                if [ -n "$resolved" ]; then
                    # 精确匹配或作为某个 repo 项目的子目录
                    if grep -Fxq "$resolved" "$REPO_LIST_FILE" 2>/dev/null; then continue; fi
                    # 检查 resolved 的任何父目录是否是 repo 项目
                    local _parent="$resolved"
                    while [[ "$_parent" == */* ]]; do
                        _parent="${_parent%/*}"
                        if grep -Fxq "$_parent" "$REPO_LIST_FILE" 2>/dev/null; then
                            continue 2
                        fi
                    done
                fi
            fi

            # 非 repo 目录
            NON_REPO_DIRS+=("$rel")
            log_info "发现非 repo 目录: $rel"
        done
    }
    _discover_non_repo ""
fi

# ============================================================================
# Step 1: Kernel 同步
# ============================================================================
if [ "$KERNEL_OK" = true ]; then
    log_step "Step 1: Kernel 同步"
    cd "$KERNEL_WS"

    BASE=$(find_upstream_base)
    if [ -z "$BASE" ]; then
        log_error "无法确定 kernel upstream base commit"
        exit 1
    fi
    log_info "Upstream base: $(git log --oneline -1 "$BASE" | head -1)"

    echo "--- Modified ---"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        target="$PATCH_ROOT/kernel/modified/${f}.diff"
        if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; git diff "$BASE" -- "$f" > "$target"; fi
        [ -f "$target" ] && print_ok "kernel/modified/${f}.diff" || print_miss "kernel/modified/${f}.diff"
    done < <(git diff "$BASE" --diff-filter=M --name-only 2>/dev/null | grep -vE "$EXCLUDE_RE")

    echo "--- New (tracked) ---"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        target="$PATCH_ROOT/kernel/new/${f}"
        if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; cp "$f" "$target"; fi
        [ -f "$target" ] && print_ok "kernel/new/${f}" || print_miss "kernel/new/${f}"
    done < <(git diff "$BASE" --diff-filter=ACR --name-only 2>/dev/null | grep -vE "$EXCLUDE_RE")

    echo "--- New (untracked) ---"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        target="$PATCH_ROOT/kernel/new/${f}"
        if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; cp "$f" "$target"; fi
        [ -f "$target" ] && print_ok "kernel/new/${f}" || print_miss "kernel/new/${f}"
    done < <(git ls-files --others --exclude-standard 2>/dev/null | grep -vE "$EXCLUDE_RE")

    # 编译产物汇总
    skip_count=$( { git diff "$BASE" --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | grep -cE "$EXCLUDE_RE" || true )
    [ "$skip_count" -gt 0 ] && print_skip "kernel: ${skip_count} 个编译产物"
fi

# ============================================================================
# Step 2: AOSP 同步
# ============================================================================
if [ "$AOSP_OK" = true ]; then
    log_step "Step 2: AOSP 同步"
    cd "$AOSP_WS"

    for proj_dir in $AOSP_CHANGED_PROJECTS; do
        cd "$AOSP_WS/$proj_dir"
        BASE=$(find_upstream_base)
        [ -z "$BASE" ] && BASE=$(git rev-parse HEAD 2>/dev/null)

        all_files=$( { git diff "$BASE" --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } )
        real_count=$(echo "$all_files" | grep -vE "$EXCLUDE_RE" | grep -c '.' || true)
        [ -z "$real_count" ] && real_count=0

        if [ "$real_count" -eq 0 ]; then
            skip_count=$(echo "$all_files" | grep -cE "$EXCLUDE_RE" || true)
            [ -z "$skip_count" ] && skip_count=0
            [ "$skip_count" -gt 0 ] && print_skip "${proj_dir}: ${skip_count} 个编译产物"
            cd "$AOSP_WS"; continue
        fi

        echo "--- $proj_dir ---"

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            target="$PATCH_ROOT/aosp/modified/${proj_dir}/${f}.diff"
            if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; git diff "$BASE" -- "$f" > "$target"; fi
            [ -f "$target" ] && print_ok "aosp/modified/${proj_dir}/${f}.diff" || print_miss "aosp/modified/${proj_dir}/${f}.diff"
        done < <(git diff "$BASE" --diff-filter=M --name-only 2>/dev/null | grep -vE "$EXCLUDE_RE")

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            target="$PATCH_ROOT/aosp/new/${proj_dir}/${f}"
            if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; cp "$f" "$target"; fi
            [ -f "$target" ] && print_ok "aosp/new/${proj_dir}/${f}" || print_miss "aosp/new/${proj_dir}/${f}"
        done < <(git diff "$BASE" --diff-filter=ACR --name-only 2>/dev/null | grep -vE "$EXCLUDE_RE")

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            target="$PATCH_ROOT/aosp/new/${proj_dir}/${f}"
            if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; cp "$f" "$target"; fi
            [ -f "$target" ] && print_ok "aosp/new/${proj_dir}/${f}" || print_miss "aosp/new/${proj_dir}/${f}"
        done < <(git ls-files --others --exclude-standard 2>/dev/null | grep -vE "$EXCLUDE_RE")

        skip_count=$(echo "$all_files" | grep -cE "$EXCLUDE_RE" || true)
        [ -z "$skip_count" ] && skip_count=0
        [ "$skip_count" -gt 0 ] && print_skip "${proj_dir}: ${skip_count} 个编译产物"
        cd "$AOSP_WS"
    done

    # 非 repo 目录
    if [ ${#NON_REPO_DIRS[@]} -gt 0 ]; then
        echo "--- 非 repo 目录 ---"
        for nr_dir in "${NON_REPO_DIRS[@]}"; do
            cd "$AOSP_WS"
            [ ! -d "$nr_dir" ] && continue
            while IFS= read -r f; do
                target="$PATCH_ROOT/aosp/new/${f}"
                if [ "$CHECK_ONLY" = false ]; then mkdir -p "$(dirname "$target")"; cp "$AOSP_WS/$f" "$target"; fi
                [ -f "$target" ] && print_ok "aosp/new/${f}" || print_miss "aosp/new/${f}"
            done < <(find "$nr_dir" -type f 2>/dev/null | grep -vE "$EXCLUDE_RE")
        done
    fi
fi

# ============================================================================
# Step 3: 陈旧文件检查（patchs 有，workspace 无）
# ============================================================================
log_step "Step 3: 陈旧文件检查"

check_stale() {
    local subpath="$1" ws="$2" strip_diff="$3"
    local full_dir="$PATCH_ROOT/$subpath"
    [ ! -d "$full_dir" ] && return
    while IFS= read -r -d '' pfile; do
        local rel="${pfile#$full_dir/}"
        [ "$strip_diff" = 1 ] && rel="${rel%.diff}"
        if [ ! -f "$ws/$rel" ]; then
            local suffix=""; [ "$strip_diff" = 1 ] && suffix=".diff"
            print_stale "${subpath}/${rel}${suffix}"
        fi
    done < <(find "$full_dir" -type f -print0 2>/dev/null)
}

if [ "$KERNEL_OK" = true ]; then
    echo "--- Kernel ---"
    check_stale "kernel/modified" "$KERNEL_WS" 1
    check_stale "kernel/new" "$KERNEL_WS" 0
fi

if [ "$AOSP_OK" = true ]; then
    echo "--- AOSP ---"
    check_stale "aosp/modified" "$AOSP_WS" 1
    check_stale "aosp/new" "$AOSP_WS" 0
fi

[ "$TOTAL_STALE" -eq 0 ] && log_info "无陈旧文件"

# ============================================================================
# 总结
# ============================================================================
log_step "同步完成"

echo -e "  ${GREEN}OK${NC}:    $TOTAL_OK 个文件已同步/验证"
echo -e "  ${RED}MISS${NC}:  $TOTAL_MISS 个文件缺失"
[ "$TOTAL_SKIP" -gt 0 ] && echo -e "  ${YELLOW}SKIP${NC}:  $TOTAL_SKIP 项已跳过（编译产物）"
[ "$TOTAL_STALE" -gt 0 ] && echo -e "  ${YELLOW}STALE${NC}: $TOTAL_STALE 个陈旧文件"

if [ "$CHECK_ONLY" = true ]; then log_info "本次为仅检查模式，未执行实际归档操作"; fi
if [ "$TOTAL_MISS" -gt 0 ]; then log_warn "部分文件缺失，请去掉 --check-only 重新执行"; fi
if [ "$TOTAL_STALE" -gt 0 ]; then log_warn "部分 patchs 文件在 workspace 中已不存在，请手动清理"; fi

cat <<'TIP'

下一步：检查上方输出，如有新增/变更条目，更新 patchs/rpi5/README.md 文件映射表。
TIP
