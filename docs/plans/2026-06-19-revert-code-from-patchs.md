# revert-code-from-patchs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `revert-code-from-patchs` 工作流，以 `patchs/rpi5` 为已知良好基线，把 `~/workspace` 中偏离 patchs 的部分拉回一致（plan/apply 两阶段 + 落盘校验）。

**Architecture:** 单脚本 `revert_code_from_patchs.sh` 实现 plan（扫描生成 TSV 计划）→ AI 主持逐条确认 → apply（执行选中条目）→ verify（全量重跑校验）三阶段。复用 `sync_code_to_patchs.sh` 的 `find_upstream_base`、排除规则、repo 扫描逻辑（函数复制，非 source——sync 脚本有顶层副作用不可 source）。

**Tech Stack:** Bash 4+（`set -uo pipefail`、进程替换、`comm`/`find -print0`/`git apply --check`），零外部依赖。

**Spec:** `docs/specs/2026-06-19-revert-code-from-patchs-design.md`

---

## File Structure

```
engineering/harness/workflows/revert-code-from-patchs/
├── revert_code_from_patchs.sh   # 主脚本（plan + apply + verify 三阶段，约 600 行）
└── WORKFLOW.md                  # 工作流文档（AI 主持逐条确认的完整规范）
.opencode/commands/revert-code-from-patchs.md  # 命令入口
```

**本计划共 5 个任务**：

| Task | 内容 | 文件 | 依赖 |
|------|------|------|------|
| 1 | 完整脚本（配置+扫描+执行+校验+主流程） | `revert_code_from_patchs.sh` | — |
| 2 | WORKFLOW.md | `WORKFLOW.md` | Task 1 |
| 3 | 命令入口 | `.opencode/commands/revert-code-from-patchs.md` | Task 2 |
| 4 | 静态检查 + 语法验证 | — | Task 1-3 |
| 5 | 集成自测（真实 workspace） | — | Task 4 |

> Task 1 是单文件且内聚度高，必须最先完成。Task 2-3 文件不重叠可并行。Task 4-5 依赖前三者。
>
> **TDD 说明**：bash 运维脚本操作真实 workspace/git，无法轻易 mock。采用 `bash -n`（语法）+ `shellcheck`（静态分析）+ `--check-only`（真实 workspace 只读集成测试）+ `--help`（参数解析）作为验证手段，替代传统单元测试。

---

## Task 1: 创建 revert_code_from_patchs.sh 完整脚本

**Files:**
- Create: `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`

- [ ] **Step 1: 创建脚本目录 + 写入完整脚本**

```bash
mkdir -p engineering/harness/workflows/revert-code-from-patchs
```

用 Write 工具创建 `engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`，完整内容如下（分区注释与 `sync_code_to_patchs.sh` 风格对齐）：

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
# ============================================================================

# --- Configuration ----------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 向上查找项目根（锚点：AGENTS.md）
REPO_ROOT="$SCRIPT_DIR"
while [ "$REPO_ROOT" != "/" ] && [ ! -f "$REPO_ROOT/AGENTS.md" ]; do
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
[ -f "$REPO_ROOT/AGENTS.md" ] || { echo "ERROR: 未找到项目根（AGENTS.md 锚点缺失）" >&2; exit 1; }
PATCH_ROOT="$REPO_ROOT/patchs/rpi5"
KERNEL_WS="${KERNEL_WS:-$HOME/workspace/rpi5-kernel-build/common}"
AOSP_WS="${AOSP_WS:-$HOME/workspace/aosp}"

# 排除规则（与 sync_code_to_patchs.sh 保持一致）
EXCLUDE_RE='\.o$|\.ko$|\.cmd$|\.symvers$|^Image$|\.dtb$|\.dtbo$|\.prebuilt$|\.prev$|overlays\.prebuilt|overlays\.prev|\.prebuilt/|\.prev/'
EXCLUDE_DIR_RE='^(out|prebuilts)$'

# --- Colors -----------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

# --- Helpers ----------------------------------------------------------------
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }
log_step()  { echo -e "\n${BLUE}========== $1 ==========${NC}"; }

# 临时文件清理（trap 兜底，防止中断时 /tmp 残留）
TMP_FILES=()
_cleanup() { [ ${#TMP_FILES[@]} -gt 0 ] && rm -f "${TMP_FILES[@]}" 2>/dev/null || true; }
trap _cleanup EXIT INT TERM

# find_upstream_base — 复用自 sync_code_to_patchs.sh（原样，保证回退的 upstream
# 与 sync 生成 diff 时的 upstream 是同一个 commit）
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

# diff_normalized — 比较两个 diff 文件语义是否一致（忽略 index 行 hash）
diff_normalized() {
    local d1="$1" d2="$2"
    diff <(grep -vE '^index ' "$d1" 2>/dev/null) <(grep -vE '^index ' "$d2" 2>/dev/null) >/dev/null 2>&1
}

# _is_excluded — 判断文件是否命中排除规则（编译产物 / 构建缓存）
_is_excluded() {
    local f="$1"
    echo "$f" | grep -qE "$EXCLUDE_RE" && return 0
    local bn="${f%%/*}"
    [[ "$bn" =~ $EXCLUDE_DIR_RE ]] && return 0
    return 1
}

# _parse_proj — 把 plan 中的"项目"字段解析为 workspace 绝对路径
# 返回值通过 stdout（调用者用 $() 捕获）
_parse_proj() {
    local proj="$1"
    case "$proj" in
        kernel) echo "$KERNEL_WS" ;;
        aosp)   echo "$AOSP_WS" ;;
        aosp:*) echo "$AOSP_WS/${proj#aosp:}" ;;
        *)      echo "" ;;
    esac
}

# --- 参数解析 ---------------------------------------------------------------
MODE="plan"
PLAN_FILE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)         MODE="apply"; shift ;;
        --check-only)    MODE="check-only"; shift ;;
        --plan-file)     [ $# -lt 2 ] && { log_error "--plan-file 需要一个路径参数"; exit 1; }; PLAN_FILE="$2"; shift 2 ;;
        --plan-file=*)   PLAN_FILE="${1#*=}"; shift ;;
        -h|--help)
            cat <<'USAGE'
Usage: bash revert_code_from_patchs.sh [--plan-file <path>] [--apply] [--check-only]
  无参数 / --plan-file X    生成回退计划（默认 /tmp/revert-plan-<ts>.tsv）
  --apply --plan-file X     执行回退计划（只执行标记 + 的条目）
  --check-only              仅扫描预览，不生成 plan 文件
USAGE
            exit 0 ;;
        *) log_error "未知参数: $1"; exit 1 ;;
    esac
done

if [ "$MODE" = "apply" ] && [ -z "$PLAN_FILE" ]; then
    log_error "--apply 模式必须配合 --plan-file <path>"
    exit 1
fi

# ============================================================================
# 前置检查
# ============================================================================
log_step "前置检查"

KERNEL_OK=false
AOSP_OK=false
[ -d "$KERNEL_WS/.git" ] && KERNEL_OK=true && log_info "Kernel workspace: $KERNEL_WS"
[ -d "$AOSP_WS/.repo"  ] && AOSP_OK=true   && log_info "AOSP workspace:   $AOSP_WS"

if [ "$KERNEL_OK" = false ] && [ "$AOSP_OK" = false ]; then
    log_error "未找到有效的 workspace（检查 KERNEL_WS/AOSP_WS 环境变量）"
    exit 1
fi
log_info "Patch root: $PATCH_ROOT"
if [ ! -d "$PATCH_ROOT/kernel" ] && [ ! -d "$PATCH_ROOT/aosp" ]; then
    log_error "patchs/rpi5 为空，无基线可回退"
    exit 1
fi
log_info "模式: $MODE"

# ============================================================================
# patchs 覆盖集合构建
# ============================================================================

# coverage_kernel — 输出 patchs 覆盖的 kernel 文件列表（相对 KERNEL_WS）
coverage_kernel() {
    [ -d "$PATCH_ROOT/kernel/modified" ] && find "$PATCH_ROOT/kernel/modified" -name '*.diff' 2>/dev/null | \
        sed "s|$PATCH_ROOT/kernel/modified/||;s|\.diff$||"
    [ -d "$PATCH_ROOT/kernel/new" ] && find "$PATCH_ROOT/kernel/new" -type f 2>/dev/null | \
        sed "s|$PATCH_ROOT/kernel/new/||"
}

# coverage_aosp_project — 输出指定 repo 项目的 patchs 覆盖文件（相对项目根）
coverage_aosp_project() {
    local proj="$1"
    [ -d "$PATCH_ROOT/aosp/modified/$proj" ] && find "$PATCH_ROOT/aosp/modified/$proj" -name '*.diff' 2>/dev/null | \
        sed "s|$PATCH_ROOT/aosp/modified/$proj/||;s|\.diff$||"
    [ -d "$PATCH_ROOT/aosp/new/$proj" ] && find "$PATCH_ROOT/aosp/new/$proj" -type f 2>/dev/null | \
        sed "s|$PATCH_ROOT/aosp/new/$proj/||"
}

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
    local BASE; BASE=$(find_upstream_base)
    [ -z "$BASE" ] && { log_error "kernel 无法确定 upstream base"; return 1; }

    while IFS= read -r -d '' dfile; do
        local rel="${dfile#$PATCH_ROOT/kernel/modified/}"; rel="${rel%.diff}"
        local tmp; tmp=$(mktemp); TMP_FILES+=("$tmp")
        git diff "$BASE" -- "$rel" > "$tmp" 2>/dev/null
        if [ ! -s "$tmp" ]; then
            # workspace 已恢复 upstream，但 patchs 有定制 → DIVERGED（缺失定制）
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "kernel" "$rel" "checkout" "workspace 已恢复 upstream，缺失 patchs 定制" >> "$out"
        elif diff_normalized "$tmp" "$dfile"; then
            G_MATCH_MODIFIED=$((G_MATCH_MODIFIED + 1))
        else
            printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "kernel" "$rel" "checkout" "workspace diff 与 patchs 不一致" >> "$out"
        fi
        rm -f "$tmp"
    done < <(find "$PATCH_ROOT/kernel/modified" -name '*.diff' -print0 2>/dev/null)
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
    done < <(find "$PATCH_ROOT/kernel/new" -type f -print0 2>/dev/null)
}

# scan_extra_kernel — workspace 改动 − patchs 覆盖集合 = EXTRA
scan_extra_kernel() {
    local out="$1"
    cd "$KERNEL_WS" || return 1
    local BASE; BASE=$(find_upstream_base)
    [ -z "$BASE" ] && return
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
        local BASE; BASE=$(find_upstream_base)
        [ -z "$BASE" ] && { log_warn "$proj 无 upstream base，跳过 modified 扫描"; continue; }

        while IFS= read -r -d '' dfile; do
            local rel="${dfile#$PATCH_ROOT/aosp/modified/$proj/}"; rel="${rel%.diff}"
            local tmp; tmp=$(mktemp); TMP_FILES+=("$tmp")
            git diff "$BASE" -- "$rel" > "$tmp" 2>/dev/null
            if [ ! -s "$tmp" ]; then
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "aosp:$proj" "$rel" "checkout" "workspace 已恢复 upstream，缺失 patchs 定制" >> "$out"
            elif diff_normalized "$tmp" "$dfile"; then
                G_MATCH_MODIFIED=$((G_MATCH_MODIFIED + 1))
            else
                printf '%s\t%s\t%s\t%s\t%s\t%s\n' "+" "MODIFIED-DIVERGED" "aosp:$proj" "$rel" "checkout" "workspace diff 与 patchs 不一致" >> "$out"
            fi
            rm -f "$tmp"
        done < <(find "$PATCH_ROOT/aosp/modified/$proj" -name '*.diff' -print0 2>/dev/null)
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
        done < <(find "$PATCH_ROOT/aosp/new/$proj" -type f -print0 2>/dev/null)
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
        local BASE; BASE=$(find_upstream_base)
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
        [[ "$bn" =~ $EXCLUDE_DIR_RE ]] && continue
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
# gen_plan — 阶段1：扫描 + 生成 plan 文件
# ============================================================================

# gen_plan — 生成完整 plan（含 log 输出，用于阶段 1）
gen_plan() {
    local out="$1"
    G_MATCH_MODIFIED=0
    G_MATCH_NEW=0

    {
        echo "# REVERT-PLAN generated at $(date +%FT%T)"
        echo "# 格式: <标记>\t<类别>\t<项目>\t<相对路径>\t<动作>\t<差异摘要>"
        echo "# 标记: + = 选中执行, - = 不执行"
        echo "# 类别: MODIFIED-DIVERGED | NEW-MISMATCH | EXTRA-MODIFIED | EXTRA-NEW-TRACKED | EXTRA-NEW-UNTRACKED"
        echo "# 动作: checkout | checkout-only | restore | revert | skip | stash-hint"
        echo ""
    } > "$out"

    log_step "扫描 kernel"
    if [ "$KERNEL_OK" = true ]; then
        scan_kernel_modified "$out"
        scan_kernel_new "$out"
        scan_extra_kernel "$out"
    fi

    log_step "扫描 aosp"
    if [ "$AOSP_OK" = true ]; then
        scan_aosp_modified "$out"
        scan_aosp_new "$out"
        scan_extra_aosp "$out"
    fi

    local total; total=$(grep -c '^[-+]' "$out" 2>/dev/null || true); total=${total:-0}
    log_step "扫描完成"
    log_info "MODIFIED-MATCH: $G_MATCH_MODIFIED 个文件已是 patchs 状态（不列入 plan）"
    log_info "NEW-MATCH: $G_MATCH_NEW 个文件已是 patchs 状态（不列入 plan）"
    log_info "需确认条目: $total 个（详见 plan 文件）"
    log_info "Plan 文件: $out"
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
    local BASE; BASE=$(find_upstream_base)
    [ -z "$BASE" ] && { log_error "$proj 无法确定 upstream base"; return 1; }
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
    local BASE; BASE=$(find_upstream_base)
    [ -z "$BASE" ] && { log_error "$proj 无法确定 upstream base"; return 1; }
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
# EXTRA-MODIFIED / EXTRA-NEW-TRACKED → git checkout upstream
# EXTRA-NEW-UNTRACKED → rm
do_revert_extra() {
    local proj="$1" rel="$2" category="$3"
    local ws; ws=$(_parse_proj "$proj")
    case "$category" in
        EXTRA-MODIFIED|EXTRA-NEW-TRACKED)
            cd "$ws" || return 1
            local BASE; BASE=$(find_upstream_base)
            [ -z "$BASE" ] && { log_error "$proj 无法确定 upstream base"; return 1; }
            git checkout "$BASE" -- "$rel" 2>/dev/null || { log_error "checkout 失败: $rel"; return 1; }
            ;;
        EXTRA-NEW-UNTRACKED)
            rm -f "$ws/$rel" || { log_error "rm 失败: $ws/$rel"; return 1; }
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

    log_step "执行回退计划 ($selected 条)"
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

        case "$action" in
            checkout)
                log_info "  [CHECKOUT] $proj:$rel"
                if do_checkout_patch "$proj" "$rel"; then
                    applied=$((applied + 1))
                else
                    log_error "  [CHECKOUT] $proj:$rel 失败，停止执行"
                    return 1
                fi
                ;;
            checkout-only)
                log_info "  [CHECKOUT-ONLY] $proj:$rel"
                if do_checkout_only "$proj" "$rel"; then
                    applied=$((applied + 1))
                else
                    return 1
                fi
                ;;
            restore)
                log_info "  [RESTORE] $proj:$rel"
                if do_restore "$proj" "$rel"; then
                    applied=$((applied + 1))
                else
                    return 1
                fi
                ;;
            revert)
                log_info "  [REVERT] $category $proj:$rel"
                if do_revert_extra "$proj" "$rel" "$category"; then
                    applied=$((applied + 1))
                else
                    return 1
                fi
                ;;
            skip|stash-hint)
                ;;  # 跳过
            *)
                log_warn "  未知动作 '$action'，跳过: $proj:$rel"
                ;;
        esac
    done < "$plan"

    log_step "执行完成"
    log_info "已执行: $applied 条"
    return 0
}

# ============================================================================
# verify_after_apply — 阶段3：全量重跑落盘校验
# ============================================================================

verify_after_apply() {
    local orig_plan="$1"
    local verify_out="/tmp/revert-verify-$(date +%Y%m%d%H%M%S).tsv"

    log_step "落盘校验（全量重跑）"

    # 生成新的扫描结果（静默）
    local new_plan; new_plan=$(mktemp); TMP_FILES+=("$new_plan")
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

    rm -f "$new_plan"

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
        [ -z "$PLAN_FILE" ] && PLAN_FILE="/tmp/revert-plan-$(date +%Y%m%d%H%M%S).tsv"
        gen_plan "$PLAN_FILE"
        ;;
    apply)
        if apply_plan "$PLAN_FILE"; then
            verify_after_apply "$PLAN_FILE"
            exit $?
        else
            log_error "apply 失败，跳过校验"
            exit 1
        fi
        ;;
    check-only)
        PLAN_FILE=$(mktemp /tmp/revert-preview.XXXXXX); TMP_FILES+=("$PLAN_FILE")
        gen_plan "$PLAN_FILE"
        echo ""
        log_step "差异预览"
        cat "$PLAN_FILE"
        rm -f "$PLAN_FILE"
        ;;
esac
```

- [ ] **Step 2: 添加可执行权限**

```bash
chmod +x engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh
```

- [ ] **Step 3: 语法检查**

Run: `bash -n engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`
Expected: 无输出（退出码 0），表示语法无误

- [ ] **Step 4: shellcheck 静态分析（如可用）**

Run: `shellcheck engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh 2>/dev/null || true`
Expected: 无 error 级别问题（warning/info 可接受，常见的是 `SC2086` 双引号建议，进程替换和 `<(...)` 可忽略）

- [ ] **Step 5: --help 输出验证**

Run: `bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --help`
Expected: 输出三行 Usage 说明，退出码 0

- [ ] **Step 6: Commit**

```bash
git add engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh
git commit -m "新增(workflows): revert-code-from-patchs 回退脚本

- 实现 plan/apply/verify 三阶段：扫描生成 TSV 计划 → 执行选中条目 → 全量重跑校验
- 五类分类：MODIFIED-MATCH/NEW-MATCH 仅汇总，DIVERGED/NEW-MISMATCH/EXTRA 逐条列入 plan
- 复用 sync-code-to-patchs 的 find_upstream_base/排除规则/repo 扫描逻辑
- 落盘校验分 FIXED/KEPT/RESIDUAL/NEW-DIFF 四类，有 RESIDUAL/NEW-DIFF 则退出码非 0"
```

---

## Task 2: 创建 WORKFLOW.md

**Files:**
- Create: `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`

- [ ] **Step 1: 写入完整 WORKFLOW.md**

用 Write 工具创建 `engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md`，完整内容如下：

```markdown
---
name: revert-code-from-patchs
description: patchs/rpi5 为基线，把 workspace 偏离部分拉回一致（计划生成 → AI 逐条确认 → 执行 → 落盘校验）。
---

# revert-code-from-patchs

将 `~/workspace/` 中偏离 `patchs/rpi5/` 基线的部分**拉回一致**，用于在 workspace 改坏后回到上次归档的可工作状态。

**核心语义**：`patchs/rpi5/` 是 workspace 定制改动的**已知良好基线**（真相源）。本工作流是 `sync-code-to-patchs` 的逆操作：sync 是 workspace→patchs 归档，revert 是 patchs→workspace 回退。

> **与 source-code-modify.md 的关系**：本工作流是该规则"workspace 是源头"原则的**受控例外**——当 workspace 处于不可用的坏状态时，允许反向把 patchs 状态写回 workspace。不改变日常归档流程，仅作灾难恢复。

## 前置约束

1. 操作对象仅限 `~/workspace/`（kernel + aosp），**不动 `patchs/`**
2. 执行前建议 `git stash`/commit 保存当前坏状态现场（脚本不自动备份，便于事后定位根因）
3. **不自动 `git add`/`git commit`**：执行后 working tree 处于回退后状态，由用户决定是否提交（便于 `git diff` 复查）

## 工作流（6 步闭环）

### 1. 生成回退计划（脚本）

```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh              # 生成 plan 到 /tmp
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --plan-file X # 指定 plan 路径
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --check-only  # 仅预览，不生成 plan
```

脚本扫描 workspace 与 patchs 差异，输出五类分类：

| 类别 | 含义 | 是否列入 plan |
|------|------|--------------|
| `MODIFIED-MATCH` | patchs 有 modified，workspace 当前 = patchs | ❌ 仅汇总 |
| `NEW-MATCH` | patchs 有 new，workspace 存在且内容 = patchs | ❌ 仅汇总 |
| `MODIFIED-DIVERGED` | patchs 有 modified，workspace 当前 ≠ patchs | ✅ 逐条 |
| `NEW-MISMATCH` | patchs 有 new，workspace 缺失或内容 ≠ patchs | ✅ 逐条 |
| `EXTRA` | workspace 有改动但 patchs 未覆盖（坏改动/调试代码） | ✅ 逐条 |

### 2. AI 主持逐条确认

AI 读取生成的 plan 文件（TSV），按类别分组呈现给用户：

- **MODIFIED-DIVERGED**：列出文件 + 差异摘要，默认动作 `checkout`（拉回 patchs）
- **NEW-MISMATCH**：列出文件 + 缺失/不一致状态，默认动作 `restore`
- **EXTRA**：列出文件 + 来源描述，默认动作 `revert`

用户逐条/逐类指示：
- "这条 skip" / "这组全选" / "这条改成 checkout-only"
- AI **直接编辑 plan 文件**的 `+`（执行）/ `-`（跳过）标记和动作字段

### 3. 最终确认 + 执行

AI 展示选中条目汇总（各类数量 + 动作分布），等用户最终 `y` 确认后执行：

```bash
bash .../revert_code_from_patchs.sh --apply --plan-file /tmp/revert-plan-xxx.tsv
```

脚本行为：
- 只执行 `+` 标记的条目
- 每条执行前打印动作（`[CHECKOUT] kernel:drivers/...`）
- **失败立即停止**（退出码非 0），避免半完成状态

### 4. 落盘校验（强制，全量）

apply 完成后脚本自动重跑全量扫描，与原 plan 对比，分 4 类输出：

| 标记 | 含义 | 是否算失败 |
|------|------|-----------|
| ✅ `FIXED` | 原 `+` 执行的条目现已是 MATCH | 否（成功） |
| ⚠ `KEPT` | 原 `-` skip 的条目仍偏离 | 否（用户主动保留） |
| ❌ `RESIDUAL` | 原 `+` 执行的条目仍偏离 | **是（回退未生效）** |
| ❓ `NEW-DIFF` | apply 后新出现的差异 | **是（需排查）** |

落盘文件：`/tmp/revert-verify-<timestamp>.tsv`
退出码：有 `RESIDUAL` 或 `NEW-DIFF` → 非 0（失败）；仅 `KEPT` → 0（成功）

### 5. 执行结果报告

AI 汇报各类执行数量 + 校验结果。若校验失败，列出 RESIDUAL/NEW-DIFF 条目供排查。

### 6. 后续（不自动）

- 提示用户 `git diff` 复查回退后的 working tree
- 由用户决定是否编译验证（`make bootimage` 等）
- **不自动 git commit**——是否提交由用户决定

## 动作矩阵

| 动作 | 适用类别 | 语义 | 具体 git 操作 |
|------|---------|------|-------------|
| `checkout` | MODIFIED-DIVERGED | 拉回 patchs | `git checkout $BASE -- $f && git apply patchs.diff` |
| `checkout-only` | MODIFIED-DIVERGED | 移除定制 | `git checkout $BASE -- $f` |
| `restore` | NEW-MISMATCH | 从 patchs 补回 | `cp patchs/new/... workspace` |
| `revert` | EXTRA-MODIFIED / EXTRA-NEW-TRACKED | 恢复 upstream | `git checkout $BASE -- $f` |
| `revert` | EXTRA-NEW-UNTRACKED | 删除 | `rm -f $f` |
| `skip` | 任意 | 不动 | — |
| `stash-hint` | EXTRA | 提示用户手动 stash | —（不执行） |

## plan 文件格式（TSV）

```
# REVERT-PLAN generated at <时间戳>
# 格式: <标记>\t<类别>\t<项目>\t<相对路径>\t<动作>\t<差异摘要>

+	MODIFIED-DIVERGED	kernel	drivers/usb/storage/transport.c	checkout	workspace diff 与 patchs 不一致
-	MODIFIED-DIVERGED	aosp:device/brcm/rpi5	device.mk	skip	仅注释差异，保留现状
+	NEW-MISMATCH	kernel	vendor/lechao/LcView/lcview_main.c	restore	workspace 缺失
+	EXTRA-MODIFIED	kernel	drivers/input/mouse/elog.c	revert	未归档的 upstream 文件改动
+	EXTRA-NEW-UNTRACKED	aosp:vendor/lechao	vendor/lechao/debug_temp.c	revert	非 repo 目录未归档文件
```

- **标记**：`+` 执行 / `-` 跳过（AI 确认时编辑）
- **项目**：`kernel` / `aosp` / `aosp:<repo_project>`（精确到 repo 才能正确 checkout）
- **相对路径**：相对 workspace 项目根

## 异常处理

| 场景 | 处理 |
|------|------|
| workspace 不存在 | 报错退出 |
| 无法确定 upstream base | 报错退出，提示 `git remote -v` 检查 |
| patchs 为空 | 报错退出 |
| `.diff` 损坏（`git apply --check` 失败） | 标记 BROKEN-DIFF，该条 return 1 停止执行 |
| apply 失败 | **立即停止**，退出码非 0 |
| EXTRA 命中编译产物 | 不列入 EXTRA（排除规则） |
| workspace 有 staged 改动 | 仅警告不阻断 |
| 校验有 RESIDUAL/NEW-DIFF | 退出码非 0 |

## 不做的事（YAGNI）

- 不自动 `git add`/`git commit`
- 不处理 `patchs/others/`（仅 kernel/aosp）
- 不做反向 patch（用 `git checkout` 更可靠）
- 不做多平台（仅 rpi5，未来扩展另立）
```

- [ ] **Step 2: Commit**

```bash
git add engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md
git commit -m "新增(workflows): revert-code-from-patchs WORKFLOW 文档"
```

---

## Task 3: 创建命令入口

**Files:**
- Create: `.opencode/commands/revert-code-from-patchs.md`

- [ ] **Step 1: 写入命令入口**

用 Write 工具创建 `.opencode/commands/revert-code-from-patchs.md`，内容与现有命令（`sync-code-to-patchs.md`）风格完全对齐：

```markdown
---
description: 生成回退计划，AI 主持逐条确认后把 workspace 拉回与 patchs/rpi5 一致
---
生成回退计划（参数透传）：
!`bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh $ARGUMENTS`

严格遵循完整工作流（计划生成 → AI 主持逐条确认 → 执行 → 落盘校验）：
@engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md
```

- [ ] **Step 2: Commit**

```bash
git add .opencode/commands/revert-code-from-patchs.md
git commit -m "新增(workflows): revert-code-from-patchs 命令入口"
```

---

## Task 4: 静态检查 + 语法验证

**Files:**
- 无文件改动，仅验证

- [ ] **Step 1: 验证文件结构完整**

Run:
```bash
test -f engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh && \
test -f engineering/harness/workflows/revert-code-from-patchs/WORKFLOW.md && \
test -f .opencode/commands/revert-code-from-patchs.md && \
echo "ALL FILES OK"
```
Expected: `ALL FILES OK`

- [ ] **Step 2: bash 语法检查**

Run: `bash -n engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh`
Expected: 无输出（退出码 0）

- [ ] **Step 3: --help 输出**

Run: `bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --help`
Expected: 输出三行 Usage，退出码 0

- [ ] **Step 4: 未知参数报错**

Run: `bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --bogus 2>&1; echo "EXIT=$?"`
Expected: `[ERROR] 未知参数: --bogus`，`EXIT=1`

- [ ] **Step 5: --apply 缺少 --plan-file 报错**

Run: `bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --apply 2>&1; echo "EXIT=$?"`
Expected: `[ERROR] --apply 模式必须配合 --plan-file <path>`，`EXIT=1`

- [ ] **Step 6: 命令入口格式校验**

Run:
```bash
head -1 .opencode/commands/revert-code-from-patchs.md | grep -q '^---' && \
grep -q 'revert_code_from_patchs.sh' .opencode/commands/revert-code-from-patchs.md && \
echo "COMMAND OK"
```
Expected: `COMMAND OK`

---

## Task 5: 集成自测（真实 workspace）

> **前提**：在真实 workspace（`~/workspace/rpi5-kernel-build/common` + `~/workspace/aosp`）上运行。本任务全部使用 `--check-only`（只读，不修改 workspace）。

- [ ] **Step 1: --check-only 扫描当前差异**

Run:
```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --check-only 2>&1 | tail -20
```
Expected: 输出 `前置检查` → `扫描 kernel` → `扫描 aosp` → `扫描完成` → `差异预览`（TSV 条目列表），无报错

- [ ] **Step 2: --plan-file 生成 plan 文件**

Run:
```bash
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --plan-file /tmp/revert-test.tsv 2>&1 | tail -10
echo "---"
head -10 /tmp/revert-test.tsv
```
Expected:
- 终端输出 `扫描完成` + `Plan 文件: /tmp/revert-test.tsv`
- 文件首 6 行为 `#` 注释（header），后续为 TSV 数据行
- `#` 行不计入数据；数据行首列为 `+` 或 `-`

- [ ] **Step 3: plan 文件格式校验**

Run:
```bash
# 数据行（非注释非空）应为 6 个 tab 分隔字段
awk -F'\t' '/^[+-]/ && NF!=6 {print "BAD LINE ("NF" fields): "$0}' /tmp/revert-test.tsv
echo "FORMAT CHECK DONE (无 BAD LINE 即通过)"
```
Expected: 无 `BAD LINE` 输出

- [ ] **Step 4: plan 文件类别校验**

Run:
```bash
echo "=== 类别分布 ==="
awk -F'\t' '/^[+-]/ {print $2}' /tmp/revert-test.tsv | sort | uniq -c
echo "=== 动作分布 ==="
awk -F'\t' '/^[+-]/ {print $5}' /tmp/revert-test.tsv | sort | uniq -c
```
Expected: 类别仅出现在白名单内：`MODIFIED-DIVERGED | NEW-MISMATCH | EXTRA-MODIFIED | EXTRA-NEW-TRACKED | EXTRA-NEW-UNTRACKED`；动作仅出现在白名单内：`checkout | checkout-only | restore | revert | skip | stash-hint`

- [ ] **Step 5: 项目字段格式校验**

Run:
```bash
awk -F'\t' '/^[+-]/ {print $3}' /tmp/revert-test.tsv | sort -u
```
Expected: 项目字段格式为 `kernel` / `aosp` / `aosp:<repo_project>`（如 `aosp:device/brcm/rpi5`），无空值、无非法格式

- [ ] **Step 6: 幂等性验证**

Run:
```bash
# 生成两次 plan，比较数据行是否一致（排除时间戳）
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh --plan-file /tmp/revert-test-2.tsv >/dev/null 2>&1
diff <(grep '^[+-]' /tmp/revert-test.tsv) <(grep '^[+-]' /tmp/revert-test-2.tsv)
echo "IDEMPOTENT CHECK EXIT=$?"
```
Expected: `diff` 无输出（两次扫描结果一致），`EXIT=0`

- [ ] **Step 7: 清理测试文件**

```bash
rm -f /tmp/revert-test.tsv /tmp/revert-test-2.tsv
```

- [ ] **Step 8: 最终提交（spec + plan 文档）**

如果 spec 和 plan 文档尚未提交，现在一起提交：

```bash
git add docs/specs/2026-06-19-revert-code-from-patchs-design.md docs/plans/2026-06-19-revert-code-from-patchs.md
git commit -m "文档(specs,plans): revert-code-from-patchs 设计规格与实施计划"
```

---

## 自检

### 1. Spec 覆盖检查

| Spec 章节 | 覆盖 Task | 状态 |
|-----------|----------|------|
| §1 背景与动机 | （文档背景，无需代码） | ✓ |
| §2 对仗关系 | （文档背景，无需代码） | ✓ |
| §3 核心语义与前置约束 | Task 2 WORKFLOW.md 前置约束章节 | ✓ |
| §3 不自动 git add/commit | Task 1 主流程（无 git add/commit 调用） | ✓ |
| §4 五类分类 + 动作矩阵 | Task 1 scan_* + do_* + Task 2 动作矩阵表 | ✓ |
| §4 EXTRA 细分 revert 映射 | Task 1 do_revert_extra（MODIFIED/TRACKED→checkout，UNTRACKED→rm） | ✓ |
| §5.1 plan/apply 两阶段 | Task 1 MODE 分发 + apply_plan | ✓ |
| §5.2 TSV 格式 | Task 1 gen_plan header + printf 格式 | ✓ |
| §5.3 两阶段命令 | Task 1 参数解析 + 主流程 case | ✓ |
| §5.4 参数清单 | Task 1 参数解析（--apply/--check-only/--plan-file/-h） | ✓ |
| §5.5 AI 主持确认流程 | Task 2 WORKFLOW.md 步骤 2-3 | ✓ |
| §5.5 落盘校验 4 分类 | Task 1 verify_after_apply + Task 2 步骤 4 | ✓ |
| §6 异常矩阵（12 项） | Task 1 各函数错误处理 + Task 2 异常处理表 | ✓ |
| §6 幂等性 | Task 1 do_* 幂等设计 + Task 5 Step 6 幂等验证 | ✓ |
| §6 安全兜底 | Task 1 apply 前警告 + Task 2 WORKFLOW.md | ✓ |
| §7 脚本结构与复用 | Task 1 完整脚本（find_upstream_base 等复用） | ✓ |
| §7.4 关键实现细节 | Task 1 diff_normalized/coverage_*/scan_extra/do_checkout_patch | ✓ |
| §8 命令入口 + WORKFLOW.md | Task 2 + Task 3 | ✓ |
| §9 YAGNI | Task 2 WORKFLOW.md 不做的事章节 | ✓ |
| §10 验收标准（8 条） | Task 4-5 验证 | ✓ |

**验收标准逐条对应**：

| 验收标准 | 验证方式 |
|---------|---------|
| 1. 扫描生成 plan | Task 5 Step 1-2 |
| 2. 五类分类正确 | Task 5 Step 3-4 |
| 3. --apply 执行 + 条目 | Task 1 apply_plan + Task 5 Step 2（真实环境确认有条目） |
| 4. 异常场景处理 | Task 4 Step 4-5（参数异常）+ Task 1 代码内 BROKEN-DIFF/staged 处理 |
| 5. 幂等性 | Task 5 Step 6 |
| 6. --check-only 预览 | Task 5 Step 1 |
| 7. 命令入口可用 | Task 3 + Task 4 Step 6 |
| 8. 落盘校验 4 分类 | Task 1 verify_after_apply + Task 2 步骤 4 表 |

无遗漏。

### 2. Placeholder 扫描

无 TBD/TODO/FIXME/"implement later"。所有代码块均为可直接执行的完整脚本内容。Task 1 的脚本代码是完整的、自包含的，执行 agent 可直接 Write 到文件。

### 3. 类型/命名一致性

- `revert_code_from_patchs.sh` — 全文一致（下划线命名，与 `sync_code_to_patchs.sh` 对仗）
- `revert-code-from-patchs` — 全文一致（连字符命名，用于目录名/命令名）
- 函数命名一致：`gen_plan` / `gen_plan_silent` / `apply_plan` / `verify_after_apply` / `do_checkout_patch` / `do_checkout_only` / `do_restore` / `do_revert_extra` / `scan_kernel_modified` / `scan_kernel_new` / `scan_extra_kernel` / `scan_aosp_modified` / `scan_aosp_new` / `scan_extra_aosp` / `scan_extra_aosp_non_repo` / `coverage_kernel` / `coverage_aosp_project` / `find_upstream_base` / `diff_normalized` / `_is_excluded` / `_parse_proj`
- 全局变量一致：`G_MATCH_MODIFIED` / `G_MATCH_NEW` / `MODE` / `PLAN_FILE` / `KERNEL_OK` / `AOSP_OK`
- 动作白名单一致：`checkout | checkout-only | restore | revert | skip | stash-hint`
- 类别白名单一致：`MODIFIED-DIVERGED | NEW-MISMATCH | EXTRA-MODIFIED | EXTRA-NEW-TRACKED | EXTRA-NEW-UNTRACKED`
- 校验标记一致：`FIXED | KEPT | RESIDUAL | NEW-DIFF`
- plan 文件 TSV 字段顺序一致：`标记 → 类别 → 项目 → 相对路径 → 动作 → 差异摘要`（6 字段）
- `verify_after_apply` 用 `cut -f3,4`（项目+相对路径）作为 key，与 plan 字段顺序对应（第 3、4 列）

无矛盾。

### 4. 潜在问题排查（含子 agent 深度审查 + 修复）

> 以下问题由独立子 agent 对脚本代码做逐行审查发现，均已修复。

| 问题 | 级别 | 修复 |
|------|------|------|
| `--plan-file` 无后续参数时 `shift 2` 失败 → 死循环 | 🔴 | 加 `$# -lt 2` 校验 |
| `grep -c ... \|\| echo 0` 产生 `"0\n0"` 多行垃圾值 → 整数比较崩溃 | 🔴 | 改用 `\|\| true; var=${var:-0}` |
| `do_checkout_patch` 先 checkout 后 check，BROKEN-DIFF 时 workspace 被半修改（定制丢失） | 🟡 | 交换顺序：先 `git apply --check` 再 `git checkout` |
| 无 `trap` 清理临时文件，中断时 `/tmp` 残留 | 🟡 | 添加 `TMP_FILES` 数组 + `trap _cleanup EXIT INT TERM` |

### 5. 其他排查确认（子 agent 审查通过项）

| 排查项 | 结论 |
|--------|------|
| `comm` 输入需排序 | `coverage_*`、`scan_extra_*` 均对集合做 `sort -u`，`verify` 对 key 做 `sort -u`。✓ |
| 子 shell 变量隔离 | scan 函数直接在当前 shell 执行（非 `()`），`G_MATCH_*` 全局计数正常。✓ |
| `cd` 副作用 | scan 函数各自 `cd` 到正确 workspace；`gen_plan` 串行调用各 scan；脚本执行完即退出。✓ |
| 非 repo 目录无 git | `do_revert_extra` 对 `EXTRA-NEW-UNTRACKED` 走 `rm`，不调 `find_upstream_base`。✓ |
| `<<< "$var"` 空输入 | `var` 为空时传入换行符，while 循环首行 `[ -z "$f" ] && continue` 跳过。✓ |
| AOSP project 名含 `/` | `project.list` 每行是完整项目路径，`find "$PATCH_ROOT/.../$proj"` 正确匹配。✓ |
| `find -print0` + `read -d ''` | 处理含空格文件名。✓ |
| `git apply --check` 需在 repo 根 | `do_checkout_patch` 先 `cd "$ws"`（repo 根），再 `git apply`。✓ |
| plan 无数据行时 `grep -c` 返回 1 | 已改用 `\|\| true; var=${var:-0}` 模式，返回空字符串后 `${var:-0}` 兜底为 0。✓ |
| `apply_plan` 逐行 `< "$plan"` 含 header | `[[ "$line" =~ ^[[:space:]]*# ]]` 跳过注释行。✓ |
| `IFS=$'\t' read` 解析 TSV | summary 是最后字段，吸收剩余内容（含空格）。✓ |
| `set -uo pipefail`（无 `-e`）管道失败 | 命令替换中的管道失败（`grep -v` 无匹配 rc=1）不触发退出，赋值正常完成。✓ |
| `sed` 分隔符与路径特殊字符 | `PATCH_ROOT` 实际路径不含 `\|`/`&`，当前环境无问题（如需加固可改用 `#` 分隔符）。✓ |
| `printf '%s'` 与参数中 `%` | `%s` 是格式占位符，参数中的 `%` 作为数据传入不被二次解释。✓ |
| 修复后脚本语法检查 | 提取 688 行脚本，`bash -n` 通过（SYNTAX OK）。✓ |

无阻塞性问题。
