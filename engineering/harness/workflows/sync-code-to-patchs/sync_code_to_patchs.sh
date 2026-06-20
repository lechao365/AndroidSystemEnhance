#!/bin/bash
set -eo pipefail

# ============================================================================
# sync_code_to_patchs.sh — workspace → patchs/rpi5 全量镜像同步脚本
# 规则详见: engineering/harness/workflows/sync-code-to-patchs/WORKFLOW.md
# 用法:    bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]
# 退出码:  0=成功; 1=有MISS(需检查); 3=参数/环境错误
# ============================================================================

# --- 锚点 + 公共库（bootstrap 统一入口）-------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/harness_bootstrap.sh"

PATCH_ROOT="$REPO_ROOT/patchs/rpi5"
KERNEL_WS="${KERNEL_WS:-$HOME/workspace/rpi5-kernel-build/common}"
AOSP_WS="${AOSP_WS:-$HOME/workspace/aosp}"

# --- 接入维测库（模式 B：高密度写操作，fail-fast）---------------------------
harness_init --with-errexit "sync_code_to_patchs"

# --- Counters ---------------------------------------------------------------
TOTAL_OK=0
TOTAL_MISS=0
TOTAL_SKIP=0
TOTAL_STALE=0
TOTAL_PRUNE=0

# Deletion tracking（workspace 中被删除的文件，供 manifest deletions: 记录）
KERNEL_DELETIONS=()
AOSP_DELETIONS=()

# --- 业务输出（薄包装：仅计数 + 终端彩色，日志走 harness_status_emit）-------
print_ok()    { harness_status_emit "OK"    "$1" "${2:-}"; TOTAL_OK=$((TOTAL_OK + 1)); }
print_miss()  { harness_status_emit "MISS"  "$1" "${2:-}"; TOTAL_MISS=$((TOTAL_MISS + 1)); }
print_skip()  { harness_status_emit "SKIP"  "$1" "${2:-}"; TOTAL_SKIP=$((TOTAL_SKIP + 1)); }
print_stale() { harness_status_emit "STALE" "$1" "${2:-}"; TOTAL_STALE=$((TOTAL_STALE + 1)); }
print_prune() { harness_status_emit "PRUNE" "$1" "${2:-}"; TOTAL_PRUNE=$((TOTAL_PRUNE + 1)); }

# ============================================================================
# 参数解析
# ============================================================================
CHECK_ONLY=false
PRUNE=true
for arg in "$@"; do
    case "$arg" in
        --check-only|--dry-run) CHECK_ONLY=true ;;
        --no-prune) PRUNE=false ;;
        -h|--help)
            echo "Usage: bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh [--check-only] [--no-prune]"
            echo "  --check-only  仅扫描和验证，不执行归档（STALE 仅报告）"
            echo "  --no-prune    仅添加/更新，不删除对齐（默认全量镜像含删除）"
            harness_exit 0 ;;
        *) log_error "未知参数: $arg"; harness_exit 3 ;;
    esac
done

# ============================================================================
# 前置检查
# ============================================================================
step_begin "前置检查"

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
    log_error "未找到有效的 workspace"
    harness_exit 3
fi
log_info "模式:       $([ "$CHECK_ONLY" = true ] && echo '仅检查' || echo '同步归档')"
log_info "Patch root: $PATCH_ROOT"
step_end 0

# ============================================================================
# Step 0: 发现非 repo 目录 + 获取改动项目列表
# ============================================================================
NON_REPO_DIRS=()
REPO_PROJECT_LIST=""
AOSP_CHANGED_PROJECTS=""
REPO_LIST_FILE=""

if [ "$AOSP_OK" = true ]; then
    step_begin "Step 0: 扫描 workspace"

    # 读取 project.list（<1ms），替代 repo forall（400ms）
    REPO_LIST_FILE=$(harness_tmp_file "repolist.txt")
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
    ' 2>/dev/null || true)
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
            [[ "$bn" =~ $HARNESS_EXCLUDE_DIR_RE ]] && continue

            # 判断此目录是否属于某个 repo 项目：精确匹配（rel == proj）或被包含（rel 在 proj 下）
            if grep -Fxq "$rel" "$REPO_LIST_FILE" 2>/dev/null; then continue; fi
            if grep -E "^${rel}/" "$REPO_LIST_FILE" >/dev/null 2>&1; then
                _discover_non_repo "$rel/"
                continue
            fi

            # 符号链接解析（如 build/core → build/make/core）
            if [ -L "$AOSP_WS/$rel" ]; then
                resolved=$(realpath --relative-to="$AOSP_WS" "$AOSP_WS/$rel" 2>/dev/null || true)
                if [ -n "$resolved" ]; then
                    if grep -Fxq "$resolved" "$REPO_LIST_FILE" 2>/dev/null; then continue; fi
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
    step_end 0
fi

# ============================================================================
# Step 1: Kernel 同步
# ============================================================================
if [ "$KERNEL_OK" = true ]; then
    step_begin "Step 1: Kernel 同步"
    cd "$KERNEL_WS"

    BASE=$(harness_find_upstream_base)
    if [ -z "$BASE" ]; then
        harness_report_no_upstream "kernel"
        harness_exit 3
    fi
    log_info "Upstream base: $(git log --oneline -1 "$BASE" 2>/dev/null | head -1 || true)"

    # Tracked deletion detection（git diff --diff-filter=D 列出已删除文件）
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        KERNEL_DELETIONS+=("$f")
    done < <(git diff "$BASE" --diff-filter=D --name-only 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

    echo "--- Modified ---"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        target="$PATCH_ROOT/kernel/modified/${f}.diff"
        # 空差异检测（check-only 也判）：git diff --quiet 退出码 0 = 无差异 = workspace 已恢复上游原样
        if git diff --quiet "$BASE" -- "$f" 2>/dev/null; then
            if [ "$CHECK_ONLY" = true ]; then
                print_prune "kernel/modified/${f}.diff" "空diff，将清理"
            else
                rm -f "$target"
                print_prune "kernel/modified/${f}.diff" "空diff，已恢复原样"
            fi
            continue
        fi
        if [ "$CHECK_ONLY" = false ]; then
            mkdir -p "$(dirname "$target")"
            git diff "$BASE" -- "$f" > "$target" 2>/dev/null
        fi
        if [ -f "$target" ]; then
            print_ok "kernel/modified/${f}.diff"
        else
            print_miss "kernel/modified/${f}.diff"
        fi
    done < <(git diff "$BASE" --diff-filter=M --name-only 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

    echo "--- New (tracked) ---"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        target="$PATCH_ROOT/kernel/new/${f}"
        if [ "$CHECK_ONLY" = false ]; then
            mkdir -p "$(dirname "$target")"
            cp "$f" "$target"
        fi
        if [ -f "$target" ]; then
            print_ok "kernel/new/${f}"
        else
            print_miss "kernel/new/${f}"
        fi
    done < <(git diff "$BASE" --diff-filter=ACR --name-only 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

    echo "--- New (untracked) ---"
    while IFS= read -r f; do
        [ -z "$f" ] && continue
        target="$PATCH_ROOT/kernel/new/${f}"
        if [ "$CHECK_ONLY" = false ]; then
            mkdir -p "$(dirname "$target")"
            cp "$f" "$target"
        fi
        if [ -f "$target" ]; then
            print_ok "kernel/new/${f}"
        else
            print_miss "kernel/new/${f}"
        fi
    done < <(git ls-files --others --exclude-standard 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

    # 编译产物汇总
    skip_count=$( { git diff "$BASE" --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } | grep -cE "$HARNESS_EXCLUDE_RE" || true )
    [ "$skip_count" -gt 0 ] && print_skip "kernel: ${skip_count} 个编译产物"
    step_end 0
fi

# ============================================================================
# Step 2: AOSP 同步
# ============================================================================
if [ "$AOSP_OK" = true ]; then
    step_begin "Step 2: AOSP 同步"
    cd "$AOSP_WS"

    for proj_dir in $AOSP_CHANGED_PROJECTS; do
        cd "$AOSP_WS/$proj_dir"
        BASE=$(harness_find_upstream_base)
        if [ -z "$BASE" ]; then
            harness_report_no_upstream "aosp:$proj_dir"
            harness_exit 3
        fi

        all_files=$( { git diff "$BASE" --name-only 2>/dev/null; git ls-files --others --exclude-standard 2>/dev/null; } || true)
        real_count=$(echo "$all_files" | grep -vE "$HARNESS_EXCLUDE_RE" | grep -c '.' || true)
        [ -z "$real_count" ] && real_count=0

        if [ "$real_count" -eq 0 ]; then
            skip_count=$(echo "$all_files" | grep -cE "$HARNESS_EXCLUDE_RE" || true)
            [ -z "$skip_count" ] && skip_count=0
            [ "$skip_count" -gt 0 ] && print_skip "${proj_dir}: ${skip_count} 个编译产物"
            cd "$AOSP_WS"; continue
        fi

        echo "--- $proj_dir ---"

        # Tracked deletion detection
        while IFS= read -r f; do
            [ -z "$f" ] && continue
            AOSP_DELETIONS+=("${proj_dir}/${f}")
        done < <(git diff "$BASE" --diff-filter=D --name-only 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            target="$PATCH_ROOT/aosp/modified/${proj_dir}/${f}.diff"
            # 空差异检测（check-only 也判）：git diff --quiet 退出码 0 = 无差异 = workspace 已恢复上游原样
            if git diff --quiet "$BASE" -- "$f" 2>/dev/null; then
                if [ "$CHECK_ONLY" = true ]; then
                    print_prune "aosp/modified/${proj_dir}/${f}.diff" "空diff，将清理"
                else
                    rm -f "$target"
                    print_prune "aosp/modified/${proj_dir}/${f}.diff" "空diff，已恢复原样"
                fi
                continue
            fi
            if [ "$CHECK_ONLY" = false ]; then
                mkdir -p "$(dirname "$target")"
                git diff "$BASE" -- "$f" > "$target" 2>/dev/null
            fi
            if [ -f "$target" ]; then
                print_ok "aosp/modified/${proj_dir}/${f}.diff"
            else
                print_miss "aosp/modified/${proj_dir}/${f}.diff"
            fi
        done < <(git diff "$BASE" --diff-filter=M --name-only 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            target="$PATCH_ROOT/aosp/new/${proj_dir}/${f}"
            if [ "$CHECK_ONLY" = false ]; then
                mkdir -p "$(dirname "$target")"
                cp "$f" "$target"
            fi
            if [ -f "$target" ]; then
                print_ok "aosp/new/${proj_dir}/${f}"
            else
                print_miss "aosp/new/${proj_dir}/${f}"
            fi
        done < <(git diff "$BASE" --diff-filter=ACR --name-only 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

        while IFS= read -r f; do
            [ -z "$f" ] && continue
            target="$PATCH_ROOT/aosp/new/${proj_dir}/${f}"
            if [ "$CHECK_ONLY" = false ]; then
                mkdir -p "$(dirname "$target")"
                cp "$f" "$target"
            fi
            if [ -f "$target" ]; then
                print_ok "aosp/new/${proj_dir}/${f}"
            else
                print_miss "aosp/new/${proj_dir}/${f}"
            fi
        done < <(git ls-files --others --exclude-standard 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)

        skip_count=$(echo "$all_files" | grep -cE "$HARNESS_EXCLUDE_RE" || true)
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
                if [ "$CHECK_ONLY" = false ]; then
                    mkdir -p "$(dirname "$target")"
                    cp "$AOSP_WS/$f" "$target"
                fi
                if [ -f "$target" ]; then
                    print_ok "aosp/new/${f}"
                else
                    print_miss "aosp/new/${f}"
                fi
            done < <(find "$nr_dir" -type f 2>/dev/null | grep -vE "$HARNESS_EXCLUDE_RE" || true)
        done
    fi
    step_end 0
fi

# ============================================================================
# Step 3: 删除对齐（全量镜像）—— patchs 有，workspace 无则删除
# ============================================================================
step_begin "Step 3: 删除对齐（全量镜像）"

# 删除对齐：遍历 patchs 文件，workspace 中无对应源文件则删除（或仅报告）
# subpath: patchs 下子路径（如 kernel/modified）
# ws:      对应 workspace 根目录
# strip_diff: 1=modified(.diff)，需去掉 .diff 后缀再定位源文件
sync_prune() {
    local subpath="$1" ws="$2" strip_diff="$3"
    local full_dir="$PATCH_ROOT/$subpath"
    [ ! -d "$full_dir" ] && return
    while IFS= read -r -d '' pfile; do
        local rel="${pfile#$full_dir/}"
        [ "$strip_diff" = 1 ] && rel="${rel%.diff}"
        if [ ! -f "$ws/$rel" ]; then
            local suffix=""; [ "$strip_diff" = 1 ] && suffix=".diff"
            local label="${subpath}/${rel}${suffix}"
            if [ "$CHECK_ONLY" = true ]; then
                print_stale "${label}" "将删除"
            elif [ "$PRUNE" = true ]; then
                rm -f "$pfile"
                print_prune "$label"
            else
                print_stale "$label"
            fi
        fi
    done < <(find "$full_dir" -type f -print0 2>/dev/null || true)
}

if [ "$KERNEL_OK" = true ]; then
    echo "--- Kernel ---"
    sync_prune "kernel/modified" "$KERNEL_WS" 1
    sync_prune "kernel/new"      "$KERNEL_WS" 0
fi

if [ "$AOSP_OK" = true ]; then
    echo "--- AOSP ---"
    sync_prune "aosp/modified" "$AOSP_WS" 1
    sync_prune "aosp/new"      "$AOSP_WS" 0
fi

# 清理空目录，保持 patchs 目录树干净
if [ "$CHECK_ONLY" = false ] && [ "$PRUNE" = true ]; then
    find "$PATCH_ROOT/kernel/modified" "$PATCH_ROOT/kernel/new" \
         "$PATCH_ROOT/aosp/modified" "$PATCH_ROOT/aosp/new" \
         -type d -empty -delete 2>/dev/null || true
fi

if [ "$TOTAL_PRUNE" -eq 0 ] && [ "$TOTAL_STALE" -eq 0 ]; then
    log_info "无删除对齐项"
fi
step_end 0

# ============================================================================
# Step 4: 更新 manifest.yaml（patch ↔ workspace 结构映射）
# ============================================================================
step_begin "Step 4: 更新 manifest.yaml"

MANIFEST="$PATCH_ROOT/manifest.yaml"

# 生成 manifest 内容到临时文件（section 键只输出一次，modified/new 嵌套其下）
_manifest_emit() {
    local section="$1" ws_root="$2" sub dir files rel src emitted=0
    for sub in modified new; do
        dir="$PATCH_ROOT/${section}/${sub}"
        [ -d "$dir" ] || continue
        files=$(find "$dir" -type f 2>/dev/null | sed "s|^$dir/||" | sort)
        [ -z "$files" ] && continue
        [ "$emitted" = 0 ] && { echo "${section}:"; emitted=1; }
        echo "  ${sub}:"
        while IFS= read -r rel; do
            [ -z "$rel" ] && continue
            src="$rel"
            [ "$sub" = "modified" ] && src="${src%.diff}"
            echo "    - patch: ${section}/${sub}/${rel}"
            echo "      source: ${ws_root}/${src}"
        done <<< "$files"
    done
}

MANIFEST_TMP=$(harness_tmp_file "manifest.yaml")
{
    echo "# Auto-generated by sync_code_to_patchs.sh — patch↔workspace 结构映射。"
    echo "# 禁止手动编辑。README.md（人类可读）由 AI 基于此文件维护。"
    echo ""

    _manifest_emit "kernel" "rpi5-kernel-build/common"
    _manifest_emit "aosp"   "aosp"

    # deletions 段（workspace 中被 git 删除的文件，对应 patchs 中已 prune 的旧 patch）
    if [ ${#KERNEL_DELETIONS[@]} -gt 0 ] || [ ${#AOSP_DELETIONS[@]} -gt 0 ]; then
        echo "deletions:"
        if [ ${#KERNEL_DELETIONS[@]} -gt 0 ]; then
            echo "  kernel:"
            for f in "${KERNEL_DELETIONS[@]}"; do
                echo "    - source: rpi5-kernel-build/common/${f}"
            done
        fi
        if [ ${#AOSP_DELETIONS[@]} -gt 0 ]; then
            echo "  aosp:"
            for f in "${AOSP_DELETIONS[@]}"; do
                echo "    - source: aosp/${f}"
            done
        fi
    fi

    # others（无 workspace 映射）
    if [ -d "$PATCH_ROOT/others" ]; then
        others_files=$(find "$PATCH_ROOT/others" -type f 2>/dev/null | sed "s|^$PATCH_ROOT/others/||" | sort)
        if [ -n "$others_files" ]; then
            echo "others:"
            while IFS= read -r rel; do
                [ -z "$rel" ] && continue
                echo "  - patch: others/${rel}"
                echo "    source: null"
            done <<< "$others_files"
        fi
    fi
} > "$MANIFEST_TMP"

# manifest 内容完整性校验：非空且含合法结构头
if [ ! -s "$MANIFEST_TMP" ]; then
    log_error "manifest 临时文件为空，中止更新"
    rm -f "$MANIFEST_TMP"
    harness_exit 1
fi
if ! grep -qE '^(kernel|aosp|others):' "$MANIFEST_TMP" 2>/dev/null; then
    log_warn "manifest 无任何 section（可能全部为空）"
fi

# 归档本次生成的 manifest 临时文件（供回溯）
artifact_register "$MANIFEST_TMP" "manifest.yaml"

if [ ! -f "$MANIFEST" ] || ! diff -q "$MANIFEST" "$MANIFEST_TMP" >/dev/null 2>&1; then
    if [ "$CHECK_ONLY" = false ]; then
        mv "$MANIFEST_TMP" "$MANIFEST"
        log_info "manifest.yaml 已更新"
    else
        rm -f "$MANIFEST_TMP"
        log_info "manifest.yaml 有变化（仅检查模式，未写入）"
    fi
else
    rm -f "$MANIFEST_TMP"
    log_info "manifest.yaml 无变化"
fi
step_end 0

# ============================================================================
# 归档 repo 项目列表（供回溯）
# ============================================================================
if [ -n "${REPO_LIST_FILE:-}" ] && [ -f "$REPO_LIST_FILE" ]; then
    artifact_register "$REPO_LIST_FILE" "repolist.txt"
    rm -f "$REPO_LIST_FILE"
fi

# ============================================================================
# 汇总
# ============================================================================
step_begin "汇总"
echo "OK: $TOTAL_OK  MISS: $TOTAL_MISS  SKIP: $TOTAL_SKIP  STALE: $TOTAL_STALE  PRUNE: $TOTAL_PRUNE"
[ "$CHECK_ONLY" = true ] && log_info "本次为仅检查模式，未执行实际归档/删除操作"
step_end 0

cat <<'TIP'

下一步：manifest.yaml 已全量重生成（含删除对齐）。README.md 由 AI 自动同步——
  1. 读取 manifest 与当前 README 文件映射表对比，识别新增/删除文件
  2. 新增文件读取对应 diff 生成"改动要点"
  3. 已删除文件（workspace 删除/恢复原样）对应行直接移除，不保留历史
  4. 直接落盘，输出更新摘要（新增 N / 删除 M / 修改要点 K）
判定：仅当存在 MISS 时停下不更新 README；PRUNE（删除对齐/空diff清理）属正常，继续更新。
TIP

if [ "$TOTAL_MISS" -gt 0 ]; then
    log_warn "同步完成，有 $TOTAL_MISS 个 MISS（退出码 1）"
    harness_exit 1
else
    log_info "同步完成，无 MISS"
    harness_exit 0
fi
